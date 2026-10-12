"""Local web UI for mocap.py: drop a video, pick in/out, run the whole chain.

  tools\\venv\\Scripts\\python.exe ui\\server.py [--port 8765] [--no-browser]
  (or double-click Mocap.bat)

Listens on 127.0.0.1 only. One job at a time; it keeps running if the page is
closed, and the page picks it up again on reload.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import mocap  # noqa: E402

UI = Path(__file__).resolve().parent
UPLOADS = REPO / "plates" / "_uploads"
MEDIA_ROOTS = (REPO / "clips", REPO / "plates")
CHAR_EXT = (".glb", ".gltf", ".fbx")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
STEP_RE = re.compile(r"^@@STEP (\w+) (\w+)")
MEAN_RE = re.compile(r"mean ([\d.]+) deg")


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.reset()

    def reset(self):
        self.state = "idle"            # idle | running | done | error | cancelled
        self.name = self.clip = self.error = None
        self.steps = {k: "pending" for k, _ in mocap.STEPS}
        self.step_t: dict[str, list[float]] = {}
        self.log: deque[str] = deque(maxlen=600)
        self.progress = ""             # last \r-terminated line (tqdm bars)
        self.started = self.ended = None

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()

            def secs(k):
                t = self.step_t.get(k)
                return round((t[1] or now) - t[0], 1) if t else None

            return {
                "state": self.state, "name": self.name, "clip": self.clip, "error": self.error,
                "steps": [{"key": k, "title": t, "state": self.steps[k], "seconds": secs(k)}
                          for k, t in mocap.STEPS],
                "log": list(self.log), "progress": self.progress,
                "elapsed": round((self.ended or now) - self.started, 1) if self.started else 0,
            }


JOB = Job()


def start_job(cmd: list[str], name: str, clip: str) -> None:
    JOB.reset()
    JOB.state, JOB.name, JOB.clip, JOB.started = "running", name, clip, time.time()
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    JOB.proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    threading.Thread(target=pump, args=(JOB.proc,), daemon=True).start()


def handle_line(line: str) -> None:
    m = STEP_RE.match(line)
    if m:
        key, state = m.groups()
        state = {"start": "running"}.get(state, state)
        if key in JOB.steps:
            JOB.steps[key] = state
            if state == "running":
                JOB.step_t[key] = [time.time(), None]
            elif key in JOB.step_t:
                JOB.step_t[key][1] = time.time()
        JOB.progress = ""
        return
    if line.startswith("@@RESULT "):
        JOB.clip = json.loads(line[9:]).get("clip", JOB.clip)
        return
    if line.strip():
        JOB.log.append(line.rstrip())
        JOB.progress = ""


def pump(proc: subprocess.Popen) -> None:
    buf = b""
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        # "\r\n" and "\n" end a line; a lone "\r" ends a progress-bar redraw (tqdm).
        while True:
            n, r = buf.find(b"\n"), buf.find(b"\r")
            if r >= 0 and (n < 0 or r < n):
                if r == len(buf) - 1:
                    break                      # wait: a "\n" may follow
                is_line = buf[r + 1:r + 2] == b"\n"
                seg, buf = buf[:r], buf[r + (2 if is_line else 1):]
            elif n >= 0:
                is_line, seg, buf = True, buf[:n], buf[n + 1:]
            else:
                break
            text = seg.decode("utf-8", errors="replace")
            with JOB.lock:
                if is_line:
                    handle_line(text)
                elif text.strip():
                    JOB.progress = text.strip()[-160:]
    code = proc.wait()
    with JOB.lock:
        if buf.strip():
            handle_line(buf.decode("utf-8", errors="replace"))
        JOB.ended = time.time()
        JOB.progress = ""
        if JOB.state == "cancelled":
            pass
        elif code == 0:
            JOB.state = "done"
        else:
            JOB.state = "error"
            JOB.error = next((l for l in reversed(JOB.log) if l.startswith(("Échec", "Fichiers manquants",
                                                                            "Traceback", "SystemExit"))),
                             JOB.log[-1] if JOB.log else f"code {code}")
        for k, s in JOB.steps.items():
            if s == "running":
                JOB.steps[k] = "error" if JOB.state != "done" else "done"


def clip_info(d: Path) -> dict:
    """A clip folder: its .blend, the plate it came from (mocap.json, else guessed
    from the folder name <char>_<plate>) and any review videos (mocap.py --review)."""
    meta = {}
    if (d / "mocap.json").exists():
        try:
            meta = json.loads((d / "mocap.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    plate = meta.get("plate")
    if not plate:
        names = [p.name for p in (REPO / "plates").iterdir() if p.is_dir()] if (REPO / "plates").exists() else []
        plate = max((n for n in names if d.name.endswith("_" + n)), key=len, default=None)
    plate_video = REPO / "plates" / plate / f"{plate}.mp4" if plate else None
    order = {"review_front.mp4": 0, "review_three_quarter.mp4": 1}
    blend = d / f"{d.name}.blend"
    return {
        "clip": d.name, "blend": blend.exists(), "fbx": (d / f"{d.name}.fbx").exists(),
        "mtime": (blend if blend.exists() else d).stat().st_mtime,
        "plate": plate, "character": meta.get("character"),
        "start": meta.get("start"), "end": meta.get("end"), "duration": meta.get("duration"),
        "source": f"/media/plates/{plate}/{plate}.mp4" if plate_video and plate_video.exists() else None,
        "videos": sorted((p.name for p in d.glob("review_*.mp4")), key=lambda n: (order.get(n, 2), n)),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MocapUI/1"

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/api/status", "/media/")):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    # -- helpers --------------------------------------------------------------
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def send_file(self, path: Path, ctype: str | None = None):
        size = path.stat().st_size
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else end
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

    # -- routes ---------------------------------------------------------------
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.send_file(UI / "index.html", "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send_json(JOB.snapshot())
        if path == "/api/characters":
            chars = [{"file": p.name, "ready": (REPO / f"{mocap.sanitize(p.stem)}_rest.blend").exists()}
                     for p in sorted(REPO.iterdir()) if p.suffix.lower() in CHAR_EXT]
            return self.send_json(chars)
        if path == "/api/clips":
            d = REPO / "clips"
            clips = [clip_info(c) for c in d.iterdir() if c.is_dir()] if d.exists() else []
            clips = [c for c in clips if c["blend"]]
            return self.send_json(sorted(clips, key=lambda c: -c["mtime"]))
        if path.startswith("/api/clip/"):
            d = REPO / "clips" / path[len("/api/clip/"):]
            if d.is_dir() and d.parent == REPO / "clips":
                return self.send_json(clip_info(d))
            return self.send_json({"error": "clip inconnu"}, 404)
        if path.startswith("/media/"):
            target = (REPO / path[len("/media/"):]).resolve()
            if any(target.is_relative_to(r) for r in MEDIA_ROOTS) and target.is_file():
                return self.send_file(target)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/upload":
            return self.upload()
        if path == "/api/preview":
            return self.preview()
        if path == "/api/run":
            return self.run_job()
        if path == "/api/cancel":
            with JOB.lock:
                if JOB.state == "running" and JOB.proc:
                    JOB.state = "cancelled"
                    subprocess.run(["taskkill", "/PID", str(JOB.proc.pid), "/T", "/F"],
                                   capture_output=True)
            return self.send_json({"ok": True})
        if path == "/api/reset":
            with JOB.lock:
                if JOB.state != "running":
                    JOB.reset()
            return self.send_json({"ok": True})
        if path == "/api/open":
            return self.open_target()
        if path == "/api/export":
            return self.export_fbx()
        self.send_error(404)

    def upload(self):
        raw = unquote(self.headers.get("X-Filename") or "video.mp4")
        stem, ext = os.path.splitext(os.path.basename(raw))
        ext = ext.lower() if ext.lower() in VIDEO_EXT else ".mp4"
        UPLOADS.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS / f"{mocap.sanitize(stem)}{ext}"
        left = int(self.headers.get("Content-Length") or 0)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            tmp.unlink(missing_ok=True)
            return self.send_json({"error": "envoi interrompu"}, 400)
        tmp.replace(dest)
        try:
            info = mocap.probe(dest)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": f"vidéo illisible : {e}"}, 400)
        self.send_json({"upload": dest.name, "name": mocap.sanitize(stem), **info})

    def preview(self):
        """H.264 proxy for codecs the browser cannot play (HEVC from phones, ...)."""
        up = UPLOADS / os.path.basename(self.read_json().get("upload", ""))
        if not up.is_file():
            return self.send_json({"error": "fichier inconnu"}, 404)
        prev = up.with_name(up.stem + "_preview.mp4")
        if not prev.exists():
            res = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(up), "-vf", "scale=-2:'min(720,ih)'",
                                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                                  "-an", str(prev)], capture_output=True, text=True)
            if res.returncode != 0:
                return self.send_json({"error": res.stderr[-400:]}, 500)
        self.send_json({"url": f"/media/plates/_uploads/{prev.name}"})

    def run_job(self):
        req = self.read_json()
        with JOB.lock:
            if JOB.state == "running":
                return self.send_json({"error": "un traitement est déjà en cours"}, 409)
        up = UPLOADS / os.path.basename(req.get("upload", ""))
        if not up.is_file():
            return self.send_json({"error": "vidéo introuvable, la redéposer"}, 400)
        name = mocap.sanitize(req.get("name") or up.stem)
        character = os.path.basename(req.get("character") or "Benjamin.glb")
        cmd = [sys.executable, str(REPO / "mocap.py"), "--video", str(up), "--name", name,
               "--character", character]
        for k in ("start", "end"):
            if req.get(k) is not None:
                cmd += [f"--{k}", f"{float(req[k]):.3f}"]
        clip = f"{mocap.sanitize(Path(character).stem)}_{name}"
        start_job(cmd, name, clip)
        self.send_json({"ok": True, "clip": clip})

    def export_fbx(self):
        req = self.read_json()
        try:
            out = export_clip_fbx(req.get("clip", ""), bool(req.get("source_size", True)))
        except FileNotFoundError as e:
            return self.send_json({"error": str(e)}, 404)
        except RuntimeError as e:
            return self.send_json({"error": f"export échoué : {e}"}, 500)
        self.send_json({"ok": True, "fbx": out.name, "size": out.stat().st_size})

    def open_target(self):
        req = self.read_json()
        d = REPO / "clips" / os.path.basename(req.get("clip", ""))
        if not d.is_dir():
            return self.send_json({"error": "clip inconnu"}, 404)
        if req.get("what") == "blend":
            blend = d / f"{d.name}.blend"
            if not blend.exists():
                return self.send_json({"error": "pas de .blend"}, 404)
            # autoexec: the embedded export panel (N > Mocap) registers on load
            subprocess.Popen([mocap.blender_exe(None), "--enable-autoexec", str(blend)], cwd=REPO)
        else:
            subprocess.Popen(["explorer", str(d)])
        self.send_json({"ok": True})


class Server(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets a second server bind the same port silently;
    # an exclusive bind makes a second launch fail and reuse the first instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def export_clip_fbx(clip: str, source_size: bool = True) -> Path:
    """<clip>.fbx next to the .blend: skeleton + animation, no mesh (mocap_fbx_export.py)."""
    d = REPO / "clips" / os.path.basename(clip)
    blend = d / f"{d.name}.blend"
    if not blend.exists():
        raise FileNotFoundError("pas de .blend pour ce clip")
    cmd = [mocap.blender_exe(None), "-b", str(blend), "-P", str(REPO / "pipeline" / "mocap_fbx_export.py"),
           "--", "--export"] + ([] if source_size else ["--pipeline-size"])
    res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = d / f"{d.name}.fbx"
    if res.returncode != 0 or not out.exists():
        raise RuntimeError((res.stderr or res.stdout)[-600:])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Mocap UI tourne déjà : {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return
    print(f"Mocap UI : {url}  (fermer cette fenêtre pour arrêter)", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

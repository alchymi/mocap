"""Video -> animation on a character with a mixamorig: skeleton, end to end
(SMPL-X path: GVHMR -> retarget_smplx -> bl_motion; --review adds render_review + eval_fidelity).

  tools\\venv\\Scripts\\python.exe mocap.py --video C:\\...\\ma_video.mp4 [--start 2 --end 12] [--name x]
  tools\\venv\\Scripts\\python.exe mocap.py --name tennis                     # plate already imported
  tools\\venv\\Scripts\\python.exe mocap.py --video ... --character ybot.fbx

--video imports the clip into plates/<name>/<name>.mp4: trimmed to
--start/--end (seconds), at most 30 fps and 1920 px, constant frame rate.
Re-importing a name clears that plate's caches (GVHMR keys its own by file
name). The character (.glb/.gltf/.fbx) is converted once into
<char>_rest.blend + rig_profiles/<char>.json; the clip goes to
clips/<char>_<name>/ (the .blend, motion.npz and mocap.json: plate, character, in/out).

Progress lines for ui/server.py: "@@STEP <key> start|done|skip|error" and a
final "@@RESULT <json>".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
GVHMR_PY = REPO / "tools" / "GVHMR" / ".venv" / "Scripts" / "python.exe"
PY = REPO / "tools" / "venv" / "Scripts" / "python.exe"
SMPLX = REPO / "tools" / "GVHMR" / "inputs" / "checkpoints" / "body_models" / "smplx" / "SMPLX_NEUTRAL.npz"
BLENDER_CANDIDATES = [
    os.environ.get("BLENDER", ""),
    r"D:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
]
STEPS = [
    ("import", "Import de la vidéo"),
    ("rig", "Préparation du personnage"),
    ("estimate", "Estimation de la pose (GVHMR)"),
    ("retarget", "Retarget sur le personnage"),
    ("apply", "Animation dans Blender"),
]
REVIEW_STEPS = [
    ("review", "Vidéos de contrôle"),
    ("eval", "Mesures de fidélité"),
]
TITLES = dict(STEPS + REVIEW_STEPS)


def sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower()).strip("_") or "video"


def blender_exe(arg: str | None) -> str:
    for c in ([arg] if arg else []) + BLENDER_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise SystemExit("Blender 5.1+ introuvable — passer --blender ou définir BLENDER")


def probe(video: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=avg_frame_rate,width,height:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    num, _, den = s["avg_frame_rate"].partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"fps": fps, "width": s["width"], "height": s["height"],
            "duration": float(d.get("format", {}).get("duration", 0.0))}


def step(key: str, state: str, note: str = "") -> None:
    if state == "start":
        print(f"\n=== {TITLES[key]} ===", flush=True)
    elif note:
        print(note, flush=True)
    print(f"@@STEP {key} {state}", flush=True)


def run(cmd: list, key: str) -> None:
    """Run one stage; its output goes straight to ours (the UI reads it live)."""
    print("> " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), flush=True)
    # Piped (ui/server.py): no console windows for Blender & co.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and not sys.stdout.isatty() else 0
    sys.stdout.flush()
    res = subprocess.run([str(c) for c in cmd], cwd=REPO, creationflags=flags,
                         stdout=sys.stdout, stderr=sys.stderr)
    if res.returncode != 0:
        step(key, "error")
        raise SystemExit(f"Échec : {TITLES[key]} (code {res.returncode})")


def import_video(video: Path, name: str, start: float | None, end: float | None) -> None:
    plate = REPO / "plates" / name
    plate.mkdir(parents=True, exist_ok=True)
    for f in ("smplx.npz", "smplx_raw.npz", "feet2d.npz", "hands2d.npz", "landmarks.json"):
        (plate / f).unlink(missing_ok=True)
    shutil.rmtree(REPO / "tools" / "GVHMR" / "outputs" / "demo" / name, ignore_errors=True)

    info = probe(video)
    out_fps = 30 if info["fps"] > 31 else round(info["fps"], 3)
    vf = f"fps={out_fps},scale=w='if(gte(iw,ih),min(iw,1920),-2)':h='if(gte(iw,ih),-2,min(ih,1920))'"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(video), "-vf", vf, "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
            str(plate / f"{name}.mp4")]
    print(f"source {info['width']}x{info['height']} @ {info['fps']:.2f} fps -> {out_fps} fps", flush=True)
    run(cmd, "import")
    out = probe(plate / f"{name}.mp4")
    print(f"plate : plates/{name}/{name}.mp4  {out['width']}x{out['height']}, {out['duration']:.2f} s", flush=True)

    spec = REPO / "action_specs" / f"{name}.json"
    if not spec.exists():
        spec.write_text(json.dumps({
            "name": name, "action_name": name, "clip_dir": f"clips/{name}",
            "landmarks": f"plates/{name}/landmarks.json", "estimator": "gvhmr_siga24",
            "src_fps": out_fps, "dst_fps": 30}, indent=2), encoding="utf-8")
        print(f"spec créée : action_specs/{name}.json", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", type=Path, help="video to import (otherwise plates/<name>/<name>.mp4 must exist)")
    ap.add_argument("--name", help="plate name (default: the video's file name)")
    ap.add_argument("--start", type=float, help="in point, seconds")
    ap.add_argument("--end", type=float, help="out point, seconds")
    ap.add_argument("--character", default="Benjamin.glb", help=".glb/.gltf/.fbx with a mixamorig: skeleton")
    ap.add_argument("--blender")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the character's rest scene")
    ap.add_argument("--skip-estimate", action="store_true")
    ap.add_argument("--review", action="store_true",
                    help="also render the 2x2 review videos and measure fidelity against the video")
    args = ap.parse_args()

    if not args.video and not args.name:
        ap.error("--video or --name")
    name = sanitize(args.name or args.video.stem)
    blender = blender_exe(args.blender)
    os.environ["BLENDER"] = blender
    character = Path(args.character)
    character = character if character.is_absolute() else REPO / character
    char = sanitize(character.stem)
    scene = REPO / f"{char}_rest.blend"
    profile = REPO / "rig_profiles" / f"{char}.json"
    clip = REPO / "clips" / f"{char}_{name}"
    spec = REPO / "action_specs" / f"{name}.json"
    plate_video = REPO / "plates" / name / f"{name}.mp4"
    t0 = time.time()

    missing = [str(p) for p in (GVHMR_PY, PY) if not p.exists()]
    if not SMPLX.exists():
        missing.append(f"{SMPLX} (https://smpl-x.is.tue.mpg.de/)")
    if not character.exists() and not scene.exists():
        missing.append(str(character))
    if args.video and not args.video.exists():
        missing.append(str(args.video))
    if missing:
        raise SystemExit("Fichiers manquants :\n  " + "\n  ".join(missing))

    # 1. import
    if args.video:
        step("import", "start")
        import_video(args.video.resolve(), name, args.start, args.end)
        step("import", "done")
    else:
        if not plate_video.exists() or not spec.exists():
            raise SystemExit(f"plate {name} absente : {plate_video} / {spec}")
        step("import", "skip", "vidéo déjà importée")

    # 2. character rest scene, once
    if args.rebuild or not scene.exists() or not profile.exists():
        step("rig", "start")
        if character.suffix.lower() in (".glb", ".gltf"):
            run([blender, "--background", "--python", "pipeline/setup_rig_glb.py", "--",
                 "--glb", character, "--out", scene, "--profile", profile], "rig")
        else:
            run([blender, "--background", "--python", "pipeline/setup_rig.py", "--",
                 "--fbx", character, "--out", scene], "rig")
            profile.parent.mkdir(exist_ok=True)
            shutil.copyfile(REPO / "rig_profile.json", profile)
        step("rig", "done")
    else:
        step("rig", "skip", f"{scene.name} déjà prêt")

    # 3. GVHMR + 2D refinement + feet/hands
    if args.skip_estimate or (REPO / "plates" / name / "smplx.npz").exists():
        step("estimate", "skip", "estimation déjà faite")
    else:
        step("estimate", "start")
        run([GVHMR_PY, "pipeline/estimate_pose_gvhmr.py", "--video", plate_video,
             "--out", REPO / "plates" / name / "landmarks.json", "--smplx-only", "--feet-python", PY], "estimate")
        step("estimate", "done")

    # 4-5
    step("retarget", "start")
    run([PY, "pipeline/retarget_smplx.py", "--spec", spec, "--rig-profile", profile, "--out-dir", clip], "retarget")
    step("retarget", "done")

    step("apply", "start")
    blend = clip / f"{clip.name}.blend"
    run([blender, "-b", scene, "-P", "pipeline/bl_motion.py", "--", "apply",
         "--motion", clip / "motion.npz", "--save", blend], "apply")
    # one-button FBX export inside the .blend (N panel > Mocap)
    source_scale = json.loads(profile.read_text(encoding="utf-8")).get("source_scale")
    run([blender, "-b", blend, "-P", "pipeline/mocap_fbx_export.py", "--", "--embed"]
        + (["--source-scale", source_scale] if source_scale else []), "apply")
    blend.with_suffix(".blend1").unlink(missing_ok=True)
    step("apply", "done")

    (clip / "mocap.json").write_text(json.dumps({
        "plate": name, "character": character.name, "start": args.start, "end": args.end,
        "duration": round(probe(plate_video)["duration"], 3), "created": time.time()}, indent=1), encoding="utf-8")

    # 6-7, optional
    if args.review:
        step("review", "start")
        run([PY, "pipeline/render_review.py", "--spec", spec, "--clip", clip, "--scene", scene,
             "--blender", blender], "review")
        step("review", "done")

        step("eval", "start")
        run([PY, "pipeline/eval_fidelity.py", "--spec", spec, "--clip", clip, "--rig-profile", profile,
             "--out", clip / "fidelity.json"], "eval")
        step("eval", "done")

    print(f"\nTerminé en {time.time() - t0:.0f} s. Scène : {blend}", flush=True)
    print("@@RESULT " + json.dumps({"clip": clip.name, "blend": str(blend)}), flush=True)


if __name__ == "__main__":
    main()

"""setup_rig.py for a .glb character whose skeleton uses Mixamo bone names
(Mixamo download re-exported as glTF, Tripo auto-rig, ...).

  blender --background --python pipeline\\setup_rig_glb.py -- ^
      --glb Benjamin.glb --out benjamin_rest.blend --profile rig_profiles\\benjamin.json

A glTF import does not land in the convention the pipeline relies on
(docs/RIG.md), so this script rebuilds it:

- keeps the one armature with mixamorig: bones and the meshes it deforms;
  drops every other object (control rigs such as FaceIt, helpers, empties)
  and any baked action;
- object "Armature" rotated X=90deg, scale 0.01, bones and meshes in
  armature-space centimetres — what Blender gives for a Mixamo FBX;
- scales the character so its rest hip height is `--hip-height` metres
  (default 0.998, the Y Bot's): the retarget's contact and floor
  thresholds are in metres, tuned on human-sized rigs. The animation is
  FK in armature units, so scaling the Armature object afterwards scales
  the animated character uniformly;
- re-rolls the finger bones so each one's local +Z faces the palm: the
  procedural fist and the detected finger flexion are positive rotations
  about the bone-local +X on Mixamo skeletons (apply_mixamo_fk.py), which
  other auto-riggers do not reproduce.

Then writes the rig profile (setup_rig.dump_rig_profile) and saves the .blend.
The inverse of the scale is kept as "source_scale" in the profile and as the
Armature's "mocap_source_scale" property: the FBX export (mocap_fbx_export.py)
uses it to write the animation at the character's own size.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_rig import dump_rig_profile  # noqa: E402

FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")


def rpath(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO / p


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True, help="rest scene to write (.blend)")
    ap.add_argument("--profile", default="rig_profile.json", help="rig profile to write")
    ap.add_argument("--hip-height", type=float, default=0.998,
                    help="rest hip height in metres after scaling (0 keeps the file's size)")
    args = ap.parse_args(argv)

    glb, out, prof_path = rpath(args.glb), rpath(args.out), rpath(args.profile)
    if not glb.exists():
        raise SystemExit(f"{glb} not found")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 30
    scene.frame_start = 1
    bpy.ops.import_scene.gltf(filepath=str(glb))

    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"
            and "mixamorig:Hips" in o.data.bones]
    if len(arms) != 1:
        raise SystemExit(f"expected one armature with mixamorig: bones, found {[a.name for a in arms]}")
    arm = arms[0]
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.find_armature() is arm]
    if not meshes:
        raise SystemExit("no mesh is deformed by the mixamorig armature")

    # Everything else goes: other rigs, helper meshes, empties, baked actions.
    dropped = sorted(o.name for o in bpy.data.objects if o is not arm and o not in meshes)
    for o in list(bpy.data.objects):
        if o is not arm and o not in meshes:
            bpy.data.objects.remove(o)
    for m in meshes:
        if m.data.shape_keys and m.data.shape_keys.animation_data:
            m.data.shape_keys.animation_data_clear()
        for k in (m.data.shape_keys.key_blocks if m.data.shape_keys else []):
            k.value = 0.0
    arm.animation_data_clear()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)

    bad = [b.name for b in arm.data.bones if not b.name.startswith("mixamorig:")]
    if bad:
        raise SystemExit(f"non-mixamorig bones in the armature: {bad[:5]}")

    bpy.context.view_layer.update()
    hips_w = (arm.matrix_world @ arm.data.bones["mixamorig:Hips"].head_local)
    k = args.hip_height / hips_w.z if args.hip_height > 0 else 1.0
    print(f"glb rest hip height {hips_w.z:.4f} m -> scale x{k:.4f}")

    # New object transform (FBX convention) and the data transform that keeps
    # every bone and vertex where it was, times the uniform scale k.
    M1 = Matrix.Rotation(math.pi / 2, 4, "X") @ Matrix.Diagonal((0.01, 0.01, 0.01, 1.0))
    K = Matrix.Diagonal((k, k, k, 1.0))
    to_arm = M1.inverted() @ K

    worlds = {m.name: m.matrix_world.copy() for m in meshes}
    for m in meshes:
        m.parent = None
    arm.data.transform(to_arm @ arm.matrix_world)
    arm.matrix_world = M1
    arm.name = arm.data.name = "Armature"
    for m in meshes:
        m.data.transform(to_arm @ worlds[m.name], shape_keys=True)
        m.parent = arm
        m.matrix_parent_inverse = Matrix.Identity(4)
        m.matrix_basis = Matrix.Identity(4)
        for mod in m.modifiers:
            if mod.type == "ARMATURE":
                mod.object = arm

    # Finger rolls: local +Z toward the palm (a positive X rotation curls).
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    down = Vector((0, -1, 0))  # armature space: -Y is world down
    for side in ("Left", "Right"):
        hand = eb[f"mixamorig:{side}Hand"].head
        v1 = eb[f"mixamorig:{side}HandIndex1"].head - hand
        v2 = eb[f"mixamorig:{side}HandPinky1"].head - hand
        palm = v1.cross(v2).normalized()
        if palm.dot(down) < 0:
            palm = -palm
        print(f"{side} palm normal (armature space) {tuple(round(c, 3) for c in palm)}, "
              f"{math.degrees(palm.angle(down)):.1f} deg from straight down")
        for f in FINGERS:
            for j in range(1, 5):
                b = eb.get(f"mixamorig:{side}Hand{f}{j}")
                if b is not None:
                    b.align_roll(palm)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Same checks as setup_rig.py.
    bpy.context.view_layer.update()
    hip_z = (arm.matrix_world @ arm.pose.bones["mixamorig:Hips"].matrix).to_translation().z
    left_x = (arm.matrix_world @ arm.pose.bones["mixamorig:LeftArm"].matrix).to_translation().x
    if not (0.6 < hip_z < 1.4):
        raise SystemExit(f"rest hip world height {hip_z:.3f} m after scaling — not human scale")
    if left_x <= 0:
        raise SystemExit("LeftArm is not on world +X: the character does not face -Y like a Mixamo import")
    if len(arm.pose.bones) != 65:
        print(f"WARNING: {len(arm.pose.bones)} pose bones (standard characters have 65)")
    print(f"rest hip world height {hip_z:.4f} m; dropped {dropped}")

    arm["mocap_source_scale"] = 1.0 / k
    prof_path.parent.mkdir(parents=True, exist_ok=True)
    profile = dump_rig_profile(arm, prof_path)
    profile["source_scale"] = round(1.0 / k, 6)
    prof_path.write_text(json.dumps(profile, indent=1), encoding="utf-8")
    print(f"{prof_path.name} written — hip {profile['hip_height']} m, ground {profile['ground_z']} m, "
          f"height {profile['mesh_height'] / 100:.3f} m, meshes {len(profile['meshes'])}")
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"saved {out} — {len(arm.pose.bones)} bones, 30 fps")


if __name__ == "__main__":
    main()

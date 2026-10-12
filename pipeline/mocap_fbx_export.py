"""Export FBX « à la Mixamo » : squelette + animation, sans mesh (comme un
téléchargement Mixamo « FBX Binary, Without Skin »).

Embarqué dans chaque .blend produit par mocap.py (bloc de texte enregistré) :
vue 3D -> barre latérale (N) -> onglet « Mocap » -> « Exporter le FBX ».
Le fichier va à côté du .blend : <clip>.fbx.

Hors interface :
  blender -b <clip>.blend -P pipeline/mocap_fbx_export.py -- --export [--out x.fbx] [--pipeline-size]
  blender -b <clip>.blend -P pipeline/mocap_fbx_export.py -- --embed [--source-scale 0.448]

`--embed` copie ce script dans le .blend (texte « mocap_fbx_export.py »,
« Register » coché) et enregistre la taille d'origine du personnage
(setup_rig_glb.py le met à l'échelle humaine ; l'export la rétablit par défaut).
"""

import os
import sys

import bpy
from bpy.props import BoolProperty

TEXT_NAME = "mocap_fbx_export.py"
SCALE_PROP = "mocap_source_scale"


def find_armature():
    arm = bpy.data.objects.get("Armature")
    if arm is not None and arm.type == "ARMATURE":
        return arm
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    return arms[0] if len(arms) == 1 else None


def default_path():
    blend = bpy.data.filepath
    if not blend:
        return None
    return os.path.join(os.path.dirname(blend), os.path.splitext(os.path.basename(blend))[0] + ".fbx")


def action_fcurves(action, slot):
    if getattr(action, "layers", None):          # Blender 4.4+: layer / strip / channelbag
        for layer in action.layers:
            for strip in layer.strips:
                bag = strip.channelbag(slot)
                if bag:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


def export_fbx(filepath=None, source_size=True):
    """Export the armature and its active action; returns the written path.

    Works on a temporary copy (the scene is left untouched): keys shifted so the
    take starts at time 0 like a Mixamo file, and at the character's own size
    the scale goes into the bones and the Hips translation (the armature keeps
    Mixamo's 0.01 object scale). The take is named after the .blend."""
    arm = find_armature()
    if arm is None:
        raise RuntimeError("pas d'armature unique dans la scène")
    action = arm.animation_data.action if arm.animation_data else None
    if action is None:
        raise RuntimeError("l'armature n'a pas d'action")
    path = bpy.path.abspath(filepath) if filepath else default_path()
    if not path:
        raise RuntimeError("enregistre le .blend d'abord (le FBX va à côté)")
    take = os.path.splitext(os.path.basename(path))[0]
    scale = float(arm.get(SCALE_PROP, 1.0)) if source_size else 1.0

    scene, view_layer = bpy.context.scene, bpy.context.view_layer
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    saved = ([o for o in view_layer.objects if o.select_get()], view_layer.objects.active,
             scene.frame_start, scene.frame_end, scene.frame_current, scene.name, arm.name, arm.data.name)
    f0, f1 = (int(round(f)) for f in action.frame_range)

    tmp = arm.copy()
    tmp.data = arm.data.copy()
    tmp_action = action.copy()
    tmp.animation_data.action = tmp_action
    if getattr(tmp_action, "slots", None):
        tmp.animation_data.action_slot = tmp_action.slots[0]
    scene.collection.objects.link(tmp)
    if abs(scale - 1.0) > 1e-6:
        from mathutils import Matrix
        tmp.data.transform(Matrix.Scale(scale, 4))
    for fc in action_fcurves(tmp_action, tmp.animation_data.action_slot if getattr(tmp_action, "slots", None) else None):
        k = scale if fc.data_path.endswith(".location") else 1.0
        for kp in fc.keyframe_points:
            for pt in (kp.co, kp.handle_left, kp.handle_right):
                pt.x -= f0
                pt.y *= k
        fc.update()

    arm.name, arm.data.name = "Armature__src", "Armature__src"
    tmp.name, tmp.data.name = "Armature", "Armature"
    scene.name = take
    scene.frame_start, scene.frame_end = 0, f1 - f0
    for o in view_layer.objects:
        o.select_set(o == tmp)
    view_layer.objects.active = tmp
    try:
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=True, object_types={"ARMATURE"},
            global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
            axis_forward="-Z", axis_up="Y",
            add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
            use_armature_deform_only=False, armature_nodetype="NULL",
            bake_anim=True, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True,
            bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
            use_custom_props=False, path_mode="AUTO")
    finally:
        tmp_data = tmp.data
        bpy.data.objects.remove(tmp)
        bpy.data.armatures.remove(tmp_data)
        bpy.data.actions.remove(tmp_action)
        arm.name, arm.data.name, scene.name = saved[6], saved[7], saved[5]
        for o in view_layer.objects:
            o.select_set(o in saved[0])
        view_layer.objects.active = saved[1]
        scene.frame_start, scene.frame_end = saved[2], saved[3]
        scene.frame_set(saved[4])
    return path


# ---------------------------------------------------------------------------
# UI

class MOCAP_OT_export_fbx(bpy.types.Operator):
    bl_idname = "mocap.export_fbx"
    bl_label = "Exporter le FBX"
    bl_description = "Squelette + animation, sans mesh, à côté du .blend (comme Mixamo « Without Skin »)"

    def execute(self, context):
        try:
            path = export_fbx(source_size=context.scene.mocap_source_size)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.scene["mocap_last_export"] = path
        self.report({"INFO"}, f"FBX exporté : {path}")
        return {"FINISHED"}


class MOCAP_OT_open_folder(bpy.types.Operator):
    bl_idname = "mocap.open_folder"
    bl_label = "Ouvrir le dossier"

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=os.path.dirname(bpy.data.filepath) or "//")
        return {"FINISHED"}


class MOCAP_PT_export(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Mocap"
    bl_label = "Export FBX"

    def draw(self, context):
        layout = self.layout
        arm = find_armature()
        action = arm.animation_data.action if arm and arm.animation_data else None
        if action is None:
            layout.label(text="Pas d'armature animée", icon="ERROR")
            return
        f0, f1 = action.frame_range
        col = layout.column(align=True)
        col.label(text=f"Action : {action.name}", icon="ACTION")
        col.label(text=f"Images {int(f0)} – {int(f1)} · {context.scene.render.fps} fps")
        scale = float(arm.get(SCALE_PROP, 1.0))
        if abs(scale - 1.0) > 1e-4:
            layout.prop(context.scene, "mocap_source_size", text=f"Taille d'origine (×{scale:.3f})")
        layout.operator(MOCAP_OT_export_fbx.bl_idname, icon="EXPORT")
        last = context.scene.get("mocap_last_export")
        if last:
            box = layout.box()
            box.label(text=os.path.basename(last), icon="CHECKMARK")
            box.operator(MOCAP_OT_open_folder.bl_idname, icon="FILE_FOLDER")


CLASSES = (MOCAP_OT_export_fbx, MOCAP_OT_open_folder, MOCAP_PT_export)


def register():
    # Idempotent: the text runs on file load (Register) and again on « Run Script ».
    for cls in CLASSES:
        old = getattr(bpy.types, cls.__name__, None)
        if old is not None:
            bpy.utils.unregister_class(old)
        bpy.utils.register_class(cls)
    bpy.types.Scene.mocap_source_size = BoolProperty(
        name="Taille d'origine", default=True,
        description="Exporter à la taille du personnage d'origine (sinon à l'échelle humaine du pipeline)")


def unregister():
    for cls in reversed(CLASSES):
        if getattr(bpy.types, cls.__name__, None) is not None:
            bpy.utils.unregister_class(cls)


# ---------------------------------------------------------------------------
# command line (blender -b ... -P pipeline/mocap_fbx_export.py -- ...)

def embed(source_scale=None):
    src = os.path.abspath(__file__)
    with open(src, encoding="utf-8") as f:
        code = f.read()
    text = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
    text.clear()
    text.write(code)
    text.use_module = True
    arm = find_armature()
    if arm is not None and source_scale:
        arm[SCALE_PROP] = float(source_scale)
    bpy.ops.wm.save_mainfile()
    print(f"embedded {TEXT_NAME} in {bpy.data.filepath}"
          + (f" (source scale {float(source_scale):.4f})" if source_scale else ""))


def cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="mocap_fbx_export")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--pipeline-size", action="store_true", help="keep the pipeline's human-scale size")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--source-scale", type=float)
    args = ap.parse_args(argv)
    if args.embed:
        embed(args.source_scale)
    if args.export:
        print("fbx:", export_fbx(args.out, source_size=not args.pipeline_size))


if __name__ == "__main__" and "--" in sys.argv and bpy.app.background:
    cli(sys.argv[sys.argv.index("--") + 1:])
else:
    register()

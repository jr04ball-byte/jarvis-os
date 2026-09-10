"""Ambient background loop for Jarvis dashboard (Blender 5.2 background mode).

Usage:
  "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" -b -P tools\\blender_bg_bake.py -- --frames 120 --res 960x540

Output: api-gateway/assets/bg-loop-####.png (encode to webm/mp4 afterwards)
Subtle dark nebula drift: emission plane with animated noise, seamless
rotation loop so frame N wraps to frame 1.
"""
import argparse
import math
import os
import sys

import bpy


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=120, type=int)
    ap.add_argument("--res", default="960x540")
    args, _ = ap.parse_known_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    return args


def main():
    args = parse_args()
    w, h = (int(x) for x in args.res.lower().split("x"))
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    frames = max(24, args.frames)
    scene.frame_start = 1
    scene.frame_end = frames

    world = bpy.data.worlds["World"]
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.031, 0.043, 0.07, 1.0)

    for o in list(bpy.data.objects):
        if o.type in {"MESH", "LIGHT", "CAMERA"}:
            bpy.data.objects.remove(o, do_unlink=True)

    # Full-frame plane
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    plane = bpy.context.active_object
    mat = bpy.data.materials.new("BgMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)
    emis = nodes.new("ShaderNodeEmission")
    emis.location = (300, 0)
    emis.inputs[1].default_value = 0.45
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (100, 0)
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.02, 0.03, 0.055, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.13, 0.14, 0.32, 1.0)
    mid = ramp.color_ramp.elements.new(0.58)
    mid.color = (0.03, 0.045, 0.08, 1.0)
    mid2 = ramp.color_ramp.elements.new(0.8)
    mid2.color = (0.10, 0.10, 0.26, 1.0)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (-100, 0)
    noise.inputs["Scale"].default_value = 2.6
    noise.inputs["Detail"].default_value = 4.0
    tex = nodes.new("ShaderNodeTexCoord")
    tex.location = (-300, 0)
    links.new(tex.outputs["Generated"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs[0])
    links.new(ramp.outputs[0], emis.inputs[0])
    links.new(emis.outputs[0], out.inputs[0])
    plane.data.materials.append(mat)

    # Ortho camera filling the plane
    cam_data = bpy.data.cameras.new("BgCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 16
    cam = bpy.data.objects.new("BgCam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (0, 0, 5)
    scene.camera = cam

    # Seamless drift: rotate UV via empty (rotation loops 360)
    bpy.ops.object.empty_add(location=(0, 0, 0))
    empty = bpy.context.active_object
    empty.rotation_euler = (0, 0, 0)
    empty.keyframe_insert(data_path="rotation_euler", frame=1)
    empty.rotation_euler = (0, 0, math.radians(360))
    empty.keyframe_insert(data_path="rotation_euler", frame=frames)
    # Drive noise vector rotation through empty is complex; instead rotate plane itself (symmetric noise -> seamless)
    plane.rotation_euler = (0, 0, 0)
    plane.keyframe_insert(data_path="rotation_euler", frame=1)
    plane.rotation_euler = (0, 0, math.radians(360))
    plane.keyframe_insert(data_path="rotation_euler", frame=frames)
    for o in (plane, empty):
        try:
            fcurves = list(o.animation_data.action.fcurves)
        except AttributeError:
            fcurves = []
            try:
                for layer in o.animation_data.action.layers:
                    for strip in layer.strips:
                        try:
                            fcurves.extend(list(strip.channelbag.fcurves))
                        except AttributeError:
                            pass
            except AttributeError:
                pass
        for f in fcurves:
            for kp in f.keyframe_points:
                kp.interpolation = "LINEAR"

    out_dir = os.path.join(os.path.dirname(__file__), "..", "api-gateway", "assets")
    os.makedirs(out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(out_dir, "bg-loop-")
    bpy.ops.render.render(animation=True)
    print("bake done:", out_dir)


if __name__ == "__main__":
    main()

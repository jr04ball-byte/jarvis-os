"""Jarvis orb loop baker for Blender 5.2 (background mode).

Usage (Windows):
  "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" -b -P tools\\blender_orb_bake.py -- --frames 120 --res 620x440

Output:
  api-gateway/assets/orb-loop-0001..N.png (+ orb-loop.webm/mp4 if ffmpeg is found)
  Dashboard auto-plays /orb-loop.webm when present, else keeps the live canvas orb.

Dark neon command-deck look: near-black bg, violet/blue emissive core,
aqua rim light, slow rotation + emission pulse for a seamless loop.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys

import bpy


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=120, type=int)
    ap.add_argument("--res", default="620x440")
    ap.add_argument("--engine", default="BLENDER_EEVEE_NEXT")
    args, _ = ap.parse_known_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    return args


def main():
    args = parse_args()
    w, h = (int(x) for x in args.res.lower().split("x"))
    scene = bpy.context.scene
    try:
        scene.render.engine = args.engine
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    frames = max(24, args.frames)
    scene.frame_start = 1
    scene.frame_end = frames

    # World: near-black command deck
    world = bpy.data.worlds["World"]
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.031, 0.043, 0.07, 1.0)
        bg.inputs[1].default_value = 1.0

    # Clear default cube/light/camera
    for o in list(bpy.data.objects):
        if o.type in {"MESH", "LIGHT", "LIGHT_PROBE"}:
            bpy.data.objects.remove(o, do_unlink=True)

    # Core orb
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0, location=(0, 0, 0))
    orb = bpy.context.active_object
    orb.name = "JarvisOrb"
    mat = bpy.data.materials.new("OrbMat")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    fres = nodes.new("ShaderNodeFresnel")
    fres.location = (-400, 200)
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-200, 200)
    # Center lavender -> mid violet -> edge deep indigo (kept under clip)
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.72, 0.74, 1.0, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.14, 0.11, 0.48, 1.0)
    mid = ramp.color_ramp.elements.new(0.45)
    mid.color = (0.42, 0.34, 0.95, 1.0)
    emis = nodes.new("ShaderNodeEmission")
    emis.location = (200, 0)
    emis.inputs[1].default_value = 1.15
    links.new(fres.outputs[0], ramp.inputs[0])
    links.new(ramp.outputs[0], emis.inputs[0])
    links.new(emis.outputs[0], out.inputs[0])
    orb.data.materials.append(mat)

    # Inner bright core
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.45, location=(0, 0, 0.15))
    core = bpy.context.active_object
    core.name = "JarvisCore"
    cmat = bpy.data.materials.new("CoreMat")
    cmat.use_nodes = True
    cn, cl = cmat.node_tree.nodes, cmat.node_tree.links
    cn.clear()
    cout = cn.new("ShaderNodeOutputMaterial")
    cemis = cn.new("ShaderNodeEmission")
    cemis.inputs[0].default_value = (0.75, 0.85, 1.0, 1.0)
    cemis.inputs[1].default_value = 4.0
    cl.new(cemis.outputs[0], cout.inputs[0])
    core.data.materials.append(cmat)

    # Lights: violet key + aqua rim
    bpy.ops.object.light_add(type="POINT", location=(3, -2, 4))
    key = bpy.context.active_object
    key.data.energy = 800
    key.data.color = (0.55, 0.45, 1.0)
    bpy.ops.object.light_add(type="POINT", location=(-4, 2, 1))
    rim = bpy.context.active_object
    rim.data.energy = 500
    rim.data.color = (0.27, 0.88, 0.82)

    # Camera
    cam_data = bpy.data.cameras.new("OrbCam")
    cam = bpy.data.objects.new("OrbCam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (0, -4.2, 0.6)
    cam.rotation_euler = (math.radians(82), 0, 0)
    scene.camera = cam

    # Seamless loop: full rotation + sine emission pulse
    orb.rotation_mode = "XYZ"
    orb.rotation_euler = (0, 0, 0)
    orb.keyframe_insert(data_path="rotation_euler", frame=1)
    orb.rotation_euler = (0, 0, math.radians(360))
    orb.keyframe_insert(data_path="rotation_euler", frame=frames)
    act = orb.animation_data.action
    try:
        fcurves = list(act.fcurves)
    except AttributeError:
        # Blender 4.4+ layered actions
        fcurves = []
        try:
            for layer in act.layers:
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
    # Emission pulse (2 cycles per loop for seamlessness)
    for i in range(frames + 1):
        f = i + 1
        v = 1.15 + 0.35 * math.sin(2 * math.pi * 2 * i / frames)
        emis.inputs[1].default_value = v
        try:
            emis.inputs[1].keyframe_insert(data_path="default_value", frame=f)
        except Exception:
            pass

    out_dir = os.path.join(os.path.dirname(__file__), "..", "api-gateway", "assets")
    os.makedirs(out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(out_dir, "orb-loop-")
    bpy.ops.render.render(animation=True)

    # Encode loop if ffmpeg available
    ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ff:
        for ext, extra in (("webm", ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0"]),
                           ("mp4", ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])):
            out = os.path.join(out_dir, f"orb-loop.{ext}")
            cmd = [ff, "-y", "-framerate", "24", "-i", os.path.join(out_dir, "orb-loop-%04d.png"),
                   *extra, "-t", str(frames / 24), out]
            subprocess.run(cmd, check=False)
            print("wrote", out)
    print("bake done:", out_dir)


if __name__ == "__main__":
    main()

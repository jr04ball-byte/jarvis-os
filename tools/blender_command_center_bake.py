"""Bake Jarvis V23 Command Center motion assets with Blender.

Designed for Blender 4.x/5.x background mode. The dashboard works without this
bake using the existing Blender orb plus CSS/SVG motion; running this script
upgrades the center core to a richer multi-ring animated scene.

Windows example:
  "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" -b \
    -P tools\\blender_command_center_bake.py -- --frames 144 --res 900x600

Outputs (api-gateway/assets):
  command-center-loop.webm
  command-center-loop.mp4
  command-center-poster.png
  jarvis-command-center.blend

No network access is required. ffmpeg is used when available.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import bpy


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=144)
    parser.add_argument("--res", default="900x600")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--samples", type=int, default=48)
    parser.add_argument("--keep-frames", action="store_true")
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def mat_emission(name: str, rgba: tuple[float, float, float, float], strength: float):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs[0].default_value = rgba
    emission.inputs[1].default_value = strength
    links.new(emission.outputs[0], out.inputs[0])
    return mat, emission


def add_torus(name: str, major: float, minor: float, rotation, color, strength=4.0):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major,
        minor_radius=minor,
        major_segments=96,
        minor_segments=12,
        location=(0, 0, 0),
        rotation=rotation,
    )
    obj = bpy.context.active_object
    obj.name = name
    mat, _ = mat_emission(name + "Mat", color, strength)
    obj.data.materials.append(mat)
    return obj


def linearize(obj) -> None:
    action = getattr(getattr(obj, "animation_data", None), "action", None)
    if not action:
        return
    fcurves = []
    try:
        fcurves = list(action.fcurves)
    except Exception:
        try:
            for layer in action.layers:
                for strip in layer.strips:
                    channelbag = getattr(strip, "channelbag", None)
                    if channelbag:
                        fcurves.extend(list(channelbag.fcurves))
        except Exception:
            return
    for curve in fcurves:
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"


def key_loop_rotation(obj, frames: int, axis: int = 2, turns: float = 1.0, reverse: bool = False) -> None:
    obj.rotation_mode = "XYZ"
    start = list(obj.rotation_euler)
    obj.keyframe_insert(data_path="rotation_euler", frame=1)
    sign = -1.0 if reverse else 1.0
    end = list(start)
    end[axis] += math.tau * turns * sign
    obj.rotation_euler = end
    obj.keyframe_insert(data_path="rotation_euler", frame=frames + 1)
    linearize(obj)


def point_camera(cam, target=(0.0, 0.0, 0.0)):
    import mathutils

    direction = mathutils.Vector(target) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    cfg = args()
    frames = max(48, min(cfg.frames, 720))
    fps = max(12, min(cfg.fps, 60))
    width, height = [int(x) for x in cfg.res.lower().split("x", 1)]

    root = Path(__file__).resolve().parents[1]
    assets = root / "api-gateway" / "assets"
    frames_dir = assets / "command-center-frames"
    assets.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.frame_start = 1
    scene.frame_end = frames + 1
    if hasattr(scene, "eevee"):
        try:
            scene.eevee.taa_render_samples = cfg.samples
        except Exception:
            pass

    # Clear scene safely.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        # Orphan cleanup is intentionally conservative; Blender owns lifecycle.
        pass

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.003, 0.008, 0.018, 1.0)
        bg.inputs[1].default_value = 0.15

    # Central nested core.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=0.88, location=(0, 0, 0))
    core = bpy.context.active_object
    core.name = "JarvisCore"
    core_mat, core_emission = mat_emission("JarvisCoreMat", (0.25, 0.62, 1.0, 1.0), 4.2)
    core.data.materials.append(core_mat)

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=0.50, location=(0, -0.02, 0.03))
    inner = bpy.context.active_object
    inner.name = "JarvisInnerCore"
    inner_mat, inner_emission = mat_emission("JarvisInnerMat", (0.67, 0.50, 1.0, 1.0), 8.5)
    inner.data.materials.append(inner_mat)

    # Three mechanical intelligence rings.
    ring_a = add_torus("RouterRing", 1.22, 0.024, (math.radians(68), 0, math.radians(8)), (0.12, 0.75, 1.0, 1), 5.2)
    ring_b = add_torus("MemoryRing", 1.48, 0.018, (math.radians(25), math.radians(62), 0), (0.57, 0.35, 1.0, 1), 4.5)
    ring_c = add_torus("VerifyRing", 1.72, 0.012, (math.radians(88), math.radians(8), math.radians(35)), (0.25, 1.0, 0.74, 1), 3.6)
    key_loop_rotation(ring_a, frames, 2, 1.0)
    key_loop_rotation(ring_b, frames, 1, 1.0, reverse=True)
    key_loop_rotation(ring_c, frames, 2, 0.5)

    # Four provider nodes orbiting independently.
    node_colors = [
        (0.25, 0.78, 1.0, 1.0),
        (0.95, 0.78, 0.32, 1.0),
        (0.31, 1.0, 0.66, 1.0),
        (0.70, 0.42, 1.0, 1.0),
    ]
    for idx, color in enumerate(node_colors):
        pivot = bpy.data.objects.new(f"ProviderPivot{idx}", None)
        bpy.context.collection.objects.link(pivot)
        pivot.rotation_euler = (math.radians(15 * (idx % 2)), math.radians(10 * idx), math.radians(90 * idx))
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.095, location=(1.95, 0, 0))
        node = bpy.context.active_object
        node.name = f"ProviderNode{idx}"
        node.parent = pivot
        mat, _ = mat_emission(f"ProviderNodeMat{idx}", color, 7.0)
        node.data.materials.append(mat)
        key_loop_rotation(pivot, frames, 2, 1.0, reverse=bool(idx % 2))

    # Thin vertical energy spines around the core add movement without clutter.
    for idx, angle in enumerate((0, 90, 180, 270)):
        rad = math.radians(angle)
        x, y = 1.06 * math.cos(rad), 1.06 * math.sin(rad)
        bpy.ops.mesh.primitive_cube_add(location=(x, y, 0), scale=(0.008, 0.008, 0.52))
        spine = bpy.context.active_object
        spine.name = f"EnergySpine{idx}"
        mat, _ = mat_emission(f"EnergySpineMat{idx}", (0.15, 0.68, 1.0, 1.0), 3.0)
        spine.data.materials.append(mat)

    # Core energy pulses, exactly periodic across the loop.
    for frame in range(1, frames + 2):
        t = (frame - 1) / frames
        pulse = 4.2 + 1.4 * math.sin(math.tau * 2 * t)
        core_emission.inputs[1].default_value = pulse
        core_emission.inputs[1].keyframe_insert(data_path="default_value", frame=frame)
        inner_emission.inputs[1].default_value = 8.5 + 2.2 * math.sin(math.tau * 2 * t + math.pi / 2)
        inner_emission.inputs[1].keyframe_insert(data_path="default_value", frame=frame)
        scale = 1.0 + 0.035 * math.sin(math.tau * 2 * t)
        core.scale = (scale, scale, scale)
        core.keyframe_insert(data_path="scale", frame=frame)

    # Camera and modest lens perspective.
    cam_data = bpy.data.cameras.new("JarvisCommandCamera")
    cam = bpy.data.objects.new("JarvisCommandCamera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (0, -5.55, 1.0)
    cam.data.lens = 58
    point_camera(cam, (0, 0, 0.05))
    scene.camera = cam

    # Subtle area lights provide shape even though the hero parts are emissive.
    for loc, energy, color, size in [
        ((3.2, -2.4, 4.0), 650, (0.30, 0.58, 1.0), 4.0),
        ((-3.0, -1.4, 2.0), 500, (0.62, 0.38, 1.0), 3.0),
        ((0.0, 2.2, -1.8), 350, (0.24, 1.0, 0.74), 3.0),
    ]:
        bpy.ops.object.light_add(type="AREA", location=loc)
        light = bpy.context.active_object
        light.data.energy = energy
        light.data.color = color
        light.data.shape = "DISK"
        light.data.size = size
        point_camera(light, (0, 0, 0))

    # Compositor glow gives a premium neon finish and is deterministic.
    scene.use_nodes = True
    nt = scene.node_tree
    nt.nodes.clear()
    render = nt.nodes.new("CompositorNodeRLayers")
    glare = nt.nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.quality = "HIGH"
    glare.threshold = 0.35
    glare.size = 7
    comp = nt.nodes.new("CompositorNodeComposite")
    nt.links.new(render.outputs["Image"], glare.inputs["Image"])
    nt.links.new(glare.outputs["Image"], comp.inputs["Image"])

    blend_path = assets / "jarvis-command-center.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    # Render frames. frame N+1 duplicates the loop start, so encode only N frames.
    scene.render.filepath = str(frames_dir / "frame-")
    bpy.ops.render.render(animation=True)

    # Poster is the first rendered frame.
    first = frames_dir / "frame-0001.png"
    if first.exists():
        shutil.copy2(first, assets / "command-center-poster.png")

    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg:
        input_pattern = str(frames_dir / "frame-%04d.png")
        webm = assets / "command-center-loop.webm"
        mp4 = assets / "command-center-loop.mp4"
        subprocess.run([
            ffmpeg, "-y", "-framerate", str(fps), "-start_number", "1", "-i", input_pattern,
            "-frames:v", str(frames), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0", "-crf", "28", "-b:v", "0", str(webm),
        ], check=False)
        subprocess.run([
            ffmpeg, "-y", "-framerate", str(fps), "-start_number", "1", "-i", input_pattern,
            "-frames:v", str(frames), "-vf", "format=yuv420p", "-c:v", "libx264",
            "-crf", "20", "-movflags", "+faststart", str(mp4),
        ], check=False)
        print("Encoded:", webm)
        print("Encoded:", mp4)
    else:
        print("ffmpeg was not found. PNG sequence remains in", frames_dir)

    if not cfg.keep_frames and ffmpeg:
        shutil.rmtree(frames_dir, ignore_errors=True)

    print("Saved Blender scene:", blend_path)
    print("Jarvis Command Center bake complete.")


if __name__ == "__main__":
    main()

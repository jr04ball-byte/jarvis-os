"""V25 reference-matched 2.5D Blender animation: artwork plus 3D light traces.

blender -b -P tools/blender_neon_v25.py -- --output-dir PATH --frames 120
Render PNG frames and a packed editable .blend. Encode frames with FFmpeg.
"""
import argparse
import math
import sys
from pathlib import Path

import bpy


def emission(name, color, strength=1):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    out = nodes.new('ShaderNodeOutputMaterial')
    light = nodes.new('ShaderNodeEmission')
    light.inputs['Color'].default_value = (*color, 1)
    light.inputs['Strength'].default_value = strength
    material.node_tree.links.new(light.outputs[0], out.inputs['Surface'])
    return material


def curve(name, points, material, width):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    data.bevel_depth = width
    data.bevel_resolution = 2
    spline = data.splines.new('POLY')
    spline.points.add(len(points)-1)
    for p, coords in zip(spline.points, points):
        p.co = (*coords, 1)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=120)
    parser.add_argument('--width', type=int, default=960)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.width*2//3
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.frame_start, scene.frame_end = 1, args.frames
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.view_settings.view_transform = 'Standard'
    scene.world.color = (0, 0, 0)
    bpy.ops.object.camera_add(location=(0, 0, 10))
    scene.camera = bpy.context.object
    scene.camera.data.type = 'ORTHO'
    scene.camera.data.ortho_scale = 6
    bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, -3))
    backdrop = bpy.context.object
    backdrop.name = 'Reference artwork — fixed camera plate'
    backdrop.scale = (3, 2, 1)
    material = emission('Reference artwork', (1, 1, 1))
    image = material.node_tree.nodes.new('ShaderNodeTexImage')
    image.image = bpy.data.images.load(str(Path(__file__).resolve().parents[1]/'api-gateway/assets/neon-neural-v25.png'))
    image.image.pack()
    material.node_tree.links.new(image.outputs['Color'], material.node_tree.nodes.get('Emission').inputs['Color'])
    backdrop.data.materials.append(material)
    blue = emission('Ice-blue traces', (.08, .5, 1), 2)
    cyan = emission('Cyan nodes', (.4, .88, 1), 2)
    amber = emission('Amber accents', (1, .4, .1), 2)
    arcs = []
    for i in range(7):
        points = []
        for j in range(45):
            angle = j/44*math.pi*.52+i*.7
            points.append((1.30*math.cos(angle), 1.30*math.sin(angle), 0))
        arc = curve('Moving neural trace '+str(i), points, amber if i==0 else blue, .0035)
        arc.location.y = .42
        arcs.append(arc)
    particles = []
    for i in range(20):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=.010 if i%3 else .015)
        obj = bpy.context.object
        obj.name = 'Signal light '+str(i)
        obj.data.materials.append(cyan if i%4 else amber)
        particles.append(obj)
    beams = []
    for i in range(5):
        beam = curve('Beam pulse '+str(i), [(0, 0, 0), (0, .20, 0)], cyan, .004)
        beam.location.x = (i-2)*.027
        beams.append(beam)
    for frame in range(1, args.frames+2):
        phase = 2*math.pi*(frame-1)/args.frames
        for i, arc in enumerate(arcs):
            arc.rotation_euler = (.55*math.sin(phase+i), .65*math.cos(phase+i*.8), phase*(1 if i%2 else -1)+i)
            arc.keyframe_insert('rotation_euler', frame=frame)
        for i, obj in enumerate(particles):
            angle = phase+i*2.399
            obj.location = (1.31*math.cos(angle), .42+1.1*math.sin(angle), .3+.4*math.sin(angle+i))
            obj.keyframe_insert('location', frame=frame)
        for i, beam in enumerate(beams):
            beam.location.y = -1.12+((frame-1)/args.frames+i/5)%1*2.85
            beam.keyframe_insert('location', frame=frame)
    scene.render.filepath = str(out/'frame-')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'jarvis-neon-v25.blend'))
    bpy.ops.render.render(animation=True)


if __name__ == '__main__':
    main()


"""Generate Blender scene code through the configured provider."""
import ast
import re
from providers import ProviderMessage


async def generate(goal, blend_path, render_path):
    from brains import providers
    provider = providers.get('gemini')
    if provider is None or not provider.configured:
        raise RuntimeError('Gemini must be configured for request-driven Blender generation')
    prompt = (
        'Write a complete Blender Python scene for this request: ' + goal + '\n'
        'Return Python only. Create detailed geometry, materials, lighting and an active camera. '
        'Use only bpy, math, random, mathutils imports. No external files, network, shell, '
        'package installation, text execution, or file operations. Do not save or render; '
        'the worker adds those steps. Use 960x640 resolution and 32 Cycles samples. '
        'Use broadly compatible Blender APIs. For primitive_uv_sphere_add use ring_count, never rings. '
        'For primitive_cylinder_add use vertices for segment count, never rings or ring_count. '
        'Use Principled BSDF inputs Base Color, Metallic, Roughness; avoid obsolete Specular inputs. '
        'Follow the requested subject, not a generic cube. '
        'Keep the complete script under 180 lines using loops for repeated detail. '
        'Create and assign the camera near the beginning. '
        'End the complete script with the exact comment # JARVIS_SCENE_COMPLETE.'
    )
    for attempt in range(2):
        result = await provider.complete([ProviderMessage(role='user', content=prompt)], temperature=0.3, max_tokens=32768)
        code = result.content.strip()
        if code.startswith('```'):
            code = re.sub(r'^```(?:python)?\s*', '', code)
            code = re.sub(r'\s*```$', '', code)
        if code.endswith('# JARVIS_SCENE_COMPLETE') and result.metadata.get('finish_reason') != 'MAX_TOKENS':
            break
        if attempt:
            raise ValueError('Gemini returned an incomplete scene twice; no script was executed')
        prompt += '\nThe previous response was incomplete. Generate a COMPLETE compact script under 100 lines, including camera and the final completion comment. Reduce complexity as needed, retaining the requested subject.'
    tree = ast.parse(code)
    # Conservative lint, not a security sandbox. Never run this outside the approved worker.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if any(m.split('.')[0] not in {'bpy','math','random','mathutils'} for m in modules):
                raise ValueError('Scene requested an unsupported import')
        if isinstance(node, ast.Name) and node.id in {'exec','eval','compile','open','__import__','getattr','setattr'}:
            raise ValueError('Scene requested dynamic execution or file access')
        if isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            raise ValueError('Scene requested unsupported introspection')
    return code + f'''\nimport bpy
if bpy.context.scene.camera is None:
    raise RuntimeError("Generated scene has no active camera")
bpy.context.scene.render.filepath = {str(render_path)!r}
bpy.context.scene.render.image_settings.file_format = 'PNG'
bpy.ops.wm.save_as_mainfile(filepath={str(blend_path)!r})
bpy.ops.render.render(write_still=True)
'''

# SPDX-License-Identifier: Apache-2.0
"""Benchmark shader refresh on a material containing an unused node graph.

python -B benchmarks/shaders.py --nodes 500 --samples 3
blender --background --python benchmarks/shaders.py -- --nodes 500 --samples 3

Use --addon /path/to/contact_blend.py to compare a previous revision on the same
Blender runtime. This measures shader rebuilding, not geometry evaluation or rendering.
There is no timing assertion; rendered shader wiring and original-material preservation
are checked independently of the timed calls.
"""

import argparse
import importlib.util
import json
import pathlib
import statistics
import sys
import time

import bpy


def fingerprint(tree):
    return (tuple((n.name, n.bl_idname) for n in tree.nodes),
            tuple((link.from_node.name, link.from_socket.identifier,
                   link.to_node.name, link.to_socket.identifier) for link in tree.links))


def main():
    if "--" in sys.argv:
        arguments = sys.argv[sys.argv.index("--") + 1:]
    elif "--python" in sys.argv:
        arguments = []
    else:
        arguments = sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1] / "contact_blend.py")
    parser.add_argument("--nodes", type=int, default=100, help="Unused Noise nodes (default 100)")
    parser.add_argument("--samples", type=int, default=3, help="Measured refreshes after warm-up")
    args = parser.parse_args(arguments)
    if args.nodes < 0 or args.samples < 1:
        parser.error("--nodes must be nonnegative and --samples must be positive")
    spec = importlib.util.spec_from_file_location("contact_blend_benchmark", args.addon)
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_plane_add(size=4)
    target = bpy.context.object
    target_material = bpy.data.materials.new("Ground")
    target_material.use_nodes = True
    target.data.materials.append(target_material)
    target_shader = target_material.node_tree.nodes["Principled BSDF"]
    target_shader.inputs["Base Color"].default_value = (0.12, 0.34, 0.56, 1)
    target_shader.inputs["Roughness"].default_value = 0.37
    expected_color = tuple(target_shader.inputs["Base Color"].default_value)
    expected_roughness = target_shader.inputs["Roughness"].default_value
    for _ in range(args.nodes):
        target_material.node_tree.nodes.new("ShaderNodeTexNoise")
    original = fingerprint(target_material.node_tree)

    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0.4))
    source = bpy.context.object
    original_source = bpy.data.materials.new("Hero")
    original_source.use_nodes = True
    source.data.materials.append(original_source)
    original_source_graph = fingerprint(original_source.node_tree)
    mod = source.modifiers.new(cb.GROUP, "NODES")
    mod.node_group = cb.build_group()
    values = cb.modifier_values(mod) if hasattr(cb, "modifier_values") else mod
    values[cb.input_ids(mod.node_group)["Target"]] = target

    def validate():
        assert fingerprint(target_material.node_tree) == original
        assert fingerprint(original_source.node_tree) == original_source_graph
        assert tuple(target_shader.inputs["Base Color"].default_value) == expected_color
        assert target_shader.inputs["Roughness"].default_value == expected_roughness
        material = source.active_material
        mix = cb._material_mix(material)
        assert mix and mix.type == "MIX_SHADER"
        output, surface = cb._surface(material)
        assert surface.node == mix and output.inputs["Surface"].is_linked
        copied = mix.inputs[2].links[0].from_node
        assert copied.type == "BSDF_PRINCIPLED"
        assert tuple(copied.inputs["Base Color"].default_value) == expected_color
        assert copied.inputs["Roughness"].default_value == expected_roughness
        assert mix.inputs[1].links[0].from_node.type == "BSDF_PRINCIPLED"
        assert mix.inputs[0].links[0].from_node.attribute_name == cb.ATTR

    assert cb.setup_material(source, target, refresh=True) is None
    validate()
    timings = []
    for _ in range(args.samples):
        started = time.perf_counter()
        error = cb.setup_material(source, target, refresh=True)
        timings.append(time.perf_counter() - started)
        assert error is None, error
        validate()
    tree = source.active_material.node_tree
    print(json.dumps({
        "blender": bpy.app.version_string,
        "addon_version": cb.bl_info["version"],
        "unused_nodes": args.nodes,
        "samples": args.samples,
        "median_refresh_ms": round(statistics.median(timings) * 1000, 3),
        "source_nodes": len(tree.nodes),
        "copied_target_nodes": sum(n.get(cb.GENERATED_KEY) == "target" for n in tree.nodes),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

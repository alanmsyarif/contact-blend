# SPDX-License-Identifier: Apache-2.0
"""Measure shading-only Geometry Nodes evaluation with a costly unused ground branch.

Run with a Python interpreter containing Blender's bpy package:
    python -B benchmarks/geometry.py [path/to/contact_blend.py]

The default is this checkout's add-on. Pass an older copy to compare revisions on the
same machine. Two warmup evaluations are followed by six measured evaluations. Each
evaluation moves the hero to invalidate the relative-target geometry dependency.
There are no timing thresholds; machine resources and Blender versions affect results.
"""

import argparse
import pathlib
import runpy
import statistics
import time

import bpy


def benchmark(addon_path):
    addon = runpy.run_path(str(addon_path))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=60, y_subdivisions=60)
    ground = bpy.context.object
    ground.data.materials.append(bpy.data.materials.new("Ground"))
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.4))
    source = bpy.context.object
    expected_positions = [tuple(vertex.co) for vertex in source.data.vertices]
    mod = source.modifiers.new("Benchmark", "NODES")
    mod.node_group = addon["build_group"]()
    values = addon["modifier_values"](mod)
    identifiers = addon["input_ids"](mod.node_group)
    settings = {"Target": ground, "Displace Ground": False, "Contact Distance": 0.5,
                "Contact Subdivisions": 4, "Match Target Textures": True,
                "Target UV Map": "UVMap"}
    for name, value in settings.items():
        values[identifiers[name]] = value

    samples = []
    for frame in range(8):
        source.location.x = 0.001 * frame
        source.update_tag()
        started = time.perf_counter()
        evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            positions = [tuple(vertex.co) for vertex in mesh.vertices]
            blend = [item.value for item in mesh.attributes[addon["ATTR"]].data]
            uv_present = addon["TARGET_UV_ATTR"] in mesh.uv_layers
        finally:
            evaluated.to_mesh_clear()
        samples.append(time.perf_counter() - started)

        # Check that real contact geometry and texture attributes were evaluated,
        # rather than benchmarking a missing target or an empty modifier output.
        if positions != expected_positions:
            raise RuntimeError("Shading-only mode must preserve the hero's eight vertices")
        if not uv_present or not blend or min(blend) >= 0.01 or max(blend) <= 0.5:
            raise RuntimeError("The expected contact blend and projected UVs were not evaluated")

    print("Blender:", bpy.app.version_string)
    print("Add-on:", addon_path)
    print("Fixture: 60 x 60 ground grid, cube hero, shading only, contact subdivisions 4")
    blend_range = (round(min(blend), 3), round(max(blend), 3))
    print(f"Output: vertices={len(positions)} blend={blend_range} projected_uvs={uv_present}")
    print("Median of six evaluations after two warmups (ms):", round(statistics.median(samples[2:]) * 1000, 3))
    print("All eight evaluations (ms):", [round(sample * 1000, 3) for sample in samples])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addon", nargs="?", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1] / "contact_blend.py")
    benchmark(parser.parse_args().addon.resolve())

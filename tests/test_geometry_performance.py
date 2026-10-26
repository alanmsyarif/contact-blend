# SPDX-License-Identifier: Apache-2.0
"""Geometry quality and lazy-evaluation regressions; run with Blender's Python."""

import pathlib
import sys
import tempfile
import unittest

import bpy
from mathutils import Vector

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class GeometryPerformanceTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=16, y_subdivisions=16)
        self.ground = bpy.context.object
        self.ground_mat = bpy.data.materials.new("Ground")
        self.ground_mat.use_nodes = True
        self.ground.data.materials.append(self.ground_mat)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.4))
        self.source = bpy.context.object
        self.source.data.materials.append(bpy.data.materials.new("Hero"))
        self.mod = self.source.modifiers.new(cb.GROUP, "NODES")
        self.mod.node_group = cb.build_group()
        self.set_value("Target", self.ground)
        self.set_value("Target UV Map", "UVMap")
        self.set_value("Target Material", self.ground_mat)
        self.set_value("Ground Material", self.ground_mat)
        self.ground.hide_set(True)
        self.ground.hide_render = True

    def set_value(self, name, value):
        cb.modifier_values(self.mod)[cb.input_ids(self.mod.node_group)[name]] = value
        self.source.update_tag()

    def snapshot(self):
        evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            return ([tuple(v.co) for v in mesh.vertices],
                    [tuple(loop.normal) for loop in mesh.loops],
                    [tuple(item.uv) for item in mesh.uv_layers[cb.TARGET_UV_ATTR].data])
        finally:
            evaluated.to_mesh_clear()

    def assert_geometry_equal(self, before, after):
        for expected_values, actual_values in zip(before, after):
            self.assertEqual(len(expected_values), len(actual_values))
            for expected, actual in zip(expected_values, actual_values):
                self.assertLess((Vector(expected) - Vector(actual)).length, 1e-6)

    def test_shading_only_ignores_unused_ground_quality_and_detail(self):
        self.set_value("Displace Ground", False)
        self.set_value("Contact Subdivisions", 0)
        before = self.snapshot()
        image = bpy.data.images.new("Height", width=8, height=8)
        image.pixels = [1.0] * (8 * 8 * 4)
        self.set_value("Contact Subdivisions", 4)
        self.set_value("Ground Detail", 0.5)
        self.set_value("Detail Image", image)
        self.set_value("Use Viewport Quality", True)
        self.set_value("Viewport Subdivisions", 1)
        self.assert_geometry_equal(before, self.snapshot())

    def test_viewport_override_changes_density_and_keeps_render_setting(self):
        self.set_value("Contact Subdivisions", 3)
        full = len(self.snapshot()[0])
        self.set_value("Use Viewport Quality", True)
        self.set_value("Viewport Subdivisions", 0)
        low = len(self.snapshot()[0])
        self.set_value("Viewport Subdivisions", 1)
        medium = len(self.snapshot()[0])
        self.assertLess(low, medium)
        self.assertLess(medium, full)
        self.assertEqual(low, len(self.source.data.vertices) + len(self.ground.data.vertices))
        self.assertEqual(cb.modifier_values(self.mod)[cb.input_ids(self.mod.node_group)["Contact Subdivisions"]], 3)

    def test_disabled_override_has_no_effect(self):
        self.set_value("Contact Subdivisions", 2)
        self.set_value("Use Viewport Quality", False)
        self.set_value("Viewport Subdivisions", 0)
        before = self.snapshot()
        self.set_value("Viewport Subdivisions", 5)
        self.assert_geometry_equal(before, self.snapshot())

    def test_existing_group_upgrade_preserves_geometry_and_opts_out(self):
        self.set_value("Contact Subdivisions", 2)
        ng = self.mod.node_group
        group_input = next(n for n in ng.nodes if n.bl_idname == "NodeGroupInput")
        subdivide = next(n for n in ng.nodes if n.bl_idname == "GeometryNodeSubdivideMesh")
        ng.links.new(group_input.outputs["Contact Subdivisions"], subdivide.inputs["Level"])
        # Reconstruct the old single-quality interface, preserving the geometry graph.
        for node in list(ng.nodes):
            if (node.bl_idname == "GeometryNodeIsViewport"
                    or (node.bl_idname == "FunctionNodeBooleanMath" and node.operation == "AND")
                    or (node.bl_idname == "GeometryNodeSwitch" and node.input_type == "INT")):
                ng.nodes.remove(node)
        for socket in list(ng.interface.items_tree):
            if socket.name in {"Viewport Subdivisions", "Use Viewport Quality"}:
                ng.interface.remove(socket)
        ng[cb.VERSION_KEY] = 6
        before = self.snapshot()
        self.assertEqual(cb.build_group(), ng)
        self.assertFalse(cb.modifier_values(self.mod)[cb.input_ids(ng)["Use Viewport Quality"]])
        self.assert_geometry_equal(before, self.snapshot())

    def test_rebuild_preserves_evaluated_modifier_driver(self):
        identifier = cb.input_ids(self.mod.node_group)["Contact Distance"]
        if hasattr(self.mod, "properties"):
            socket = getattr(self.mod.properties.inputs, identifier)
            curve = socket.driver_add("value")
        else:
            curve = self.mod.driver_add(f'["{identifier}"]')
        curve.driver.expression = "0.1"
        bpy.context.scene.frame_set(1)
        narrow_count = len(self.snapshot()[0])
        path = curve.data_path
        self.mod.node_group[cb.VERSION_KEY] = 0
        cb.build_group()
        curve.driver.expression = "0.7"
        bpy.context.scene.frame_set(2)
        self.assertEqual(cb.input_ids(self.mod.node_group)["Contact Distance"], identifier)
        self.assertEqual(curve.data_path, path)
        self.assertAlmostEqual(cb.modifier_values(self.mod)[identifier], 0.7, places=5)
        self.assertGreater(len(self.snapshot()[0]), narrow_count)

    def test_rebuild_preserves_keyframed_ground_deformation(self):
        identifier = cb.input_ids(self.mod.node_group)["Ground Conform"]
        if hasattr(self.mod, "properties"):
            animated = getattr(self.mod.properties.inputs, identifier)
            data_path = "value"
        else:
            animated = self.mod
            data_path = f'["{identifier}"]'
        self.set_value("Ground Conform", 0.0)
        animated.keyframe_insert(data_path=data_path, frame=1)
        self.set_value("Ground Conform", 1.0)
        animated.keyframe_insert(data_path=data_path, frame=10)

        def ground_lowest_z():
            evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                vertices = {i for poly in mesh.polygons
                            if mesh.materials[poly.material_index].name == self.ground_mat.name
                            for i in poly.vertices}
                return min(mesh.vertices[i].co.z for i in vertices)
            finally:
                evaluated.to_mesh_clear()

        bpy.context.scene.frame_set(1)
        flat = ground_lowest_z()
        self.mod.node_group[cb.VERSION_KEY] = 0
        cb.build_group()
        bpy.context.scene.frame_set(10)
        self.assertEqual(cb.input_ids(self.mod.node_group)["Ground Conform"], identifier)
        self.assertAlmostEqual(cb.modifier_values(self.mod)[identifier], 1.0, places=5)
        self.assertLess(ground_lowest_z(), flat - 0.03)

    def test_viewport_quality_does_not_lower_render_quality(self):
        tree = self.ground_mat.node_tree
        tree.nodes.clear()
        geometry = tree.nodes.new("ShaderNodeNewGeometry")
        emission = tree.nodes.new("ShaderNodeEmission")
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(geometry.outputs["Normal"], emission.inputs["Color"])
        tree.links.new(emission.outputs[0], output.inputs["Surface"])
        bpy.ops.object.camera_add(location=(2, -3, 2))
        camera = bpy.context.object
        camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = 2.5
        scene = bpy.context.scene
        scene.camera = camera
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.render.resolution_x = 32
        scene.render.resolution_y = 32
        scene.render.resolution_percentage = 100

        def render(path):
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(str(path), check_existing=False)
            pixels = list(image.pixels)
            bpy.data.images.remove(image)
            return pixels

        with tempfile.TemporaryDirectory(prefix="contact-blend-quality-") as directory:
            self.set_value("Contact Subdivisions", 2)
            reference = render(pathlib.Path(directory) / "full.png")
            self.set_value("Use Viewport Quality", True)
            self.set_value("Viewport Subdivisions", 0)
            preview = render(pathlib.Path(directory) / "preview-setting.png")
            self.assertLess(max(abs(a - b) for a, b in zip(reference, preview)), 0.01)
            self.set_value("Contact Subdivisions", 0)
            coarse = render(pathlib.Path(directory) / "coarse-render.png")
            self.assertGreater(max(abs(a - b) for a, b in zip(reference, coarse)), 0.05,
                               "The render fixture must distinguish low and high subdivision")


if __name__ == "__main__":
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise SystemExit(1)

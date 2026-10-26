# SPDX-License-Identifier: Apache-2.0
"""Texture-coordinate regressions, including a small CPU render comparison."""

import pathlib
import sys
import tempfile
import unittest

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class TextureTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.target = self.plane("Ground", 4)
        self.target.data.uv_layers.active.name = "GroundUV"
        self.source = self.plane("Hero", 1)
        self.source.location = (0.2, 0.3, 0.05)
        for item in self.source.data.uv_layers.active.data:
            item.uv = (0, 0)

    def plane(self, name, size):
        bpy.ops.mesh.primitive_plane_add(size=size)
        obj = bpy.context.object
        obj.name = name
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        obj.data.materials.append(mat)
        return obj

    def apply(self, ground=False):
        bpy.ops.object.select_all(action="DESELECT")
        self.target.select_set(True)
        self.source.select_set(True)
        bpy.context.view_layer.objects.active = self.source
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        mod = cb.find_mod(self.source)
        ids = cb.input_ids(mod.node_group)
        mod[ids["Displace Ground"]] = ground
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        return mod, ids

    def copied_nodes(self, kind):
        return [n for n in self.source.active_material.node_tree.nodes
                if n.type == kind and n.get(cb.GENERATED_KEY) == "target"]

    def test_projected_uvs_follow_transformed_target_and_keep_hero_uvs(self):
        self.target.location = (1, -0.5, 0)
        self.target.rotation_euler.z = 0.4
        self.target.scale = (1.5, 0.8, 1)
        self.source.location = (1.1, -0.3, 0.05)
        self.source.rotation_euler.z = -0.2
        self.source.scale = (0.7, 0.8, 1)
        self.apply()
        for delta in (0, 0.1):
            self.target.location.x += delta
            self.source.update_tag()
            evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                self.assertIn(cb.TARGET_UV_ATTR, mesh.uv_layers)
                uv = mesh.uv_layers[cb.TARGET_UV_ATTR].data
                matrix = self.target.matrix_world.inverted() @ self.source.matrix_world
                for loop in mesh.loops:
                    point = matrix @ mesh.vertices[loop.vertex_index].co
                    expected = Vector((point.x / 4 + 0.5, point.y / 4 + 0.5))
                    self.assertLess((uv[loop.index].uv - expected).length, 1e-5)
                self.assertTrue(all(item.uv.length < 1e-6 for item in mesh.uv_layers["UVMap"].data))
            finally:
                evaluated.to_mesh_clear()

    def test_uvs_are_sampled_after_ground_conform(self):
        bpy.data.objects.remove(self.target, do_unlink=True)
        bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=16, y_subdivisions=16)
        self.target = bpy.context.object
        self.target.data.materials.append(bpy.data.materials["Ground"])
        self.target.data.uv_layers.active.name = "GroundUV"
        bpy.data.objects.remove(self.source, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.4))
        self.source = bpy.context.object
        self.source.data.materials.append(bpy.data.materials["Hero"])
        mod, ids = self.apply(ground=True)
        evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            ground = mod[ids["Ground Material"]]
            triangles = [tri for tri in mesh.loop_triangles
                         if mesh.materials[tri.material_index].name == ground.name]
            bvh = BVHTree.FromPolygons([v.co for v in mesh.vertices],
                                      [tri.vertices for tri in triangles], all_triangles=True)
            original_uvs = mesh.uv_layers["GroundUV"].data
            sampled_uvs = mesh.uv_layers[cb.TARGET_UV_ATTR].data
            count = 0
            for poly in mesh.polygons:
                if mesh.materials[poly.material_index].name == ground.name:
                    continue
                for index in poly.loop_indices:
                    point = mesh.vertices[mesh.loops[index].vertex_index].co
                    nearest, _, tri_index, _ = bvh.find_nearest(point)
                    tri = triangles[tri_index]
                    expected = barycentric_transform(
                        nearest, *(mesh.vertices[i].co for i in tri.vertices),
                        *(Vector((*original_uvs[i].uv, 0)) for i in tri.loops))
                    self.assertLess((sampled_uvs[index].uv - expected.xy).length, 1e-5)
                    count += 1
            self.assertGreater(count, 0)
        finally:
            evaluated.to_mesh_clear()

    def test_implicit_image_uvs_and_named_uv_nodes_are_rebound(self):
        tree = self.target.active_material.node_tree
        tree.nodes.new("ShaderNodeTexImage")
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = "GroundUV"
        normal = tree.nodes.new("ShaderNodeNormalMap")
        self.apply()
        image = self.copied_nodes("TEX_IMAGE")[0]
        self.assertEqual(image.inputs["Vector"].links[0].from_node.uv_map, cb.TARGET_UV_ATTR)
        self.assertTrue(all(n.uv_map == cb.TARGET_UV_ATTR for n in self.copied_nodes("UVMAP")))
        self.assertEqual(self.copied_nodes("NORMAL_MAP")[0].uv_map, cb.TARGET_UV_ATTR)
        self.assertFalse(tree.nodes.get("Image Texture").inputs["Vector"].is_linked)
        self.assertEqual(uv.uv_map, "GroundUV")

    def test_source_coordinates_are_not_rewritten(self):
        tree = self.source.active_material.node_tree
        coord = tree.nodes.new("ShaderNodeTexCoord")
        noise = tree.nodes.new("ShaderNodeTexNoise")
        tree.links.new(coord.outputs["Object"], noise.inputs["Vector"])
        self.apply()
        private = self.source.active_material.node_tree
        self.assertIsNone(private.nodes[coord.name].object)
        self.assertEqual(private.nodes[noise.name].inputs["Vector"].links[0].from_node.name, coord.name)

    def test_object_reference_and_ground_material_use_target_space(self):
        tree = self.target.active_material.node_tree
        coord = tree.nodes.new("ShaderNodeTexCoord")
        noise = tree.nodes.new("ShaderNodeTexNoise")
        tree.links.new(coord.outputs["Object"], noise.inputs["Vector"])
        mod, ids = self.apply(ground=True)
        self.assertEqual(self.copied_nodes("TEX_COORD")[0].object, self.target)
        ground = mod[ids["Ground Material"]]
        self.assertNotEqual(ground, self.target.active_material)
        self.assertEqual(ground.node_tree.nodes[coord.name].object, self.target)
        self.assertIsNone(coord.object)

    def test_explicit_external_object_reference_is_preserved(self):
        empty = bpy.data.objects.new("Mapping origin", None)
        bpy.context.collection.objects.link(empty)
        coord = self.target.active_material.node_tree.nodes.new("ShaderNodeTexCoord")
        coord.object = empty
        self.apply()
        self.assertEqual(self.copied_nodes("TEX_COORD")[0].object, empty)

    def test_nested_groups_are_private_and_refresh_does_not_leak_copies(self):
        group = bpy.data.node_groups.new("Original texture", "ShaderNodeTree")
        group.use_fake_user = True
        self.target.active_material.use_fake_user = True
        group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        image = group.nodes.new("ShaderNodeTexImage")
        output = group.nodes.new("NodeGroupOutput")
        group.links.new(image.outputs["Color"], output.inputs["Color"])
        tree = self.target.active_material.node_tree
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        mod, ids = self.apply()
        for _ in range(3):
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        copied = self.copied_nodes("GROUP")[0].node_tree
        self.assertNotEqual(copied, group)
        self.assertTrue(copied.nodes[image.name].inputs["Vector"].is_linked)
        self.assertFalse(image.inputs["Vector"].is_linked)
        self.assertEqual(sum(bool(m.get(cb.TEXTURE_DATA_KEY)) for m in bpy.data.materials), 1)
        self.assertEqual(sum(bool(g.get(cb.TEXTURE_DATA_KEY)) for g in bpy.data.node_groups), 2)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(any(m.get(cb.TEXTURE_DATA_KEY) for m in bpy.data.materials))
        self.assertFalse(any(g.get(cb.TEXTURE_DATA_KEY) for g in bpy.data.node_groups))
        self.assertIn(group.name, bpy.data.node_groups)

    def test_texture_matching_can_be_disabled(self):
        self.target.active_material.node_tree.nodes.new("ShaderNodeTexImage")
        mod, ids = self.apply()
        mod[ids["Match Target Textures"]] = False
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertFalse(self.copied_nodes("TEX_IMAGE")[0].inputs["Vector"].is_linked)
        self.assertEqual(mod[ids["Ground Material"]], self.target.active_material)
        evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            self.assertNotIn(cb.TARGET_UV_ATTR, mesh.attributes)
        finally:
            evaluated.to_mesh_clear()

    def test_additional_uv_maps_and_missing_uvs_are_reported(self):
        tree = self.target.active_material.node_tree
        node = tree.nodes.new("ShaderNodeUVMap")
        node.uv_map = "SecondaryUV"
        tree.nodes.new("ShaderNodeTexImage")
        mod, ids = self.apply()
        self.assertIn("SecondaryUV", self.source.active_material["cb_texture_warnings"])
        mod[ids["Target UV Map"]] = "MissingUV"
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertIn("UV map is missing", self.source.active_material["cb_texture_warnings"])

    def test_render_matches_target_pattern_for_uv_object_and_generated(self):
        tree = self.target.active_material.node_tree
        tree.nodes.clear()
        emission = tree.nodes.new("ShaderNodeEmission")
        emission_name = emission.name
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(emission.outputs[0], output.inputs["Surface"])
        checker = tree.nodes.new("ShaderNodeTexChecker")
        checker_name = checker.name
        checker.inputs["Scale"].default_value = 3.1
        checker.inputs["Color1"].default_value = (0.1, 0.3, 0.7, 1)
        checker.inputs["Color2"].default_value = (0.8, 0.2, 0.1, 1)
        tree.links.new(checker.outputs["Color"], tree.nodes[emission_name].inputs["Color"])
        coord = tree.nodes.new("ShaderNodeTexCoord")
        coord_name = coord.name
        self.target.location = (0.2, -0.1, 0)
        self.target.rotation_euler.z = 0.35
        self.target.scale = (1.5, 0.7, 1)
        self.source.location = (0.1, 0.1, 0.001)
        self.source.scale = (1.6, 1.6, 1)
        bpy.ops.object.camera_add(location=(0.1, 0.1, 3))
        scene = bpy.context.scene
        scene.camera = bpy.context.object
        scene.camera.data.type = "ORTHO"
        scene.camera.data.ortho_scale = 1.5
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

        with tempfile.TemporaryDirectory(prefix="contact-blend-textures-") as tmp:
            for mapping in ("UV", "Object", "Generated"):
                with self.subTest(mapping=mapping):
                    tree.links.new(tree.nodes[coord_name].outputs[mapping], tree.nodes[checker_name].inputs["Vector"])
                    self.source.hide_render = True
                    self.target.hide_render = False
                    reference = render(pathlib.Path(tmp) / f"{mapping}-reference.png")
                    self.source.hide_render = False
                    self.target.hide_set(False)
                    self.apply()
                    # Shade only the hero; the selected camera region lies inside both planes.
                    self.target.hide_render = True
                    blended = render(pathlib.Path(tmp) / f"{mapping}-blended.png")
                    rgb = [abs(a - b) for i, (a, b) in enumerate(zip(reference, blended)) if i % 4 != 3]
                    self.assertLess(max(rgb), 0.02, f"{mapping}: max error {max(rgb)}")
                    self.assertGreater(max(reference[0::4]) - min(reference[0::4]), 0.2,
                                       "Reference must contain a visible pattern")
                    self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

# SPDX-License-Identifier: Apache-2.0
"""Rendered shader dependency and copy-size regressions."""

import pathlib
import sys
import unittest

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class ShaderPerformanceTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_plane_add(size=4)
        self.target = bpy.context.object
        mat = bpy.data.materials.new("Ground")
        mat.use_nodes = True
        self.target.data.materials.append(mat)
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0.4))
        self.source = bpy.context.object
        mat = bpy.data.materials.new("Hero")
        mat.use_nodes = True
        self.source.data.materials.append(mat)

    def apply(self):
        self.target.select_set(True)
        bpy.context.view_layer.objects.active = self.source
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        return cb.find_mod(self.source)

    def copied_nodes(self):
        return [n for n in self.source.active_material.node_tree.nodes
                if n.get(cb.GENERATED_KEY) == "target"]

    def test_unused_nodes_do_not_expand_the_copied_shader(self):
        tree = self.target.active_material.node_tree
        for _ in range(500):
            tree.nodes.new("ShaderNodeTexNoise")
        original_names = set(tree.nodes.keys())
        mod = self.apply()
        self.assertEqual(len(self.copied_nodes()), 1)
        self.assertEqual(len(self.source.active_material.node_tree.nodes), 5)
        self.assertEqual(set(tree.nodes.keys()), original_names)
        ids = cb.input_ids(mod.node_group)
        self.assertEqual(cb.modifier_values(mod)[ids["Ground Material"]], self.target.active_material)
        for _ in range(2):
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
            self.assertEqual(len(self.copied_nodes()), 1)
        self.assertFalse(any(m.get(cb.TEXTURE_DATA_KEY) for m in bpy.data.materials))

    def test_frame_hierarchy_and_relative_positions_survive_copying(self):
        tree = self.target.active_material.node_tree
        outer = tree.nodes.new("NodeFrame")
        outer.label = "Outer frame"
        inner = tree.nodes.new("NodeFrame")
        inner.label = "Inner frame"
        inner.parent = outer
        shader = tree.nodes["Principled BSDF"]
        shader.parent = inner
        shader.location = (12, -34)
        self.apply()
        copied = next(n for n in self.copied_nodes() if n.type == "BSDF_PRINCIPLED")
        self.assertEqual(copied.parent.label, "Inner frame")
        self.assertEqual(copied.parent.parent.label, "Outer frame")
        self.assertEqual(tuple(copied.location), tuple(shader.location))

    def test_unused_shader_groups_are_not_duplicated(self):
        group = bpy.data.node_groups.new("Unused texture", "ShaderNodeTree")
        group.use_fake_user = True
        image = group.nodes.new("ShaderNodeTexImage")
        node = self.target.active_material.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        self.apply()
        self.assertFalse(any(n.type == "GROUP" for n in self.copied_nodes()))
        self.assertFalse(any(g.get(cb.TEXTURE_DATA_KEY) for g in bpy.data.node_groups))
        self.assertFalse(image.inputs["Vector"].is_linked)

    def test_user_nodes_keep_their_frames_after_refresh_and_removal(self):
        tree = self.target.active_material.node_tree
        outer = tree.nodes.new("NodeFrame")
        inner = tree.nodes.new("NodeFrame")
        inner.parent = outer
        tree.nodes["Principled BSDF"].parent = inner
        self.apply()
        private = self.source.active_material.node_tree
        copied = next(n for n in self.copied_nodes() if n.type == "BSDF_PRINCIPLED")
        frame_name, parent_name = copied.parent.name, copied.parent.parent.name
        user = private.nodes.new("ShaderNodeMath")
        user.name = "User adjustment"
        user.parent = private.nodes[frame_name]
        user.location = (23, -45)
        for action in (bpy.ops.object.contact_blend_update, bpy.ops.object.contact_blend_remove):
            self.assertEqual(action(), {"FINISHED"})
            self.assertEqual(user.parent.name, frame_name)
            self.assertEqual(user.parent.parent.name, parent_name)
            self.assertEqual(tuple(user.location), (23, -45))
            self.assertNotIn(cb.GENERATED_KEY, user.parent)

    def test_recursive_remapping_only_visits_used_group_outputs(self):
        child = bpy.data.node_groups.new("Unused branch", "ShaderNodeTree")
        child.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        image = child.nodes.new("ShaderNodeTexImage")
        output = child.nodes.new("NodeGroupOutput")
        child.links.new(image.outputs["Color"], output.inputs["Color"])
        group = bpy.data.node_groups.new("Two outputs", "ShaderNodeTree")
        for name in ("Used", "Unused"):
            group.interface.new_socket(name=name, in_out="OUTPUT", socket_type="NodeSocketColor")
        used_image = group.nodes.new("ShaderNodeTexImage")
        nested = group.nodes.new("ShaderNodeGroup")
        nested.node_tree = child
        output = group.nodes.new("NodeGroupOutput")
        group.links.new(used_image.outputs["Color"], output.inputs["Used"])
        group.links.new(nested.outputs["Color"], output.inputs["Unused"])
        tree = self.target.active_material.node_tree
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        tree.links.new(node.outputs["Used"], tree.nodes["Principled BSDF"].inputs["Base Color"])
        self.apply()
        copied = next(n.node_tree for n in self.copied_nodes() if n.type == "GROUP")
        self.assertTrue(copied.nodes[used_image.name].inputs["Vector"].is_linked)
        self.assertEqual(copied.nodes[nested.name].node_tree, child)
        self.assertFalse(image.inputs["Vector"].is_linked)
        self.assertEqual(sum(bool(g.get(cb.TEXTURE_DATA_KEY)) for g in bpy.data.node_groups), 2)

    def test_ground_volume_displacement_and_renderer_outputs_are_preserved(self):
        tree = self.target.active_material.node_tree
        noise = tree.nodes.new("ShaderNodeTexNoise")
        volume = tree.nodes.new("ShaderNodeVolumeScatter")
        displacement = tree.nodes.new("ShaderNodeDisplacement")
        output = tree.nodes["Material Output"]
        output.target = "CYCLES"
        tree.links.new(noise.outputs["Color"], volume.inputs["Color"])
        tree.links.new(noise.outputs[0], displacement.inputs["Height"])
        tree.links.new(volume.outputs[0], output.inputs["Volume"])
        tree.links.new(displacement.outputs[0], output.inputs["Displacement"])
        eevee = tree.nodes.new("ShaderNodeOutputMaterial")
        eevee.target = "EEVEE"
        tree.links.new(tree.nodes["Principled BSDF"].outputs[0], eevee.inputs["Surface"])
        mod = self.apply()
        self.assertFalse(any(n.type == "TEX_NOISE" for n in self.copied_nodes()))
        ground = cb.modifier_values(mod)[cb.input_ids(mod.node_group)["Ground Material"]]
        self.assertNotEqual(ground, self.target.active_material)
        copied_tree = ground.node_tree
        self.assertTrue(copied_tree.nodes[output.name].inputs["Volume"].is_linked)
        self.assertTrue(copied_tree.nodes[output.name].inputs["Displacement"].is_linked)
        self.assertEqual(copied_tree.nodes[eevee.name].target, "EEVEE")
        self.assertTrue(copied_tree.nodes[noise.name].inputs["Vector"].is_linked)
        self.assertFalse(noise.inputs["Vector"].is_linked)


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

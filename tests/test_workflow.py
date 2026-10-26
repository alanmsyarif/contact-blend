# SPDX-License-Identifier: Apache-2.0
"""Run with Blender's Python runtime; see README.md for the commands."""

import pathlib
import sys
import tempfile
import unittest

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.target = self.mesh("Ground", (1, 0, 0, 1))
        self.source = self.mesh("Rock", (0, 0, 1, 1))

    def mesh(self, name, color):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = name
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = color
        obj.data.materials.append(mat)
        return obj

    def activate(self, obj):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    def apply(self, src=None, tgt=None):
        src, tgt = src or self.source, tgt or self.target
        self.activate(src)
        tgt.select_set(True)
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        return cb.find_mod(src)

    def test_shared_material_is_untouched(self):
        original = self.source.active_material
        other = self.mesh("Other rock", (0, 1, 0, 1))
        other.active_material = original
        before = len(original.node_tree.nodes)
        self.apply()
        self.assertEqual(other.active_material, original)
        self.assertNotEqual(self.source.active_material, original)
        self.assertEqual(len(original.node_tree.nodes), before)
        self.assertIsNone(cb._material_mix(original))

    def test_linked_mesh_material_assignment_is_untouched(self):
        other = self.source.copy()
        bpy.context.collection.objects.link(other)
        original = other.active_material
        self.apply()
        self.assertEqual(self.source.data, other.data)
        self.assertEqual(other.active_material, original)
        self.assertNotEqual(self.source.active_material, original)
        self.assertEqual(self.source.material_slots[0].link, "OBJECT")

    def test_source_and_target_can_share_a_material(self):
        original = self.target.active_material
        self.source.active_material = original
        self.apply()
        self.assertEqual(self.target.active_material, original)
        self.assertIsNotNone(cb._material_mix(self.source.active_material))
        self.assertIsNone(cb._material_mix(original))

    def test_repeated_apply_updates_one_modifier(self):
        mod = self.apply()
        ids = cb.input_ids(mod.node_group)
        mod[ids["Contact Distance"]] = 0.75
        count = len(self.source.active_material.node_tree.nodes)
        for _ in range(3):
            self.activate(self.source)  # the existing target is hidden
            self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        self.assertEqual(len(self.source.modifiers), 1)
        self.assertAlmostEqual(mod[ids["Contact Distance"]], 0.75)
        self.assertEqual(len(self.source.active_material.node_tree.nodes), count)

    def test_refresh_changes_target_shader_and_keeps_source_edits(self):
        self.apply()
        tree = self.source.active_material.node_tree
        base = cb._material_mix(self.source.active_material).inputs[1].links[0].from_node
        base.inputs["Roughness"].default_value = 0.123
        tree.nodes.new("ShaderNodeMath").name = "User adjustment"
        self.target.active_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0, 1, 0, 1)
        count = len(tree.nodes)
        for _ in range(3):
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        mix = cb._material_mix(self.source.active_material)
        copied = mix.inputs[2].links[0].from_node
        self.assertEqual(tuple(copied.inputs["Base Color"].default_value), (0, 1, 0, 1))
        self.assertAlmostEqual(base.inputs["Roughness"].default_value, 0.123)
        self.assertIn("User adjustment", tree.nodes)
        self.assertEqual(len(tree.nodes), count)

    def test_remove_keeps_material_edits_and_restores_visibility(self):
        original = self.source.active_material
        self.apply()
        material = self.source.active_material
        tree = material.node_tree
        mix = cb._material_mix(material)
        base_name = mix.inputs[1].links[0].from_node.name
        tree.nodes[base_name].inputs["Roughness"].default_value = 0.123
        tree.nodes.new("ShaderNodeMath").name = "User adjustment"
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIsNone(cb.find_mod(self.source))
        self.assertEqual(self.source.active_material, material)
        self.assertIsNone(cb._material_mix(material))
        self.assertEqual(cb._surface(material)[1].node.name, base_name)
        self.assertAlmostEqual(tree.nodes[base_name].inputs["Roughness"].default_value, 0.123)
        self.assertIn("User adjustment", tree.nodes)
        self.assertEqual(len(tree.nodes), 3)
        self.assertNotAlmostEqual(original.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value, 0.123)
        self.assertFalse(self.target.hide_get())
        self.assertFalse(self.target.hide_render)
        self.assertNotIn(cb.VISIBILITY_KEY, self.target)

    def test_user_reused_target_branch_survives_update_and_remove(self):
        self.apply()
        tree = self.source.active_material.node_tree
        mix = cb._material_mix(self.source.active_material)
        copied = mix.inputs[2].links[0].from_node
        copied_name = copied.name
        output = tree.nodes.new("ShaderNodeMixShader")
        output.name = "User shader"
        tree.links.new(copied.outputs["BSDF"], output.inputs[1])
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertIn(copied_name, tree.nodes)
        self.assertEqual(output.inputs[1].links[0].from_node.name, copied_name)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIn(copied_name, tree.nodes)
        self.assertIn("User shader", tree.nodes)

    def test_removal_isolates_a_material_shared_after_blending(self):
        self.apply()
        working = self.source.active_material
        other = self.mesh("Other rock", (0, 1, 0, 1))
        other.active_material = working
        self.activate(self.source)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertEqual(other.active_material, working)
        self.assertIsNotNone(cb._material_mix(other.active_material))
        self.assertIsNone(cb._material_mix(self.source.active_material))

    def test_invalid_target_material_does_not_change_existing_shader(self):
        self.apply()
        working = self.source.active_material
        count = len(working.node_tree.nodes)
        self.target.active_material = None
        self.assertIsNotNone(cb.setup_material(self.source, self.target, refresh=True))
        self.assertEqual(self.source.active_material, working)
        self.assertEqual(len(working.node_tree.nodes), count)
        self.assertIsNotNone(cb._material_mix(working))

    def test_retarget_restores_old_target(self):
        mod = self.apply()
        other = self.mesh("New ground", (0, 1, 0, 1))
        self.activate(self.source)
        mod[cb.input_ids(mod.node_group)["Target"]] = other
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertFalse(self.target.hide_get())
        self.assertFalse(self.target.hide_render)
        self.assertTrue(other.hide_get())
        self.assertTrue(other.hide_render)
        mix = cb._material_mix(self.source.active_material)
        self.assertEqual(tuple(mix.inputs[2].links[0].from_node.inputs["Base Color"].default_value), (0, 1, 0, 1))

    def test_disabling_ground_restores_target(self):
        mod = self.apply()
        mod[cb.input_ids(mod.node_group)["Displace Ground"]] = False
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertFalse(self.target.hide_get())
        self.assertFalse(self.target.hide_render)
        mod[cb.input_ids(mod.node_group)["Displace Ground"]] = True
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertTrue(self.target.hide_get())
        self.assertTrue(self.target.hide_render)

    def test_target_restored_only_after_last_user(self):
        self.apply()
        other = self.mesh("Other rock", (0, 1, 0, 1))
        mod = other.modifiers.new(cb.GROUP, "NODES")
        mod.node_group = cb.build_group()
        mod[cb.input_ids(mod.node_group)["Target"]] = self.target
        self.activate(other)
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.activate(self.source)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertTrue(self.target.hide_get())
        self.assertTrue(self.target.hide_render)
        self.activate(other)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(self.target.hide_get())
        self.assertFalse(self.target.hide_render)

    def test_existing_render_visibility_is_restored(self):
        self.target.hide_render = True
        self.apply()
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertTrue(self.target.hide_render)
        self.assertFalse(self.target.hide_get())

    def test_manual_visibility_changes_are_preserved(self):
        self.target.hide_render = True
        self.apply()
        self.target.hide_render = False
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(self.target.hide_render)

    def test_visibility_is_restored_in_each_recorded_view_layer(self):
        self.apply()
        original_layer = bpy.context.view_layer
        layer = bpy.context.scene.view_layers.new("Other layer")
        with bpy.context.temp_override(view_layer=layer):
            self.activate(self.source)
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
            self.assertTrue(self.target.hide_get(view_layer=layer))
            self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(self.target.hide_get(view_layer=layer))
        self.assertFalse(self.target.hide_get(view_layer=original_layer))

    def test_remove_after_target_socket_is_cleared(self):
        mod = self.apply()
        mod[cb.input_ids(mod.node_group)["Target"]] = None
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(self.target.hide_get())
        self.assertFalse(self.target.hide_render)

    def test_legacy_removal_preserves_untagged_nodes(self):
        self.apply()
        tree = self.source.active_material.node_tree
        for node in tree.nodes:
            if cb.GENERATED_KEY in node:
                del node[cb.GENERATED_KEY]
        cb._material_mix(self.source.active_material).name = cb.LEGACY_NAMES[0]
        count = len(tree.nodes)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIsNone(cb._material_mix(self.source.active_material))
        self.assertEqual(len(tree.nodes), count - 1)
        self.assertEqual(cb._surface(self.source.active_material)[1].node.type, "BSDF_PRINCIPLED")

    def test_state_survives_save_and_reload(self):
        self.apply()
        with tempfile.TemporaryDirectory(prefix="contact-blend-test-") as directory:
            path = str(pathlib.Path(directory) / "workflow.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            source, target = bpy.data.objects["Rock"], bpy.data.objects["Ground"]
            self.activate(source)
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
            self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
            self.assertFalse(target.hide_get())
            self.assertFalse(target.hide_render)
            self.assertIsNone(cb._material_mix(source.active_material))


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

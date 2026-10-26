# SPDX-License-Identifier: Apache-2.0
"""Regressions for the Geometry Nodes modifier API change in Blender 5.2."""

import pathlib
import sys
import unittest

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        self.source = bpy.context.object
        bpy.ops.mesh.primitive_plane_add()
        self.target = bpy.context.object
        bpy.context.view_layer.objects.active = self.source
        self.mod = self.source.modifiers.new(cb.GROUP, "NODES")
        self.mod.node_group = cb.build_group()
        self.ids = cb.input_ids(self.mod.node_group)

    def test_operator_polls_handle_unassigned_and_assigned_target(self):
        self.assertIsNone(cb.mod_target(self.mod))
        self.assertFalse(bpy.ops.object.contact_blend_update.poll())
        self.assertFalse(bpy.ops.object.contact_blend_hide_target.poll())
        self.assertFalse(bpy.ops.object.contact_blend_bump_only.poll())
        cb.modifier_values(self.mod)[self.ids["Target"]] = self.target
        for _ in range(3):
            self.assertTrue(bpy.ops.object.contact_blend_update.poll())
            self.assertTrue(bpy.ops.object.contact_blend_hide_target.poll())
            self.assertFalse(bpy.ops.object.contact_blend_bump_only.poll())
        self.assertNotIn(cb.MODIFIER_STATE_KEY, self.source,
                         "Polling and reading socket values must not create add-on metadata")

    def test_poll_handles_group_without_target_socket(self):
        group = bpy.data.node_groups.new("Incomplete group", "GeometryNodeTree")
        self.mod.node_group = group
        # Exercise mod_target independently of find_mod's group-name filter.
        self.assertIsNone(cb.mod_target(self.mod))
        self.assertIsNone(cb.mod_target(None))
        self.assertFalse(bpy.ops.object.contact_blend_update.poll())

    def test_values_are_written_to_the_runtime_modifier_inputs(self):
        material = bpy.data.materials.new("Material")
        image = bpy.data.images.new("Height", width=2, height=2)
        values = {"Target": self.target, "Contact Distance": 0.75,
                  "Displace Ground": False, "Target UV Map": "Custom UV",
                  "Ground Material": material, "Detail Image": image}
        for name, value in values.items():
            identifier = self.ids[name]
            cb.modifier_values(self.mod)[identifier] = value
            if hasattr(self.mod, "properties"):
                actual = getattr(self.mod.properties.inputs, identifier).value
            else:
                actual = self.mod[identifier]
            self.assertEqual(actual, value, name)
        self.assertEqual(cb.mod_target(self.mod), self.target)

    def test_rebuild_preserves_field_modes_names_and_values(self):
        identifier = self.ids["Ground Conform"]
        cb.modifier_values(self.mod)[identifier] = 0.75
        if hasattr(self.mod, "properties"):
            socket = getattr(self.mod.properties.inputs, identifier)
            socket.type = "ATTRIBUTE"
            socket.attribute_name = "contact_weight"
        else:
            self.mod[identifier + "_use_attribute"] = True
            self.mod[identifier + "_attribute_name"] = "contact_weight"
        expected_options = cb._input_options(self.mod, identifier)
        self.mod.node_group[cb.VERSION_KEY] = 0
        cb.build_group()
        identifier = cb.input_ids(self.mod.node_group)["Ground Conform"]
        self.assertEqual(cb.modifier_values(self.mod)[identifier], 0.75)
        self.assertEqual(cb._input_options(self.mod, identifier), expected_options)

    def test_metadata_survives_modifier_rename_and_is_removed_with_blend(self):
        cb.modifier_values(self.mod)[self.ids["Target"]] = self.target
        cb.modifier_values(self.mod)["cb_probe"] = "remember me"
        self.mod.name = "Renamed blend"
        self.assertEqual(cb.modifier_values(self.mod)["cb_probe"], "remember me")
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertNotIn(cb.MODIFIER_STATE_KEY, self.source)

    def test_two_modifiers_have_independent_bookkeeping(self):
        other = self.source.modifiers.new("Other blend", "NODES")
        other.node_group = self.mod.node_group
        cb.modifier_values(self.mod)["cb_probe"] = self.target
        cb.modifier_values(other)["cb_probe"] = self.source
        self.assertNotEqual(self.mod.persistent_uid, other.persistent_uid)
        self.assertEqual(cb.modifier_values(self.mod)["cb_probe"], self.target)
        self.assertEqual(cb.modifier_values(other)["cb_probe"], self.source)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertEqual(cb.modifier_values(other)["cb_probe"], self.source)

    def test_rebuild_and_remove_release_material_input_references(self):
        material = bpy.data.materials.new("Ground copy")
        cb.modifier_values(self.mod)[self.ids["Ground Material"]] = material
        self.assertEqual(material.users, 1)
        for _ in range(3):
            self.mod.node_group[cb.VERSION_KEY] = 0
            cb.build_group()
            self.assertEqual(material.users, 1)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertEqual(material.users, 0)

    def test_saved_uv_choice_is_kept_when_old_bookkeeping_is_missing(self):
        self.target.data.uv_layers.new(name="Saved UV")
        values = cb.modifier_values(self.mod)
        values[self.ids["Target"]] = self.target
        values[self.ids["Target UV Map"]] = "Saved UV"
        _, uv_name, _ = cb._texture_settings(self.source, self.target)
        self.assertEqual(uv_name, "Saved UV")


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

# SPDX-License-Identifier: Apache-2.0
"""UI workflow regressions; validate RNA bindings without a window or graphics device."""

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


class LayoutRecorder:
    """Record layout calls and reject property paths Blender itself cannot resolve."""
    def __init__(self, records=None, parent=None):
        self.records = [] if records is None else records
        self.parent = parent
        self.enabled = True
        self.active = True
        self.alert = False
        self.use_property_split = False
        self.use_property_decorate = False
        self.operator_context = "INVOKE_DEFAULT"

    def _enabled(self):
        return self.enabled and self.active and (self.parent is None or self.parent._enabled())

    def _child(self, *args, **kwargs):
        return LayoutRecorder(self.records, self)

    row = column = box = split = grid_flow = _child

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", ""), self._enabled()))

    def separator(self, *args, **kwargs):
        pass

    separator_spacer = separator

    def prop(self, data, property, **kwargs):
        data.path_resolve(property)
        self.records.append(("prop", data, property, kwargs.get("text", ""), self._enabled()))

    def prop_search(self, data, property, search_data, search_property, **kwargs):
        data.path_resolve(property)
        collection = search_data.path_resolve(search_property)
        self.records.append(("search", data, property, collection,
                             kwargs.get("text", ""), self._enabled()))

    def operator(self, idname, **kwargs):
        module, name = idname.split(".")
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        self.records.append(("operator", idname, kwargs.get("text", ""), self._enabled()))
        return SimpleNamespace()

    def operator_menu_enum(self, idname, property, **kwargs):
        return self.operator(idname, **kwargs)

    operator_enum = operator_menu_enum

    def template_ID(self, data, property, **kwargs):
        self.prop(data, property, **kwargs)


class UITests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_plane_add(size=4)
        self.target = bpy.context.object
        self.target.name = "Ground"
        self.target.data.uv_layers.active.name = "Ground UV"
        self.target.data.uv_layers.new(name="Height UV")
        self.target.data.materials.append(self.material("Ground material"))
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0.4))
        self.source = bpy.context.object
        self.source.name = "Rock"
        self.source.data.materials.append(self.material("Rock material"))
        self.target.select_set(True)
        bpy.context.view_layer.objects.active = self.source
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        self.mod = cb.find_mod(self.source)
        self.ids = cb.input_ids(self.mod.node_group)

    def material(self, name):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        return material

    def draw(self, panel, ctx=None):
        layout = LayoutRecorder()
        panel.draw(SimpleNamespace(layout=layout), ctx or bpy.context)
        return layout.records

    def values(self):
        values = cb.modifier_values(self.mod)
        return {name: values[identifier] for name, identifier in self.ids.items()
                if name != "Geometry" and identifier in values}

    def test_editability_explains_empty_nonmesh_edit_mode_and_readonly_contexts(self):
        self.assertEqual(cb._editing_problem(bpy.context), "")
        empty = SimpleNamespace(active_object=None, mode="OBJECT", selected_objects=[])
        self.assertTrue(cb._editing_problem(empty))
        camera = SimpleNamespace(type="CAMERA", mode="OBJECT", is_editable=True,
                                 library=None, override_library=None)
        self.assertTrue(cb._editing_problem(SimpleNamespace(active_object=camera, mode="OBJECT")))
        readonly = SimpleNamespace(type="MESH", mode="OBJECT", is_editable=False,
                                   library=None, override_library=None)
        self.assertTrue(cb._editing_problem(SimpleNamespace(active_object=readonly, mode="OBJECT")))
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            self.assertTrue(cb._editing_problem(bpy.context))
            self.assertFalse(bpy.ops.object.contact_blend_quality.poll())
            self.assertFalse(bpy.ops.object.contact_blend_update.poll())
            self.assertFalse(bpy.ops.object.contact_blend_remove.poll())
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")

    def test_quality_presets_preserve_contact_settings_and_do_not_copy_materials(self):
        values = cb.modifier_values(self.mod)
        values[self.ids["Contact Distance"]] = 0.73
        values[self.ids["Surface Offset"]] = 0.007
        source_material = self.source.active_material
        ground_material = values[self.ids["Ground Material"]]
        materials = {material.as_pointer() for material in bpy.data.materials}
        groups = {group.as_pointer() for group in bpy.data.node_groups}
        for preset, viewport, render in (("DRAFT", 0, 2), ("BALANCED", 1, 2), ("FINAL", 2, 3)):
            with self.subTest(preset=preset):
                self.assertEqual(bpy.ops.object.contact_blend_quality(preset=preset), {"FINISHED"})
                self.assertTrue(values[self.ids["Use Viewport Quality"]])
                self.assertEqual(values[self.ids["Viewport Subdivisions"]], viewport)
                self.assertEqual(values[self.ids["Contact Subdivisions"]], render)
                self.assertAlmostEqual(values[self.ids["Contact Distance"]], 0.73, places=5)
                self.assertAlmostEqual(values[self.ids["Surface Offset"]], 0.007, places=5)
                self.assertEqual(self.source.active_material, source_material)
                self.assertEqual(values[self.ids["Ground Material"]], ground_material)
                self.assertEqual({material.as_pointer() for material in bpy.data.materials}, materials)
                self.assertEqual({group.as_pointer() for group in bpy.data.node_groups}, groups)

    def test_invalid_target_types_and_self_targets_cannot_mutate_the_scene(self):
        camera = bpy.data.objects.new("Invalid target", bpy.data.cameras.new("Camera"))
        bpy.context.collection.objects.link(camera)
        bpy.ops.object.select_all(action="DESELECT")
        self.source.select_set(True)
        values = cb.modifier_values(self.mod)
        material = self.source.active_material
        for target in (camera, self.source):
            with self.subTest(target=target.name):
                values[self.ids["Target"]] = target
                before_visibility = (target.hide_get(), target.hide_render,
                                     self.target.hide_get(), self.target.hide_render)
                self.assertFalse(bpy.ops.object.contact_blend.poll())
                self.assertFalse(bpy.ops.object.contact_blend_update.poll())
                self.assertFalse(bpy.ops.object.contact_blend_material.poll())
                for operator in (cb.CONTACTBLEND_OT_add, cb.CONTACTBLEND_OT_update,
                                 cb.CONTACTBLEND_OT_material):
                    result = operator.execute(SimpleNamespace(report=mock.Mock()), bpy.context)
                    self.assertEqual(result, {"CANCELLED"})
                self.assertEqual((target.hide_get(), target.hide_render,
                                  self.target.hide_get(), self.target.hide_render), before_visibility)
                self.assertEqual(self.source.active_material, material)
                self.assertEqual(cb.mod_target(self.mod), target)

    def test_texture_changes_request_refresh_and_clear_the_notice_after_updating(self):
        values = cb.modifier_values(self.mod)
        for name, value in (("Target UV Map", "Height UV"), ("Match Target Textures", False)):
            with self.subTest(setting=name):
                values[self.ids[name]] = value
                issues = cb._blend_issues(self.source, self.mod, bpy.context, detailed=False)
                self.assertTrue(any("update" in message.lower() for _, message in issues), issues)
                self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
                issues = cb._blend_issues(self.source, self.mod, bpy.context, detailed=False)
                self.assertFalse(any("update" in message.lower() for _, message in issues), issues)

    def test_diagnostics_report_visibility_and_displacement_hazards(self):
        self.target.hide_render = False
        material = self.target.active_material
        displacement = material.node_tree.nodes.new("ShaderNodeDisplacement")
        output = material.node_tree.nodes.get("Material Output")
        material.node_tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
        material.displacement_method = "BOTH"
        issues = cb._blend_issues(self.source, self.mod, bpy.context)
        self.assertTrue(all(len(issue) == 2 for issue in issues))
        messages = "\n".join(message for _, message in issues).lower()
        self.assertTrue("render" in messages or "visible" in messages, messages)
        self.assertIn("displac", messages)
        before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
        before_values = self.values()
        self.assertEqual(bpy.ops.object.contact_blend_diagnostics(), {"FINISHED"})
        text = bpy.data.texts.get("Contact Blend Status")
        self.assertIsNotNone(text)
        report = text.as_string()
        self.assertIn(self.source.name, report)
        self.assertIn(self.target.name, report)
        self.assertIn("displac", report.lower())
        self.assertEqual({mesh.as_pointer() for mesh in bpy.data.meshes}, before_meshes)
        self.assertEqual(self.values(), before_values)

    def test_panels_bind_real_rna_properties_without_changing_the_blend(self):
        before_values = self.values()
        before_materials = {material.as_pointer() for material in bpy.data.materials}
        records = []
        with mock.patch.object(cb, "target_displaces", side_effect=AssertionError("Shader scan during redraw")), \
                mock.patch.object(cb, "_surface", side_effect=AssertionError("Shader scan during redraw")):
            for panel in (cb.CONTACTBLEND_PT_panel, cb.CONTACTBLEND_PT_quality,
                          cb.CONTACTBLEND_PT_shading, cb.CONTACTBLEND_PT_detail):
                with self.subTest(panel=panel.__name__):
                    records.extend(self.draw(panel))
        operators = [record[1] for record in records if record[0] == "operator"]
        self.assertIn("object.contact_blend_update", operators)
        self.assertIn("object.contact_blend_remove", operators)
        self.assertIn("object.contact_blend_quality", operators)
        searches = [record for record in records if record[0] == "search"]
        self.assertTrue(searches, "UV selectors should expose available target UV maps")
        self.assertTrue(any("Ground UV" in search[3] for search in searches))
        self.assertEqual(self.values(), before_values)
        self.assertEqual({material.as_pointer() for material in bpy.data.materials}, before_materials)
        self.assertIn("DEFAULT_CLOSED", cb.CONTACTBLEND_PT_detail.bl_options)

    def test_panel_shows_guidance_without_an_active_mesh(self):
        bpy.context.view_layer.objects.active = None
        records = self.draw(cb.CONTACTBLEND_PT_panel)
        labels = " ".join(record[1] for record in records if record[0] == "label").lower()
        self.assertTrue(labels, "The empty panel should explain how to start")
        self.assertTrue(any(word in labels for word in ("mesh", "select", "active")), labels)

    def test_incomplete_blend_data_has_recovery_guidance_and_hides_child_panels(self):
        self.mod.node_group.name = "Saved complete blend"
        broken = bpy.data.node_groups.new(cb.GROUP, "GeometryNodeTree")
        self.mod.node_group = broken
        records = self.draw(cb.CONTACTBLEND_PT_panel)
        labels = " ".join(record[1] for record in records if record[0] == "label").lower()
        self.assertIn("incomplete", labels)
        self.assertTrue(any(record[0] == "operator" and record[1] == "object.contact_blend_remove"
                            for record in records))
        for panel in (cb.CONTACTBLEND_PT_quality, cb.CONTACTBLEND_PT_shading,
                      cb.CONTACTBLEND_PT_detail, cb.CONTACTBLEND_PT_diagnostics):
            self.assertFalse(panel.poll(bpy.context))

    def test_irrelevant_detail_controls_are_disabled_in_shading_only_mode(self):
        values = cb.modifier_values(self.mod)
        values[self.ids["Displace Ground"]] = False
        records = self.draw(cb.CONTACTBLEND_PT_detail)
        properties = [record for record in records if record[0] in {"prop", "search"}]
        self.assertTrue(properties)
        self.assertTrue(all(not record[-1] for record in properties))

    def test_remove_requires_confirmation_when_invoked_from_the_ui(self):
        window_manager = SimpleNamespace(invoke_confirm=mock.Mock(return_value={"RUNNING_MODAL"}))
        context = SimpleNamespace(window_manager=window_manager)
        operator = SimpleNamespace()
        event = SimpleNamespace()
        self.assertEqual(cb.CONTACTBLEND_OT_remove.invoke(operator, context, event), {"RUNNING_MODAL"})
        window_manager.invoke_confirm.assert_called_once()
        self.assertIsNotNone(cb.find_mod(self.source), "Confirmation must precede any removal")


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

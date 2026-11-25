# SPDX-License-Identifier: Apache-2.0
"""Run with Blender's Python runtime; see README.md for the commands."""

import pathlib
import runpy
import sys
import tempfile
import types
import unittest
import warnings

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import contact_blend as cb
import fake_layout


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
        cb.ensure_node_tree(mat)
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

    # --- target shader copies keep node state the generic property loop cannot reach ---
    def test_copied_target_nodes_keep_their_state(self):
        tree = self.target.active_material.node_tree
        bsdf = tree.nodes["Principled BSDF"]
        ramp = tree.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.interpolation = "CONSTANT"
        ramp.color_ramp.color_mode = "HSV"
        ramp.color_ramp.hue_interpolation = "FAR"
        ramp.color_ramp.elements[0].color = (0, 1, 0, 1)
        ramp.color_ramp.elements[1].position = 0.8
        ramp.color_ramp.elements.new(0.4).color = (0.2, 0.3, 0.9, 0.5)
        rgb = tree.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = (0.1, 0.2, 0.3, 1)
        value = tree.nodes.new("ShaderNodeValue")
        value.outputs[0].default_value = 7.0
        curves = tree.nodes.new("ShaderNodeRGBCurve")
        curves.mapping.curves[3].points.new(0.3, 0.9)
        curves.mapping.curves[3].points[1].handle_type = "VECTOR"
        curves.mapping.use_clip = False
        curves.mapping.extend = "EXTRAPOLATED"
        curves.mapping.black_level = (0.1, 0.1, 0.1)
        curves.mapping.update()
        image = tree.nodes.new("ShaderNodeTexImage")
        image.image = bpy.data.images.new("Sequence", 4, 4)
        image.interpolation = "Closest"
        image.extension = "EXTEND"
        image.projection = "BOX"
        image.image_user.frame_duration = 12
        image.image_user.frame_offset = 5
        image.image_user.use_cyclic = True
        image.color_mapping.brightness = 1.5
        image.color_mapping.use_color_ramp = True
        image.color_mapping.color_ramp.elements[0].color = (1, 0, 0, 1)
        image.texture_mapping.translation = (1, 2, 3)
        outer = tree.nodes.new("NodeFrame")
        outer.location = (-900, 300)
        inner = tree.nodes.new("NodeFrame")
        inner.parent = outer
        inner.location_absolute = (-800, 200)
        value.parent = inner
        value.location_absolute = (-700, 100)
        tree.links.new(value.outputs[0], ramp.inputs[0])
        tree.links.new(ramp.outputs[0], bsdf.inputs["Base Color"])
        tree.links.new(rgb.outputs[0], curves.inputs["Color"])
        tree.links.new(curves.outputs[0], bsdf.inputs["Emission Color"])
        self.apply()

        def copy(kind):
            found = [n for n in self.source.active_material.node_tree.nodes
                     if n.type == kind and n.get(cb.GENERATED_KEY) == "target"]
            self.assertEqual(len(found), 1, kind)
            return found[0]

        r = copy("VALTORGB").color_ramp
        self.assertEqual((r.interpolation, r.color_mode, r.hue_interpolation), ("CONSTANT", "HSV", "FAR"))
        self.assertEqual([round(e.position, 4) for e in r.elements], [0.0, 0.4, 0.8])
        self.assertEqual([tuple(round(c, 4) for c in e.color) for e in r.elements],
                         [(0, 1, 0, 1), (0.2, 0.3, 0.9, 0.5), (1, 1, 1, 1)])
        self.assertEqual(tuple(round(c, 4) for c in copy("RGB").outputs[0].default_value), (0.1, 0.2, 0.3, 1))
        self.assertAlmostEqual(copy("VALUE").outputs[0].default_value, 7.0)
        m = copy("CURVE_RGB").mapping
        self.assertEqual([tuple(round(c, 4) for c in p.location) for p in m.curves[3].points],
                         [(0, 0), (0.3, 0.9), (1, 1)])
        self.assertEqual(m.curves[3].points[1].handle_type, "VECTOR")
        self.assertFalse(m.use_clip)
        self.assertEqual(m.extend, "EXTRAPOLATED")
        self.assertAlmostEqual(m.black_level[0], 0.1, places=5)
        im = copy("TEX_IMAGE")
        self.assertEqual(im.image, image.image)
        self.assertEqual((im.interpolation, im.extension, im.projection), ("Closest", "EXTEND", "BOX"))
        self.assertEqual((im.image_user.frame_duration, im.image_user.frame_offset,
                          im.image_user.use_cyclic), (12, 5, True))
        self.assertAlmostEqual(im.color_mapping.brightness, 1.5)
        self.assertTrue(im.color_mapping.use_color_ramp)
        self.assertEqual(tuple(im.color_mapping.color_ramp.elements[0].color), (1, 0, 0, 1))
        self.assertEqual(tuple(im.texture_mapping.translation), (1, 2, 3))
        frames = [n for n in self.source.active_material.node_tree.nodes
                  if n.type == "FRAME" and n.get(cb.GENERATED_KEY) == "target"]
        v = copy("VALUE")
        self.assertIn(v.parent, frames)
        self.assertIn(v.parent.parent, frames)
        self.assertIsNone(v.parent.parent.parent)
        # every copied node is shifted by one common offset, frame children included
        shift = v.location_absolute - value.location_absolute
        for src, dst in ((inner, v.parent), (outer, v.parent.parent), (ramp, copy("VALTORGB"))):
            self.assertLess((dst.location_absolute - src.location_absolute - shift).length, 1e-3)

    # --- Material.use_nodes is deprecated on 5.x ---
    def test_no_use_nodes_deprecation_warnings(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.apply()
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
            cb.migrate_names()
            cb.target_displaces(cb.find_mod(self.source))
            self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        hits = [str(w.message) for w in caught if "use_nodes" in str(w.message)]
        self.assertEqual(hits, [])

    # --- every usable material slot is blended ---
    def add_slot(self, obj, mat):
        obj.data.materials.append(mat)
        return len(obj.material_slots) - 1

    def test_every_usable_material_slot_is_blended(self):
        rock_a = self.source.active_material
        rock_b = bpy.data.materials.new("Moss")
        cb.ensure_node_tree(rock_b)
        unlinked = bpy.data.materials.new("No surface")
        tree = cb.ensure_node_tree(unlinked)
        for link in list(tree.links):
            tree.links.remove(link)
        b_index = self.add_slot(self.source, rock_b)
        self.add_slot(self.source, None)
        self.add_slot(self.source, unlinked)
        for poly in self.source.data.polygons[:3]:
            poly.material_index = b_index
        self.source.active_material_index = 0
        self.apply()
        slots = self.source.material_slots
        blended = [slots[0].material, slots[1].material]
        for mat, original in zip(blended, (rock_a, rock_b)):
            self.assertNotEqual(mat, original)
            self.assertTrue(mat.get(cb.ISOLATED_KEY))
            self.assertIsNotNone(cb._material_mix(mat), mat.name)
            self.assertIn("cb_texture_warnings", mat)
            self.assertIsNone(cb._material_mix(original))
        for mat in blended:
            self.assertIsNotNone(cb.breakup_node(mat), mat.name)
        self.assertIsNone(slots[2].material)
        self.assertEqual(slots[3].material, unlinked)
        self.assertIsNone(cb._material_mix(unlinked))
        counts = [len(m.node_tree.nodes) for m in blended]
        for _ in range(3):
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertEqual([len(m.node_tree.nodes) for m in blended], counts, "refresh accumulated")
        self.assertEqual([s.material for s in slots[:2]], blended)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        for mat in blended:
            self.assertIsNone(cb._material_mix(mat))
            self.assertEqual(cb._surface(mat)[1].node.type, "BSDF_PRINCIPLED")
            self.assertIsNone(cb.breakup_node(mat))

    def test_no_usable_source_slot_is_an_error(self):
        mat = self.source.active_material
        for link in list(mat.node_tree.links):
            mat.node_tree.links.remove(link)
        err = cb.setup_material(self.source, self.target)
        self.assertIn("no node material with a linked Surface", err)
        self.assertEqual(self.source.active_material, mat)

    # --- edge breakup ---
    def test_breakup_node_is_inserted_and_keeps_user_values(self):
        self.apply()
        mat = self.source.active_material
        tree = mat.node_tree
        node = cb.breakup_node(mat)
        self.assertIsNotNone(node)
        self.assertEqual(node.get(cb.GENERATED_KEY), "breakup")
        self.assertEqual(node.node_tree.get(cb.GENERATED_KEY), "breakup")
        mix = cb._material_mix(mat)
        self.assertEqual(mix.inputs[0].links[0].from_node, node)
        attr = node.inputs["Blend"].links[0].from_node
        self.assertEqual((attr.type, attr.attribute_name), ("ATTRIBUTE", cb.ATTR))
        self.assertEqual(node.inputs["Breakup"].default_value, 0.0)
        self.assertEqual(node.inputs["Sharpness"].default_value, 0.0)
        # noise is in the hero's object space, not on its UVs
        group = node.node_tree
        noise = next(n for n in group.nodes if n.type == "TEX_NOISE")
        coord = noise.inputs["Vector"].links[0]
        self.assertEqual((coord.from_node.type, coord.from_socket.name), ("TEX_COORD", "Object"))
        self.assertIsNone(coord.from_node.object)
        node.inputs["Breakup"].default_value = 0.7
        node.inputs["Sharpness"].default_value = 0.3
        node.inputs["Noise Scale"].default_value = 12.0
        name, count = node.name, len(tree.nodes)
        for _ in range(2):
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        node = cb.breakup_node(mat)
        self.assertEqual(node.name, name)
        self.assertEqual(len(tree.nodes), count)
        self.assertAlmostEqual(node.inputs["Breakup"].default_value, 0.7, places=5)
        self.assertAlmostEqual(node.inputs["Sharpness"].default_value, 0.3, places=5)
        self.assertAlmostEqual(node.inputs["Noise Scale"].default_value, 12.0, places=5)
        self.assertEqual(sum(1 for g in bpy.data.node_groups if g.get(cb.GENERATED_KEY) == "breakup"), 1)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIsNone(cb.breakup_node(mat))
        self.assertFalse(any(g.get(cb.GENERATED_KEY) == "breakup" for g in bpy.data.node_groups))

    def test_existing_blend_gets_breakup_on_update(self):
        self.apply()
        mat = self.source.active_material
        tree = mat.node_tree
        # rewind to a material wired by an earlier release: Attribute straight into the mix,
        # untagged as legacy files are
        node = cb.breakup_node(mat)
        attr = node.inputs["Blend"].links[0].from_node
        tree.nodes.remove(node)
        del attr[cb.GENERATED_KEY]
        tree.links.new(attr.outputs["Fac"], cb._material_mix(mat).inputs[0])
        self.assertIsNone(cb.setup_material(self.source, self.target))   # plain re-run: no change
        self.assertIsNone(cb.breakup_node(mat))
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        node = cb.breakup_node(mat)
        self.assertIsNotNone(node)
        self.assertEqual(node.inputs["Blend"].links[0].from_node, attr)
        self.assertEqual(cb._material_mix(mat).inputs[0].links[0].from_node, node)

    # --- groups are ours by stamp, not by name ---
    def test_appended_blend_with_suffixed_group_is_found_and_upgraded(self):
        mod = self.apply()
        ids = cb.input_ids(mod.node_group)
        mod[ids["Contact Distance"]] = 0.777
        mod.node_group[cb.VERSION_KEY] = cb.GROUP_VERSION - 1
        with tempfile.TemporaryDirectory(prefix="contact-blend-test-") as directory:
            path = str(pathlib.Path(directory) / "library.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.read_factory_settings(use_empty=True)
            rock, ground = self.mesh("Rock", (0, 0, 1, 1)), self.mesh("Ground", (1, 0, 0, 1))
            self.apply(rock, ground)
            own = cb.find_mod(rock).node_group
            n_nodes = len(own.nodes)
            bpy.ops.wm.append(filepath=f"{path}/Object/Rock", directory=f"{path}/Object/", filename="Rock")
        appended = bpy.data.objects["Rock.001"]
        mod = cb.find_mod(appended)
        self.assertIsNotNone(mod, "appended blend is invisible to the panel")
        self.assertNotEqual(mod.node_group, own)
        self.assertNotEqual(mod.node_group.name, cb.GROUP)
        self.assertEqual(cb.group_version(mod.node_group), cb.GROUP_VERSION - 1)
        self.assertEqual(cb.build_group(), own, "new blends must keep using the existing group")
        self.assertEqual(cb.group_version(mod.node_group), cb.GROUP_VERSION, "stale group not upgraded")
        self.assertEqual(len(mod.node_group.nodes), n_nodes)
        self.assertAlmostEqual(mod[cb.input_ids(mod.node_group)["Contact Distance"]], 0.777, places=5)
        self.assertEqual(len(cb._our_groups()), 2, "groups were merged or lost")
        # the diagnostic must see it too
        if appended.name not in bpy.context.scene.collection.all_objects:
            bpy.context.scene.collection.objects.link(appended)
        bpy.context.view_layer.update()
        bpy.context.view_layer.objects.active = appended
        runpy.run_path(str(pathlib.Path(cb.__file__).with_name("contact_blend_diag.py")))
        report = bpy.data.texts["Contact Blend Report"].as_string()
        self.assertNotIn("has no Contact Blend modifier", report)
        self.assertIn(f"group    : {mod.node_group.name}", report)

    def test_linked_blend_is_found_and_its_read_only_group_left_alone(self):
        mod = self.apply()
        mod.node_group[cb.VERSION_KEY] = cb.GROUP_VERSION - 1
        with tempfile.TemporaryDirectory(prefix="contact-blend-test-") as directory:
            path = str(pathlib.Path(directory) / "library.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.ops.wm.link(filepath=f"{path}/Object/Rock", directory=f"{path}/Object/", filename="Rock")
            linked = bpy.data.objects["Rock"]
            self.assertIsNotNone(linked.library)
            self.assertIsNotNone(cb.find_mod(linked), "linked blend is invisible to the panel")
            ng = cb.build_group()
            self.assertIsNone(ng.library, "new blends must not use a read-only linked group")
            self.assertEqual(cb.group_version(ng), cb.GROUP_VERSION)
            self.assertEqual(cb.group_version(cb.find_mod(linked).node_group), cb.GROUP_VERSION - 1)

    def test_load_upgrades_ours_and_leaves_a_same_named_user_group_alone(self):
        mod = self.apply()
        ours = mod.node_group
        ours.name = "Contact Blend.001"
        ours[cb.VERSION_KEY] = cb.GROUP_VERSION - 1
        user = bpy.data.node_groups.new(cb.GROUP, "GeometryNodeTree")
        user.use_fake_user = True
        user.nodes.new("NodeGroupInput")
        with tempfile.TemporaryDirectory(prefix="contact-blend-test-") as directory:
            path = str(pathlib.Path(directory) / "clash.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        user, ours = bpy.data.node_groups[cb.GROUP], bpy.data.node_groups["Contact Blend.001"]
        self.assertEqual(cb.group_version(ours), cb.GROUP_VERSION, "load did not upgrade our group")
        self.assertEqual(len(user.nodes), 1, "load rebuilt the user's group")
        self.assertIsNone(cb.group_version(user))
        self.assertEqual(cb.find_mod(bpy.data.objects["Rock"]).node_group, ours)
        self.assertEqual(cb.build_group(), ours)

    # --- targets outside the current view layer ---
    def test_target_outside_view_layer_does_not_break_ui_or_operators(self):
        mod = self.apply()
        loose = bpy.data.objects.new("Loose ground", self.target.data)   # in no scene at all
        mod[cb.input_ids(mod.node_group)["Target"]] = loose
        self.activate(self.source)
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertTrue(loose.hide_render, "operator stopped before hiding from render")
        self.assertIsNotNone(cb._material_mix(self.source.active_material))
        self.assertFalse(self.target.hide_get(), "previous target not restored on retarget")

        cb.CONTACTBLEND_PT_panel.draw(types.SimpleNamespace(layout=fake_layout.Layout()), bpy.context)
        for op in (cb.CONTACTBLEND_OT_update, cb.CONTACTBLEND_OT_hide_target, cb.CONTACTBLEND_OT_remove):
            op.poll(bpy.context)
        self.assertIsNone(cb.target_leaks(mod))
        self.assertTrue(cb.is_hidden(loose))
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertFalse(loose.hide_render)

    # --- distances fitted to the object's size ---
    def fitted(self, size):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(5, 5, size / 2))
        obj = bpy.context.object
        obj.data.materials.append(self.source.active_material)
        mod = self.apply(obj)
        ids = cb.input_ids(mod.node_group)
        return obj, mod, ids

    def test_new_blend_fits_distances_to_object_size(self):
        for size, contact, offset in ((0.05, 0.05 / 3, 0.0002), (0.5, 0.5 / 3, 0.002), (20, 20 / 3, 0.05)):
            obj, mod, ids = self.fitted(size)
            self.assertAlmostEqual(mod[ids["Contact Distance"]], contact, places=5)
            self.assertAlmostEqual(mod[ids["Material Blend Distance"]], contact, places=5)
            self.assertAlmostEqual(mod[ids["Normal Blend Distance"]], contact * 2 / 3, places=5)
            self.assertAlmostEqual(mod[ids["Surface Offset"]], offset, places=6)
            self.target.hide_set(False)
        # world size, not mesh size: a 1 m cube scaled 3x is a 3 m object
        obj, mod, ids = self.fitted(1)
        obj.scale = (3, 3, 3)
        self.assertEqual(bpy.ops.object.contact_blend_fit(), {"FINISHED"})
        self.assertAlmostEqual(mod[ids["Contact Distance"]], 1.0, places=5)

    def test_user_distances_survive_reapply_and_update_until_fit(self):
        obj, mod, ids = self.fitted(0.5)
        mod[ids["Contact Distance"]] = 0.9
        mod[ids["Surface Offset"]] = 0.01
        self.activate(obj)
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertAlmostEqual(mod[ids["Contact Distance"]], 0.9, places=5)
        self.assertAlmostEqual(mod[ids["Surface Offset"]], 0.01, places=5)
        self.assertEqual(bpy.ops.object.contact_blend_fit(), {"FINISHED"})
        self.assertAlmostEqual(mod[ids["Contact Distance"]], 0.5 / 3, places=5)
        self.assertAlmostEqual(mod[ids["Surface Offset"]], 0.002, places=5)


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

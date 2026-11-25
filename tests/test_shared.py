# SPDX-License-Identifier: Apache-2.0
"""Shared ground: many objects on one ground, selection resolution, panel states, diagnostics."""

import collections
import pathlib
import runpy
import sys
import tempfile
import types
import unittest

import bpy
from mathutils.kdtree import KDTree

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import contact_blend as cb
import fake_layout
from test_geometry import grid

RIM = 4.0          # the test ground is 8 m across


class SharedBase(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.ground = grid("Ground", 8, 80)
        self.material(self.ground, "Ground")

    def material(self, obj, name):
        mat = bpy.data.materials.new(name)
        cb.ensure_node_tree(mat)
        obj.data.materials.append(mat)
        return mat

    def rock(self, name, x, y, radius=0.4):
        # sunk 40% of its radius, so each one has a real intersection to close
        bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=(x, y, radius * 0.6))
        obj = bpy.context.object
        obj.name = name
        self.material(obj, name)
        return obj

    def select(self, objs, active=None):
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)

    def blend(self, *heroes):
        """Blend `heroes` into the ground with the Ground picker, as an artist would."""
        bpy.context.scene.contact_blend_ground = self.ground
        self.select(heroes)
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        bpy.context.scene.contact_blend_ground = None
        return cb.find_proxy(self.ground)

    def evaluated(self, obj):
        obj.update_tag()
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        self.addCleanup(ev.to_mesh_clear)
        return ev.to_mesh()

    def ground_points(self):
        proxy = cb.find_proxy(self.ground)
        self.assertIsNotNone(proxy, "no shared ground")
        me = self.evaluated(proxy)
        return me, [proxy.matrix_world @ v.co for v in me.vertices]

    def dip(self, points, obj, radius=0.6):
        near = [p.z for p in points if (p.xy - obj.matrix_world.translation.xy).length < radius]
        self.assertTrue(near, f"no ground near {obj.name}")
        return min(near) - self.ground.matrix_world.translation.z

    def cut_vertices(self, me, points):
        seen = collections.Counter()
        for p in me.polygons:
            vs = list(p.vertices)
            for a, b in zip(vs, vs[1:] + vs[:1]):
                seen[(min(a, b), max(a, b))] += 1
        local = [v.co for v in me.vertices]
        return {i for e, c in seen.items() if c == 1 for i in e
                if abs(local[i].x) < RIM - 1e-4 and abs(local[i].y) < RIM - 1e-4}


class SharedGroundTests(SharedBase):
    def test_two_objects_share_one_ground(self):
        self.check_one_shared_ground(2)

    def test_five_objects_share_one_ground(self):
        self.check_one_shared_ground(5)

    def check_one_shared_ground(self, count):
        spots = [(-2.5, -2.5), (2.5, -2.5), (0, 0), (-2.5, 2.5), (2.5, 2.5)]
        rocks = [self.rock(f"Rock{i}", *spots[i]) for i in range(count)]
        proxy = self.blend(*rocks)
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy.parent, self.ground)
        self.assertEqual({o.name for o in cb.heroes_collection(self.ground).objects},
                         {r.name for r in rocks})
        coll = cb.heroes_collection(self.ground)
        self.assertFalse(any(coll in s.collection.children_recursive for s in bpy.data.scenes),
                         "the helper collection must not show in the scene")
        # exactly one ground surface draws: the proxy. The target is hidden and every
        # object carries only its own mesh.
        depsgraph = bpy.context.evaluated_depsgraph_get()
        drawing = []
        for inst in depsgraph.object_instances:
            obj = inst.object.original
            if obj.type != "MESH" or not obj.visible_get() or obj.hide_render:
                continue
            me = inst.object.to_mesh()
            if any(m and m.name.startswith("Ground") for m in me.materials) and me.polygons:
                drawing.append(obj)
            inst.object.to_mesh_clear()
        self.assertEqual(drawing, [proxy])
        for r in rocks:
            self.assertEqual(len(self.evaluated(r).vertices), len(r.data.vertices))
        me, pts = self.ground_points()
        for r in rocks:
            self.assertLess(self.dip(pts, r), -0.03, f"no crater under {r.name}")
        reach = max(cb.socket(cb.find_mod(r), "Contact Distance") for r in rocks)
        far = [p for p in pts if all((p - r.location).length - 0.4 > reach + 0.05 for r in rocks)]
        self.assertGreater(len(far), 1000)
        self.assertLess(max(abs(p.z) for p in far), 1e-6, "ground moved far from every object")
        cut = self.cut_vertices(me, pts)
        self.assertTrue(cut, "no subdivision cut; the test is not biting")
        self.assertLess(max(abs(pts[i].z) for i in cut), 1e-6, "the cut moved: it will crack")
        # no duplicate vertices along the cut either (crease ring)
        keys = collections.Counter(tuple(round(c, 6) for c in p) for p in pts)
        self.assertEqual(max(keys.values()), 1, "unwelded vertices on the shared ground")

    def test_each_object_keeps_its_own_contact_distance(self):
        big, small = self.rock("Big", -2, 0), self.rock("Small", 2, 0)
        self.blend(big, small)
        for obj, contact in ((big, 0.6), (small, 0.12)):
            cb.set_socket(cb.find_mod(obj), "Contact Distance", contact)
            obj.update_tag()
        me, pts = self.ground_points()

        def surface_distance(p, obj):
            return (p - obj.location).length - 0.4

        # Conform only moves the ground towards the object, so a vertex found beyond an object's
        # radius started out even farther: it must not have moved at all.
        beyond_small = [p for p in pts if surface_distance(p, small) > 0.12 + 1e-3
                        and (p.xy - small.location.xy).length < 1.5]
        self.assertGreater(len(beyond_small), 100)
        self.assertLess(max(abs(p.z) for p in beyond_small), 1e-6, "small object used a larger radius")
        # ...while the big one pulls ground well past the small one's radius
        mid_big = [p for p in pts if 0.2 < surface_distance(p, big) < 0.5]
        self.assertTrue(any(abs(p.z) > 1e-4 for p in mid_big), "big object's radius not applied")

    def test_ground_follows_a_moved_scaled_rotated_target(self):
        rock = self.rock("Rock", 0.3, 0.2)
        proxy = self.blend(rock)
        self.ground.location = (0.4, -0.3, 0.25)
        self.ground.rotation_euler.z = 0.3
        self.ground.scale = (1.5, 1.5, 1.5)
        rock.location.z += 0.25
        bpy.context.view_layer.update()
        self.assertLess((proxy.matrix_world - self.ground.matrix_world).to_translation().length +
                        max(abs(a - b) for ra, rb in zip(proxy.matrix_world, self.ground.matrix_world)
                            for a, b in zip(ra, rb)), 1e-6)
        me, pts = self.ground_points()
        self.assertLess(self.dip(pts, rock), -0.03, "crater did not follow the object")
        far = [p for p in pts if (p.xy - rock.location.xy).length > 1.5]
        self.assertLess(max(abs(p.z - 0.25) for p in far), 1e-5)

    def test_removing_objects_heals_their_craters_and_finally_cleans_up(self):
        a, b = self.rock("A", -2, 0), self.rock("B", 2, 0)
        self.blend(a, b)
        op = bpy.ops.object.contact_blend_remove(object_name="A")
        self.assertEqual(op, {"FINISHED"})
        self.assertEqual([o.name for o in cb.heroes_collection(self.ground).objects], ["B"])
        me, pts = self.ground_points()
        near_a = [p for p in pts if (p.xy - a.location.xy).length < 1.0]
        self.assertLess(max(abs(p.z) for p in near_a), 1e-6, "removed object's crater remained")
        self.assertLess(self.dip(pts, b), -0.03)
        self.assertTrue(self.ground.hide_render)
        self.assertEqual(bpy.ops.object.contact_blend_remove(object_name="B"), {"FINISHED"})
        self.assertIsNone(cb.find_proxy(self.ground))
        self.assertIsNone(cb.heroes_collection(self.ground))
        self.assertFalse(any(m.name.endswith("· Contact Blend") for m in bpy.data.meshes))
        self.assertFalse(self.ground.hide_render)
        self.assertFalse(self.ground.hide_get())

    def test_duplicated_object_joins_the_shared_ground(self):
        rock = self.rock("Rock", -1, 0)
        self.blend(rock)
        self.select([rock])
        self.assertEqual(bpy.ops.object.duplicate(), {"FINISHED"})
        copy = bpy.context.object
        self.assertNotEqual(copy, rock)
        copy.location.x = 2
        bpy.context.view_layer.update()       # the membership handler runs on depsgraph updates
        members = {o.name for o in cb.heroes_collection(self.ground).objects}
        self.assertEqual(members, {rock.name, copy.name})
        me, pts = self.ground_points()
        self.assertLess(self.dip(pts, copy), -0.03, "duplicate did not crater the ground")
        self.assertLess(self.dip(pts, rock), -0.03)

    def test_deleted_proxy_is_reported_and_recreated_by_update(self):
        rock = self.rock("Rock", 0, 0)
        proxy = self.blend(rock)
        bpy.data.objects.remove(proxy, do_unlink=True)
        self.assertIn("Shared ground is missing", cb.out_of_sync(rock, cb.find_mod(rock)))
        self.select([rock])
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertEqual(cb.out_of_sync(rock, cb.find_mod(rock)), [])
        me, pts = self.ground_points()
        self.assertLess(self.dip(pts, rock), -0.03)

    def test_ground_settings_live_on_the_proxy_and_reach_every_object(self):
        a, b = self.rock("A", -2, 0), self.rock("B", 2, 0)
        proxy = self.blend(a, b)
        gmod = cb.ground_mod(proxy)
        cb.set_socket(gmod, "Contact Subdivisions", 3)
        cb.set_socket(gmod, "Ground Detail", 0.05)
        proxy.update_tag()
        bpy.context.view_layer.update()
        for obj in (a, b):
            self.assertEqual(cb.socket(cb.find_mod(obj), "Contact Subdivisions"), 3)
            self.assertAlmostEqual(cb.socket(cb.find_mod(obj), "Ground Detail"), 0.05, places=6)
        # an image set on the ground reaches the objects, and so does clearing it
        image = bpy.data.images.new("Height", 4, 4)
        for value in (image, None):
            cb.set_socket(gmod, "Detail Image", value)
            proxy.update_tag()          # what editing it in the UI does
            bpy.context.view_layer.update()
            for obj in (a, b):
                self.assertEqual(cb.socket(cb.find_mod(obj), "Detail Image"), value)

    def test_save_and_reload_round_trip(self):
        a, b = self.rock("A", -2, 0), self.rock("B", 2, 0)
        self.blend(a, b)
        with tempfile.TemporaryDirectory(prefix="contact-blend-shared-") as directory:
            path = str(pathlib.Path(directory) / "shared.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        self.ground = bpy.data.objects["Ground"]
        a, b = bpy.data.objects["A"], bpy.data.objects["B"]
        proxy = cb.find_proxy(self.ground)
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy.parent, self.ground)
        self.assertEqual({o.name for o in cb.heroes_collection(self.ground).objects}, {"A", "B"})
        me, pts = self.ground_points()
        self.assertLess(self.dip(pts, a), -0.03)
        self.assertLess(self.dip(pts, b), -0.03)
        self.select([a, b])
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIsNone(cb.find_proxy(self.ground))
        self.assertFalse(self.ground.hide_render)

    def test_ground_passes_through_when_no_object_may_move_it(self):
        a, b = self.rock("A", -2, 0), self.rock("B", 2, 0)
        self.blend(a, b)
        for obj in (a, b):
            cb.set_socket(cb.find_mod(obj), "Ground Conform", 0.0)
            obj.update_tag()
        me, pts = self.ground_points()
        # the target itself, untouched: not even subdivided
        self.assertEqual(len(me.vertices), len(self.ground.data.vertices))
        self.assertLess(max(abs(p.z) for p in pts), 1e-9)
        cb.set_socket(cb.find_mod(b), "Ground Conform", 1.0)
        b.update_tag()
        me, pts = self.ground_points()
        self.assertGreater(len(me.vertices), len(self.ground.data.vertices))
        self.assertLess(self.dip(pts, b), -0.03)
        near_a = [p for p in pts if (p.xy - a.location.xy).length < 1.0]
        self.assertLess(max(abs(p.z) for p in near_a), 1e-6, "conform 0 still moved the ground")

    def test_reapplying_with_another_ground_retargets(self):
        rock = self.rock("Rock", 0, 0)
        self.blend(rock)
        other = grid("Other", 4, 40)
        other.location.x = 10
        rock.location.x = 10
        self.select([other, rock], active=rock)
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        self.assertEqual(cb.mod_target(cb.find_mod(rock)), other)
        self.assertIsNone(cb.find_proxy(self.ground), "old ground kept its shared ground")
        self.assertFalse(self.ground.hide_render, "old ground not shown again")
        self.assertIsNotNone(cb.find_proxy(other))
        self.assertTrue(other.hide_render)

    def test_operators_act_on_the_whole_selection(self):
        rocks = [self.rock(f"Rock{i}", -2 + 2 * i, 0, radius=0.2 + 0.1 * i) for i in range(3)]
        self.blend(*rocks)
        for r in rocks:
            cb.set_socket(cb.find_mod(r), "Contact Distance", 2.0)
        self.select(rocks)
        self.assertEqual(bpy.ops.object.contact_blend_fit(), {"FINISHED"})
        for r in rocks:
            self.assertAlmostEqual(cb.socket(cb.find_mod(r), "Contact Distance"),
                                   cb.object_width(r) / 3, places=5)
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        for r in rocks:
            self.assertEqual(cb.out_of_sync(r, cb.find_mod(r)), [])
        self.select(rocks[:2])
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertEqual([r for r in rocks if cb.find_mod(r)], rocks[2:])
        self.assertIsNotNone(cb.find_proxy(self.ground))


class ResolutionTests(SharedBase):
    def test_resolution_rules_and_labels(self):
        rock, pebble, stone = self.rock("Rock", -2, 0), self.rock("Pebble", 0, 0), self.rock("Stone", 2, 0)
        self.select([])
        r = cb.resolve(bpy.context)
        self.assertIsNone(r.target)
        self.assertIn("Select a ground", r.reason)
        # rule 2: one other mesh selected -> it is the ground
        self.select([self.ground, rock], active=rock)
        r = cb.resolve(bpy.context)
        self.assertEqual((r.target, r.heroes, r.label), (self.ground, [rock], "Blend 'Rock' into 'Ground'"))
        # rule 5: three plain meshes and no ground yet
        self.select([self.ground, rock, pebble], active=rock)
        r = cb.resolve(bpy.context)
        self.assertIsNone(r.target)
        self.assertIn("pick a Ground", r.reason)
        # rule 1: the picker wins, and picks up every selected mesh
        bpy.context.scene.contact_blend_ground = self.ground
        self.select([self.ground, rock, pebble, stone], active=pebble)
        r = cb.resolve(bpy.context)
        self.assertEqual(r.target, self.ground)
        self.assertEqual(r.heroes, [pebble, rock, stone])
        self.assertEqual(r.label, "Blend 3 objects into 'Ground'")
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        bpy.context.scene.contact_blend_ground = None
        proxy = cb.find_proxy(self.ground)
        # afterwards the objects stay selected, one of them active, the ground deselected
        self.assertEqual(set(bpy.context.selected_objects), {rock, pebble, stone})
        self.assertEqual(bpy.context.active_object, pebble)
        # rule 2 through the proxy: the visible ground stands for the real target
        extra = self.rock("Extra", 0, 2.5)
        self.select([proxy, extra], active=extra)
        r = cb.resolve(bpy.context)
        self.assertEqual((r.target, r.heroes), (self.ground, [extra]))
        # rule 3: one selected object is already a ground, the rest are new objects
        extra2 = self.rock("Extra2", 0, -2.5)
        self.select([proxy, extra, extra2], active=proxy)
        r = cb.resolve(bpy.context)
        self.assertEqual(r.target, self.ground)
        self.assertEqual(set(r.heroes), {extra, extra2})
        # rule 4: only a blended object selected -> re-apply
        self.select([rock])
        r = cb.resolve(bpy.context)
        self.assertEqual((r.target, r.heroes, r.label), (self.ground, [rock], "Re-apply 'Rock' to 'Ground'"))
        # proxies and the target are never blended, even when the picker is set
        bpy.context.scene.contact_blend_ground = proxy
        self.select([proxy, self.ground, extra], active=extra)
        r = cb.resolve(bpy.context)
        self.assertEqual((r.target, r.heroes), (self.ground, [extra]))

    def test_adding_to_a_ground_with_joined_blends_moves_them_to_shared(self):
        old = self.rock("Old", -2, 0)
        self.blend(old)
        cb.set_socket(cb.find_mod(old), "Shared Ground", False)
        cb.set_socket(cb.ground_mod(cb.find_proxy(self.ground)), "Contact Subdivisions", 1)
        cb.set_socket(cb.find_mod(old), "Contact Subdivisions", 4)
        self.select([old])
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        self.assertIsNone(cb.find_proxy(self.ground), "a joined-only ground kept a proxy")
        new = self.rock("New", 2, 0)
        self.ground.hide_set(False)
        self.select([self.ground, new], active=new)
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        self.assertTrue(cb.is_shared(cb.find_mod(old)), "joined blend left to re-cut the shared ground")
        proxy = cb.find_proxy(self.ground)
        # the converted blend's ground settings seed the shared ground
        self.assertEqual(cb.socket(cb.ground_mod(proxy), "Contact Subdivisions"), 4)
        self.assertEqual(len(self.evaluated(old).vertices), len(old.data.vertices))
        me, pts = self.ground_points()
        self.assertLess(self.dip(pts, old), -0.03)
        self.assertLess(self.dip(pts, new), -0.03)


class LegacyTests(unittest.TestCase):
    FIXTURE = HERE / "fixtures" / "joined_v7.blend"

    def test_joined_v7_blend_evaluates_the_same_and_converts_cleanly(self):
        bpy.ops.wm.open_mainfile(filepath=str(self.FIXTURE))
        rock, ground = bpy.data.objects["Rock"], bpy.data.objects["Ground"]
        mod = cb.find_mod(rock)
        self.assertEqual(cb.group_version(mod.node_group), cb.GROUP_VERSION, "not upgraded on load")
        self.assertFalse(cb.is_shared(mod), "an old blend changed mode on its own")
        self.assertIsNone(cb.find_proxy(ground))
        scene = bpy.context.scene
        rock.update_tag()
        ev = rock.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        try:
            positions = [c for v in me.vertices for c in v.co]
            self.assertEqual(len(positions), len(scene["cb_v7_positions"]))
            self.assertLess(max(abs(a - b) for a, b in zip(positions, scene["cb_v7_positions"])), 1e-6)
            blend = [d.value for d in me.attributes[cb.ATTR].data]
            self.assertLess(max(abs(a - b) for a, b in zip(blend, scene["cb_v7_blend"])), 1e-6)
            normals = [c for n in me.corner_normals for c in n.vector]
            self.assertLess(max(abs(a - b) for a, b in zip(normals, scene["cb_v7_normals"])), 1e-5)
        finally:
            ev.to_mesh_clear()
        # The joined copy, for comparison after conversion. The shared ground sinks along the
        # true normal rather than the smoothed one (see _build_ground_group), which tilts the
        # Surface Offset on a coarse object, so compare with that offset at zero: then the two
        # must be the same surface.
        ids = cb.input_ids(mod.node_group)
        mod[ids["Surface Offset"]] = 0.0
        rock.update_tag()
        ev = rock.evaluated_get(bpy.context.evaluated_depsgraph_get())
        own = len(rock.data.vertices)
        joined = [rock.matrix_world @ v.co for v in ev.to_mesh().vertices][own:]
        ev.to_mesh_clear()
        settings = {k: mod[ids[k]] for k in ("Contact Distance", "Surface Offset", "Contact Subdivisions")}

        bpy.ops.object.select_all(action="DESELECT")
        rock.select_set(True)
        bpy.context.view_layer.objects.active = rock
        self.assertEqual(bpy.ops.object.contact_blend_share(), {"FINISHED"})
        self.assertTrue(cb.is_shared(mod))
        self.assertEqual({k: mod[ids[k]] for k in settings}, settings, "conversion changed settings")
        proxy = cb.find_proxy(ground)
        self.assertEqual(cb.socket(cb.ground_mod(proxy), "Contact Subdivisions"), settings["Contact Subdivisions"])
        rock.update_tag()
        ev = rock.evaluated_get(bpy.context.evaluated_depsgraph_get())
        self.assertEqual(len(ev.to_mesh().vertices), len(rock.data.vertices))
        ev.to_mesh_clear()
        pev = proxy.evaluated_get(bpy.context.evaluated_depsgraph_get())
        pme = pev.to_mesh()
        try:
            shared = [proxy.matrix_world @ v.co for v in pme.vertices]
            self.assertEqual(len(shared), len(joined), "shared ground has a different island")
            kd = KDTree(len(shared))
            for i, p in enumerate(shared):
                kd.insert(p, i)
            kd.balance()
            worst = max(kd.find(p)[2] for p in joined)
            # float32 noise only: the joined copy is computed in the object's frame, the shared
            # ground in the target's, and both are then transformed to world space
            self.assertLess(worst, 5e-5, f"converted ground differs by {worst}")
        finally:
            pev.to_mesh_clear()
        self.assertTrue(ground.hide_render)
        self.assertEqual(bpy.ops.object.contact_blend_remove(), {"FINISHED"})
        self.assertIsNone(cb.find_proxy(ground))
        self.assertFalse(ground.hide_render)


class PanelTests(SharedBase):
    def draw(self):
        layout = fake_layout.Layout()
        cb.CONTACTBLEND_PT_panel.draw(types.SimpleNamespace(layout=layout), bpy.context)
        return layout.recorder

    def test_panel_in_every_state(self):
        # nothing selected
        self.select([])
        rec = self.draw()
        self.assertIn("Select a ground, then shift-select the objects to blend", rec.texts())
        self.assertIn(("set", ("enabled", False), {}), rec.calls)
        # a selection that will blend
        rock = self.rock("Rock", -2, 0)
        self.select([self.ground, rock], active=rock)
        self.assertIn("Blend 'Rock' into 'Ground'", self.draw().texts())
        # shared hero: hero settings, breakup inputs, ground section editing the proxy in place
        proxy = self.blend(rock)
        self.select([rock])
        rec = self.draw()
        texts = rec.texts()
        for expected in ("Re-apply 'Rock' to 'Ground'", "Update", "Displace Ground", "Contact Distance",
                         "Breakup", "Sharpness", "Noise Scale", "Shared by every object on 'Ground'",
                         "Subdivisions"):
            self.assertIn(expected, texts)
        gmod = cb.ground_mod(proxy)
        gkey = (gmod, cb.socket_key(gmod, "Contact Subdivisions"))
        self.assertIn(gkey, rec.props(), "ground section does not edit the proxy")
        hmod = cb.find_mod(rock)
        self.assertNotIn((hmod, cb.socket_key(hmod, "Contact Subdivisions")), rec.props())
        self.assertNotIn(cb.CONTACTBLEND_OT_share.bl_idname, rec.operators())
        self.assertIn(("prop_search", (cb.find_mod(rock), cb.socket_key(cb.find_mod(rock), "Target"),
                                       bpy.data, "objects"), {"text": "Target"}), rec.calls)
        self.assertIn(cb.CONTACTBLEND_OT_diagnose.bl_idname, rec.operators())
        # out of sync: a changed Target is named and Update is highlighted
        other = grid("Other", 2, 4)
        cb.set_socket(cb.find_mod(rock), "Target", other)
        rec = self.draw()
        self.assertIn("Target changed", rec.texts())
        self.assertIn(("set", ("alert", True), {}), rec.calls)
        cb.set_socket(cb.find_mod(rock), "Target", self.ground)
        # several target materials: say which one is used
        self.material(self.ground, "Ground B")
        self.assertIn("Uses 'Ground' (the ground's active material)", self.draw().texts())
        # proxy selected: ground settings and the list of objects
        self.select([proxy])
        rec = self.draw()
        self.assertIn("Objects (1)", rec.texts())
        self.assertIn("Select Objects", rec.texts())
        self.assertIn(cb.CONTACTBLEND_OT_remove.bl_idname, rec.operators())
        self.assertIn((cb.ground_mod(proxy), gkey[1]), rec.props())
        # missing proxy
        bpy.data.objects.remove(proxy, do_unlink=True)
        self.select([rock])
        rec = self.draw()
        self.assertIn("Shared ground is missing", rec.texts())
        self.assertIn("Shared ground missing: press Update", rec.texts())
        self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
        # joined (legacy) hero: offers the conversion, draws its own ground settings
        cb.set_socket(cb.find_mod(rock), "Shared Ground", False)
        rec = self.draw()
        self.assertIn(cb.CONTACTBLEND_OT_share.bl_idname, rec.operators())
        hmod = cb.find_mod(rock)
        self.assertIn((hmod, cb.socket_key(hmod, "Contact Subdivisions")), rec.props())
        # stale group: an upgrade prompt and no socket rows that might not exist
        mod = cb.find_mod(rock)
        mod.node_group[cb.VERSION_KEY] = cb.GROUP_VERSION - 1
        rec = self.draw()
        self.assertIn("Upgrade", rec.texts())
        self.assertNotIn((mod, cb.socket_key(mod, "Contact Distance")), rec.props())
        cb.build_group()
        # target outside the view layer
        loose = bpy.data.objects.new("Loose", self.ground.data)
        cb.set_socket(mod, "Target", loose)
        self.draw()
        # a foreign group that only looks like ours by its socket layout
        foreign = bpy.data.node_groups.new("Not ours", "GeometryNodeTree")
        foreign.interface.new_socket(name="Contact Distance", in_out="INPUT", socket_type="NodeSocketFloat")
        mod.node_group = foreign
        self.draw()

    def test_displace_ground_applies_immediately(self):
        rock = self.rock("Rock", 0, 0)
        self.blend(rock)
        rock.contact_blend_displace = False
        self.assertIsNone(cb.find_proxy(self.ground), "ground kept drawing")
        self.assertFalse(self.ground.hide_render, "target left hidden with nothing drawing it")
        self.assertEqual(cb.out_of_sync(rock, cb.find_mod(rock)), [])
        rock.contact_blend_displace = True
        self.assertIsNotNone(cb.find_proxy(self.ground))
        self.assertTrue(self.ground.hide_render)
        # through the raw socket instead (the modifier panel), the panel names it
        cb.set_socket(cb.find_mod(rock), "Displace Ground", False)
        self.assertIn("Displace Ground changed", cb.out_of_sync(rock, cb.find_mod(rock)))
        cb.set_socket(cb.find_mod(rock), "Match Target Textures", False)
        self.assertIn("Match Target Textures changed", cb.out_of_sync(rock, cb.find_mod(rock)))


class DiagnoseTests(SharedBase):
    def test_diagnose_operator_and_standalone_script(self):
        a, b = self.rock("A", -2, 0), self.rock("B", 2, 0)
        proxy = self.blend(a, b)
        self.select([a])
        self.assertEqual(bpy.ops.object.contact_blend_diagnose(), {"FINISHED"})
        report = bpy.data.texts[cb.REPORT_TEXT].as_string()
        self.assertIn(f"shared ground    : '{proxy.name}'", report)
        self.assertIn(f"attribute '{cb.PARAMS_ATTR}': present", report)
        self.assertIn("ground objects   : 2 listed, 2 expected", report)
        self.assertIn("z spread 0.000000", report)
        self.assertNotIn("^--", report, "a healthy shared blend reported a problem")
        # a hidden proxy and an object missing from the list are reported
        proxy.hide_render = True
        cb.heroes_collection(self.ground).objects.unlink(b)
        report = "\n".join(cb.diagnose(a))
        self.assertIn("the shared ground is hidden", report)
        self.assertIn("'B' is blended but missing from the shared ground", report)
        # selecting the ground reports on every object on it
        self.assertEqual(sum(1 for line in cb.diagnose(proxy) if line.startswith("object   :")), 2)
        # the standalone script runs the add-on's checks when it is loaded...
        script = str(HERE.parent / "contact_blend_diag.py")
        bpy.context.view_layer.objects.active = a
        runpy.run_path(script)
        self.assertIn("shared ground    :", bpy.data.texts[cb.REPORT_TEXT].as_string())
        # ...and its own copy when it is not
        saved = {k: v for k, v in sys.modules.items() if getattr(v, "bl_info", None)}
        for k in saved:
            del sys.modules[k]
        try:
            runpy.run_path(script)
        finally:
            sys.modules.update(saved)
        report = bpy.data.texts[cb.REPORT_TEXT].as_string()
        self.assertIn(f"mode     : shared ground; drawn by '{proxy.name}'", report)
        self.assertIn("attribute 'blend': present", report)


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

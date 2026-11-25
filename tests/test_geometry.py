# SPDX-License-Identifier: Apache-2.0
"""Node-tree regressions: island selection, world-space distances, instanced targets, laziness."""

import collections
import math
import pathlib
import sys
import time
import unittest

import bpy
from mathutils import Vector

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import contact_blend as cb


def grid(name, size, cells, height=None):
    """A cells x cells quad grid, `size` metres across, centred on the origin."""
    step = size / cells
    verts = []
    for j in range(cells + 1):
        for i in range(cells + 1):
            x, y = -size / 2 + i * step, -size / 2 + j * step
            verts.append((x, y, height(x, y) if height else 0.0))
    faces = [(j * (cells + 1) + i, j * (cells + 1) + i + 1,
              (j + 1) * (cells + 1) + i + 1, (j + 1) * (cells + 1) + i)
             for j in range(cells) for i in range(cells)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        co = me.vertices[loop.vertex_index].co
        me.uv_layers["UVMap"].data[loop.index].uv = (co.x / size + 0.5, co.y / size + 0.5)
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj


class GeometryTests(unittest.TestCase):
    """Run against the shared ground (the default for new blends); JoinedGeometryTests below
    runs every test again against the legacy per-object joined copy."""
    SHARED = True

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def material(self, obj, name):
        mat = bpy.data.materials.new(name)
        cb.ensure_node_tree(mat)
        obj.data.materials.append(mat)
        return mat

    def sphere(self, location, radius=0.5, name="Rock"):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=location)
        obj = bpy.context.object
        obj.name = name
        self.material(obj, name)
        return obj

    def apply(self, src, tgt):
        bpy.ops.object.select_all(action="DESELECT")
        tgt.select_set(True)
        src.select_set(True)
        bpy.context.view_layer.objects.active = src
        self.assertEqual(bpy.ops.object.contact_blend(), {"FINISHED"})
        mod = cb.find_mod(src)
        self.assertTrue(cb.is_shared(mod))
        if not self.SHARED:
            # What a blend made before 2.0 is: the ground joined into the object's own output.
            # Adding an object converts the ground's other blends to shared, so put them all back.
            bpy.ops.object.select_all(action="DESELECT")
            for obj, m in cb.blends(tgt):
                cb.set_socket(m, "Shared Ground", False)
                obj.select_set(True)
            bpy.context.view_layer.objects.active = src
            self.assertEqual(bpy.ops.object.contact_blend_update(), {"FINISHED"})
            self.assertIsNone(cb.find_proxy(tgt))
        return mod, cb.input_ids(mod.node_group)

    def set_ground(self, obj, name, value):
        """Ground-level settings live on the proxy when shared, on the object when joined."""
        mod = cb.find_mod(obj)
        if self.SHARED:
            mod = cb.ground_mod(cb.find_proxy(cb.mod_target(mod)))
        cb.set_socket(mod, name, value)

    def ground_view(self, obj):
        """(object that draws the ground, its evaluated mesh, the ground's polygons)."""
        mod = cb.find_mod(obj)
        if self.SHARED:
            owner = cb.find_proxy(cb.mod_target(mod))
            self.assertIsNotNone(owner, "no shared ground")
            me = self.evaluated(owner)
            return owner, me, list(me.polygons)
        me = self.evaluated(obj)
        return obj, me, self.ground_polys(me, mod, cb.input_ids(mod.node_group))

    def evaluated(self, obj):
        obj.update_tag()
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        self.addCleanup(ev.to_mesh_clear)
        return ev.to_mesh()

    def ground_polys(self, me, mod, ids):
        names = {m.name for m in (mod[ids["Target"]].data.materials) if m}
        if mod[ids["Ground Material"]]:
            names.add(mod[ids["Ground Material"]].name)
        idx = {i for i, m in enumerate(me.materials) if m and m.name in names}
        return [p for p in me.polygons if p.material_index in idx]

    def hero_blend(self, obj, me, mod, ids):
        """{position relative to the object origin, in world metres: blend} for the hero part."""
        ground = set() if self.SHARED else {i for p in self.ground_polys(me, mod, ids) for i in p.vertices}
        values = me.attributes[cb.ATTR].data
        origin = obj.matrix_world.translation
        out = {}
        for i, v in enumerate(me.vertices):
            if i not in ground:
                rel = obj.matrix_world @ v.co - origin
                out[tuple(round(c, 4) for c in rel)] = values[i].value
        self.assertEqual(len(out), len(obj.data.vertices))
        return out

    def ground_world(self, obj, me, polys):
        verts = {i for p in polys for i in p.vertices}
        return {i: obj.matrix_world @ me.vertices[i].co for i in verts}

    def open_edge_verts(self, polys):
        seen = collections.Counter()
        for p in polys:
            vs = list(p.vertices)
            for a, b in zip(vs, vs[1:] + vs[:1]):
                seen[(min(a, b), max(a, b))] += 1
        return {i for e, c in seen.items() if c == 1 for i in e}

    def test_coarse_ground_craters_with_contact_subdivisions(self):
        # One 4 m quad under a 1 m rock: every corner is far outside Contact Distance, so a
        # per-vertex island is empty and no subdivision level can ever reach the contact.
        ground = grid("Ground", 4, 1)
        self.material(ground, "Ground")
        rock = self.sphere((0, 0, 0.3))
        mod, ids = self.apply(rock, ground)
        lows = {}
        for level in (0, 3, 5):
            self.set_ground(rock, "Contact Subdivisions", level)
            owner, me, polys = self.ground_view(rock)
            pts = self.ground_world(owner, me, polys)
            lows[level] = min(p.z for p in pts.values())
            corners = [p for p in pts.values() if abs(p.x) > 2 - 1e-4 and abs(p.y) > 2 - 1e-4]
            self.assertEqual(len(corners), 4)
            self.assertLess(max(abs(p.z) for p in corners), 1e-6, "far corners moved")
        self.assertAlmostEqual(lows[0], 0.0, places=6)
        self.assertLess(lows[3], -0.05, f"no crater at level 3: {lows}")
        self.assertLessEqual(lows[5], lows[3], f"denser island cratered less: {lows}")

    def test_cut_through_coarse_faces_is_frozen(self):
        # Rock centred on a 2 m face: no vertex is within Contact Distance, so the old island
        # was empty; and edges between two far vertices pass close to the rock, so a cut along
        # one of them would tear as soon as subdivision put a moving vertex on it.
        ground = grid("Ground", 20, 10)
        self.material(ground, "Ground")
        rock = self.sphere((1, 1, 0.3))
        mod, ids = self.apply(rock, ground)
        self.set_ground(rock, "Contact Subdivisions", 3)
        owner, me, polys = self.ground_view(rock)
        pts = self.ground_world(owner, me, polys)
        self.assertLess(min(p.z for p in pts.values()), -0.05, "ground did not crater")
        cut = {i for i in self.open_edge_verts(polys)
               if abs(pts[i].x) < 10 - 1e-4 and abs(pts[i].y) < 10 - 1e-4}
        self.assertTrue(cut, "no subdivision cut found; the test is not biting")
        self.assertLess(max(abs(pts[i].z) for i in cut), 1e-6, "a vertex on the cut moved")
        contact = mod[ids["Contact Distance"]]
        rock_centre = rock.matrix_world.translation
        for i in cut:
            self.assertGreater((pts[i] - rock_centre).length - 0.5, contact)

    def test_curved_ground_is_welded_along_the_cut(self):
        # The weld now runs only on open-edge vertices; any cut vertex it missed would be a
        # duplicate position and a crease ring on a smooth, non-flat ground. (Open edges remain
        # along the cut by design -- they are the T-junctions -- so count duplicates instead.)
        ground = grid("Ground", 4, 40, lambda x, y: 0.15 * math.sin(2 * x) * math.cos(2 * y))
        ground.data.shade_smooth()
        self.material(ground, "Ground")
        rock = self.sphere((0.1, 0.2, 0.35))
        mod, ids = self.apply(rock, ground)
        owner, me, polys = self.ground_view(rock)
        verts = {i for p in polys for i in p.vertices}
        world = self.ground_world(owner, me, polys)
        self.assertGreater(len(verts), (41 * 41) * 1.2, "contact island was not subdivided")
        keys = collections.Counter(tuple(round(c, 6) for c in me.vertices[i].co) for i in verts)
        dup = [k for k, n in keys.items() if n > 1]
        self.assertFalse(dup, f"{len(dup)} unwelded duplicate ground vertices")
        # ...and the cut is really there: some open edges lie away from the ground's rim
        interior = {i for i in self.open_edge_verts(polys)
                    if max(abs(world[i].x), abs(world[i].y)) < 2 - 1e-4}
        self.assertTrue(interior, "no cut inside the ground; the test is not biting")

    def test_displace_off_shades_from_the_raw_target(self):
        # With no ground copy drawn, the hero's projected UVs and welded normals must come from
        # the target it actually meets, not from a conformed copy nobody can see.
        ground = grid("Ground", 4, 40)
        self.material(ground, "Ground")
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0.2, -0.1, 0.4))
        rock = bpy.context.object
        self.material(rock, "Rock")
        mod, ids = self.apply(rock, ground)
        rock.contact_blend_displace = False
        self.assertFalse(mod[ids["Displace Ground"]])
        self.assertIsNone(cb.find_proxy(ground), "the shared ground outlived Displace Ground")
        self.assertTrue(mod[ids["Match Target Textures"]])
        me = self.evaluated(rock)
        self.assertEqual(len(me.vertices), 8)
        uv = me.uv_layers[cb.TARGET_UV_ATTR].data
        for loop in me.loops:
            p = rock.matrix_world @ me.vertices[loop.vertex_index].co
            expected = Vector((p.x / 4 + 0.5, p.y / 4 + 0.5))
            self.assertLess((uv[loop.index].uv - expected).length, 1e-5)
        # The flat target's normal is +Z everywhere, so each corner's normal must be its own
        # normal swung towards +Z by the weld weight 1 - smoothstep(d / Normal Blend Distance),
        # d being the corner's height above or below the plane.
        reach = mod[ids["Normal Blend Distance"]]
        own = {(p.index, i): p.normal.copy() for p in rock.data.polygons for i in p.loop_indices}
        welded = 0
        for poly in me.polygons:
            for i in poly.loop_indices:
                z = (rock.matrix_world @ me.vertices[me.loops[i].vertex_index].co).z
                x = min(abs(z) / reach, 1.0)
                t = 1 - x * x * (3 - 2 * x)
                expected = (own[poly.index, i] * (1 - t) + Vector((0, 0, 1)) * t).normalized()
                got = me.corner_normals[i].vector.normalized()
                self.assertLess((got - expected).length, 1e-3,
                                f"corner {i} at z={z:.3f} not welded to the raw target")
                welded += t > 0.1
        self.assertGreater(welded, 0, "no corner is inside Normal Blend Distance")

    def test_displace_off_does_not_evaluate_the_ground_branch(self):
        bpy.ops.mesh.primitive_grid_add(size=10, x_subdivisions=300, y_subdivisions=300)
        ground = bpy.context.object
        self.material(ground, "Ground")
        rock = self.sphere((0, 0, 0.3))
        mod, ids = self.apply(rock, ground)
        rock.contact_blend_displace = False

        def cost(level):
            # the object's own socket: what its local patch would be cut with
            mod[ids["Contact Subdivisions"]] = level
            times = []
            for i in range(3):
                mod[ids["Contact Distance"]] = 0.3 + 0.001 * i
                rock.update_tag()
                t0 = time.perf_counter()
                bpy.context.evaluated_depsgraph_get().update()
                times.append(time.perf_counter() - t0)
            return min(times)

        cheap, dense = cost(0), cost(5)
        # Level 5 over this island is millions of faces; if any of it were evaluated the second
        # number would be orders of magnitude larger, not a few percent.
        self.assertLess(dense, cheap * 3 + 0.05, f"level 0 {cheap:.3f}s, level 5 {dense:.3f}s")

    def test_distances_are_world_metres_under_object_scale(self):
        ground = grid("Ground", 6, 60)
        self.material(ground, "Ground")
        plain = self.sphere((-1.2, 0, 0.3), name="Plain")
        scaled = self.sphere((1.2, 0, 0.3), radius=5, name="Scaled")
        scaled.scale = (0.1, 0.1, 0.1)
        results = {}
        for obj in (plain, scaled):
            ground.hide_set(False)     # the first blend hid it, and hidden objects cannot be selected
            mod, ids = self.apply(obj, ground)
            # set explicitly, so this tests scale handling and not the fitted defaults
            mod[ids["Contact Distance"]] = mod[ids["Material Blend Distance"]] = 0.3
            mod[ids["Normal Blend Distance"]] = 0.2
            mod[ids["Surface Offset"]] = 0.002
            obj.update_tag()       # socket writes from Python do not tag; the UI would
        for obj in (plain, scaled):
            mod = cb.find_mod(obj)
            ids = cb.input_ids(mod.node_group)
            me = self.evaluated(obj)
            blend = self.hero_blend(obj, me, mod, ids)
            owner, gme, polys = self.ground_view(obj)
            # only the ground around this object: with a shared ground both craters are in it
            near = [p for p in self.ground_world(owner, gme, polys).values()
                    if (p.xy - obj.location.xy).length < 0.95]   # no grid point is 0.95 away
            results[obj.name] = blend, min(p.z for p in near), len(near)
        (b0, z0, n0), (b1, z1, n1) = results["Plain"], results["Scaled"]
        self.assertGreater(max(b0.values()), 0.9)
        self.assertEqual(b0.keys(), b1.keys(), "hero shapes do not match; test is broken")
        self.assertLess(max(abs(b0[k] - b1[k]) for k in b0), 1e-4, "blend differs under scale")
        self.assertLess(z0, -0.05)
        self.assertAlmostEqual(z0, z1, delta=1e-4)
        self.assertEqual(n0, n1, "contact island differs under scale")

    def test_instanced_target_is_realized(self):
        ground = grid("Ground", 4, 40)
        self.material(ground, "Ground")
        ng = bpy.data.node_groups.new("As instance", "GeometryNodeTree")
        ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        gin, gout = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        inst = ng.nodes.new("GeometryNodeGeometryToInstance")
        ng.links.new(gin.outputs[0], inst.inputs[0])
        ng.links.new(inst.outputs[0], gout.inputs[0])
        ground.modifiers.new("Instance", "NODES").node_group = ng
        rock = self.sphere((0, 0, 0.3))
        mod, ids = self.apply(rock, ground)
        me = self.evaluated(rock)
        blend = self.hero_blend(rock, me, mod, ids)
        self.assertGreater(max(blend.values()), 0.9, "blend is empty against an instanced target")
        pts = self.ground_world(*self.ground_view(rock))
        self.assertGreater(len(pts), 41 * 41)
        self.assertLess(min(p.z for p in pts.values()), -0.05, "instanced ground did not crater")


class JoinedGeometryTests(GeometryTests):
    """The same checks on blends in the legacy joined mode, which old files keep using."""
    SHARED = False


if __name__ == "__main__":
    cb.register()
    try:
        result = unittest.main(argv=[__file__], exit=False).result
    finally:
        cb.unregister()
    if not result.wasSuccessful():
        raise SystemExit(1)

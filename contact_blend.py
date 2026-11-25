# SPDX-License-Identifier: Apache-2.0

bl_info = {
    "name": "Contact Blend",
    "author": "amsy",
    "version": (2, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Contact, and Object > Contact Blend",
    "description": "Sink objects into a ground without booleans: one shared ground conforms to every object on it, with welded normals and a cross-faded material at each contact",
    "category": "Object",
    "license": "SPDX:Apache-2.0",
}

import bpy

GROUP = "Contact Blend"
ATTR = "blend"
# Names this add-on shipped under before 1.1. Both the node group and the material mix node are
# found by *name*, so renaming without a migration would orphan every .blend built with an
# older release: the modifier would keep evaluating, but the panel could not find it and
# setup_material would wire a second mix node beside the first one it failed to recognise.
# migrate_names() renames them in place -- datablock and node references are pointers, so a
# rename carries every user along with it.
LEGACY_NAMES = ("Blend To Surface",)
# Bump whenever the node tree changes. build_group() rebuilds any group stamped with an older
# number, so an existing .blend picks up fixes on reload instead of silently running the tree
# it was first built with -- a stale group is indistinguishable from a bug that did not get
# fixed, and telling people to delete it by hand is exactly how it survives.
GROUP_VERSION = 8
# The stamp moved with the rename. An old group carries the value under the old key, so read
# both and rewrite to the current one -- treating the old key as unstamped would force a
# needless rebuild, and the diagnostic would report a group of unknown vintage.
VERSION_KEY = "cb_version"
LEGACY_VERSION_KEYS = ("bts_version",)
GENERATED_KEY = "cb_generated"
ISOLATED_KEY = "cb_isolated_material"
VISIBILITY_KEY = "cb_original_visibility"
HIDDEN_TARGET_KEY = "cb_hidden_target"
TARGET_UV_ATTR = "cb_target_uv"
TEXTURE_DATA_KEY = "cb_texture_data"
BREAKUP_GROUP = "Contact Blend Breakup"
GROUND_GROUP = "Contact Blend Ground"
# The ground group is stamped under its own key: _is_ours() treats anything carrying
# VERSION_KEY as a hero group, and the two must never be mistaken for each other.
GROUND_VERSION_KEY = "cb_ground_version"
GROUND_VERSION = 1
PARAMS_ATTR = "cb_params"
PROXY_KEY = "cb_ground_proxy"
HEROES_KEY = "cb_ground_objects"
APPLIED_KEY = "cb_applied"
BREAKUP_VERSION = 1


def _socket(ng, name, stype, in_out="INPUT", **kw):
    s = ng.interface.new_socket(name=name, in_out=in_out, socket_type=stype)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _node(ng, ident, x, y, **props):
    n = ng.nodes.new(ident)
    n.location = (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def _users(ng):
    return [(o, m) for o in bpy.data.objects for m in o.modifiers
            if m.type == "NODES" and m.node_group is ng]


def group_version(ng):
    """Version stamp under the current key or any name this add-on used before."""
    for k in (VERSION_KEY,) + LEGACY_VERSION_KEYS:
        if k in ng:
            return ng[k]
    return None


def _stamp(ng, version=GROUP_VERSION):
    ng[VERSION_KEY] = version
    for k in LEGACY_VERSION_KEYS:
        if k in ng:
            del ng[k]


def _is_ours(ng):
    """True if `ng` is one of our groups and not an unrelated tree that shares the old name."""
    return ng.bl_idname == "GeometryNodeTree" and (
        group_version(ng) is not None or "Contact Distance" in input_ids(ng))


def _our_groups():
    """Every Contact Blend group in the file, whatever it is called.

    Names are not reliable: appending or linking a blended object into a file that already
    has the group brings its own copy in as "Contact Blend.001", and a user is free to call an
    unrelated group "Contact Blend". The stamp (or, for pre-stamp files, the socket layout) is
    what marks a group as ours. Duplicates are deliberately left alone rather than merged --
    each one's modifiers keep their own settings, and every one is upgraded the same way.
    """
    return [ng for ng in bpy.data.node_groups if _is_ours(ng)]


def node_tree(mat):
    """The node tree that shades `mat`, or None if it is not a node material.

    `Material.use_nodes` is deprecated from 5.0 -- every material is a node material there and
    reading the flag only prints a warning (it goes away in 6.0). 4.5 still has materials
    without nodes, so the flag still decides there.
    """
    if mat is None:
        return None
    if bpy.app.version >= (5, 0, 0):
        return mat.node_tree
    return mat.node_tree if mat.use_nodes else None


def ensure_node_tree(mat):
    """Turn `mat` into a node material if it is not one already, and return its tree."""
    if bpy.app.version < (5, 0, 0) and not mat.use_nodes:
        mat.use_nodes = True
    return mat.node_tree


def migrate_names():
    """Rename anything this add-on left under a pre-1.1 name. Returns how many it moved.

    Only ever renames into a free name: if the current name is already taken the old datablock
    is left alone rather than being pushed to a `.001` suffix. That is harmless now that groups
    are found by stamp rather than by name; build_group() upgrades it either way.

    Modifiers are deliberately *not* renamed. Their name is cosmetic here -- find_mod() goes
    by node group -- and a driver written against `modifiers["Blend To Surface"]` would break.
    """
    moved = 0
    for old in LEGACY_NAMES:
        ng = bpy.data.node_groups.get(old)
        if ng and _is_ours(ng) and not bpy.data.node_groups.get(GROUP):
            ng.name = GROUP
            if group_version(ng) is not None:
                _stamp(ng, group_version(ng))
            moved += 1
        for mat in bpy.data.materials:
            tree = node_tree(mat)
            if tree is None:
                continue
            node = tree.nodes.get(old)
            if node and node.type == "MIX_SHADER" and not tree.nodes.get(GROUP):
                node.name = node.label = GROUP
                moved += 1
    return moved


def _rebuild(ng, build=None, stamp=None):
    """Rebuild `ng`'s tree in place at the current version, carrying modifier settings across.

    In place keeps every existing modifier pointing at it. Socket identifiers are regenerated,
    so settings are carried by socket *name*; anything renamed between versions falls back to
    the new default.
    """
    old = input_ids(ng)
    saved = [(obj.name, mod.name,
              {n: mod[i] for n, i in old.items() if n != "Geometry" and i in mod})
             for obj, mod in _users(ng)]
    ng.nodes.clear()
    ng.interface.clear()
    (build or _build_group)(ng)
    (stamp or _stamp)(ng)
    new = input_ids(ng)
    for obj_name, mod_name, vals in saved:
        mod = bpy.data.objects[obj_name].modifiers.get(mod_name)
        for name, v in vals.items():
            if mod and name in new:
                try:
                    mod[new[name]] = v
                except (TypeError, ValueError):
                    pass
        if mod:
            bpy.data.objects[obj_name].update_tag()
    return ng


def build_group():
    """Return our node group, rebuilding every stale one in place first.

    New modifiers get the group called exactly GROUP if that one is ours, else any of ours,
    else a fresh one. Every stamped group in the file is upgraded, not just that one: a file
    that appended a blended object from elsewhere has two, and leaving the second on an old
    tree would quietly run a version this build knows is wrong.
    """
    migrate_names()
    # A group linked from a library is read-only here; its own file upgrades it when opened.
    ours = [ng for ng in _our_groups() if ng.library is None]
    for ng in ours:
        if group_version(ng) != GROUP_VERSION:
            _rebuild(ng)
    named = bpy.data.node_groups.get(GROUP)
    ng = named if named is not None and named in ours else (ours[0] if ours else None)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
        _build_group(ng)
        _stamp(ng)
    return ng


def _is_ground_group(ng):
    return ng.bl_idname == "GeometryNodeTree" and GROUND_VERSION_KEY in ng


def _stamp_ground(ng):
    ng[GROUND_VERSION_KEY] = GROUND_VERSION


def ground_group():
    """The shared-ground node group, upgrading every stale local one in place first."""
    ours = [ng for ng in bpy.data.node_groups if _is_ground_group(ng) and ng.library is None]
    for ng in ours:
        if ng[GROUND_VERSION_KEY] != GROUND_VERSION:
            _rebuild(ng, _build_ground_group, _stamp_ground)
    named = bpy.data.node_groups.get(GROUND_GROUP)
    ng = named if named is not None and named in ours else (ours[0] if ours else None)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUND_GROUP, "GeometryNodeTree")
        _build_ground_group(ng)
        _stamp_ground(ng)
    return ng


class _Tree:
    """Node-building helpers shared by the hero and ground trees. Each call adds and wires nodes."""

    def __init__(self, ng):
        self.ng = ng
        self.link = ng.links.new
        self.pos = self.node("GeometryNodeInputPosition", -1300, -460).outputs["Position"]
        normal = self.node("GeometryNodeInputNormal", -1300, -560)
        self.nor = normal.outputs["Normal"]
        # The surface's own normal, ignoring custom normals. Missing on versions without the
        # output; there Normal is the best available stand-in.
        self.true_nor = normal.outputs.get("True Normal") or self.nor

    def node(self, ident, x, y, **props):
        return _node(self.ng, ident, x, y, **props)

    def feed(self, socket, value):
        if isinstance(value, (int, float)):
            socket.default_value = value
        elif value is not None:
            self.link(value, socket)

    def math(self, x, y, op, a, b=None):
        m = self.node("ShaderNodeMath", x, y, operation=op)
        self.feed(m.inputs[0], a)
        self.feed(m.inputs[1], b)
        return m.outputs["Value"]

    def vmath(self, x, y, op, a, b=None, scale=None):
        v = self.node("ShaderNodeVectorMath", x, y, operation=op)
        self.link(a, v.inputs[0])
        if b is not None:
            self.link(b, v.inputs[1])
        if scale is not None:
            self.feed(v.inputs[3], scale)
        return v.outputs["Value" if op in ("DISTANCE", "DOT_PRODUCT", "LENGTH") else "Vector"]

    def ramp(self, x, y, dist_out, far, near, far_value):
        """Map proximity distance -> value: `near` at contact, `far_value` at distance `far`."""
        mr = self.node("ShaderNodeMapRange", x, y, interpolation_type="SMOOTHSTEP", clamp=True)
        self.link(dist_out, mr.inputs[0])
        mr.inputs[1].default_value = 0.0
        self.feed(mr.inputs[2], far)
        self.feed(mr.inputs[3], near)
        mr.inputs[4].default_value = far_value
        return mr.outputs[0]

    def proximity(self, x, y, geo_out):
        p = self.node("GeometryNodeProximity", x, y, target_element="FACES")
        self.link(geo_out, p.inputs["Geometry"])
        self.link(self.pos, p.inputs["Sample Position"])
        return p.outputs["Distance"], p.outputs["Position"]

    def sample(self, x, y, geo_out, value, data_type="FLOAT_VECTOR"):
        """`value` of `geo_out`'s surface at the point closest to each evaluated point."""
        s = self.node("GeometryNodeSampleNearestSurface", x, y, data_type=data_type)
        self.link(geo_out, s.inputs["Mesh"])
        self.link(value, s.inputs["Value"])
        self.link(self.pos, s.inputs["Sample Position"])
        return s.outputs["Value"]

    def self_scale(self, x, y):
        """This object's world scale as one number: mean of the absolute axes.

        Object Info in RELATIVE space hands geometry over in this object's local frame, so every
        Proximity distance is in local units. Dividing the user's world-metre distances by this
        makes "0.3 m" mean 0.3 m whatever the object's scale. Exact for uniform scale; under
        non-uniform scale no single number converts distances in every direction, so it is an
        approximation there.
        """
        me = self.node("GeometryNodeSelfObject", x, y)
        info = self.node("GeometryNodeObjectInfo", x + 200, y, transform_space="ORIGINAL")
        self.link(me.outputs[0], info.inputs["Object"])
        sabs = self.vmath(x + 400, y, "ABSOLUTE", info.outputs["Scale"])
        mean = self.node("ShaderNodeVectorMath", x + 600, y, operation="DOT_PRODUCT")
        self.link(sabs, mean.inputs[0])
        mean.inputs[1].default_value = (1 / 3, 1 / 3, 1 / 3)
        return self.math(x + 800, y, "MAXIMUM", mean.outputs["Value"], 1e-6)

    def target(self, x, y, obj_socket):
        info = self.node("GeometryNodeObjectInfo", x, y, transform_space="RELATIVE")
        self.link(obj_socket, info.inputs["Object"])
        # A ground that is itself a scatter or collection instance arrives as instances, which
        # Proximity and Separate Geometry cannot see into. Realize them, or the blend is empty.
        realize = self.node("GeometryNodeRealizeInstances", x + 200, y)
        self.link(info.outputs["Geometry"], realize.inputs["Geometry"])
        return realize.outputs["Geometry"]

    def island(self, x, y, geo, dist, radius):
        """Separate the faces of `geo` that can come within `radius` of the object.

        Let c be a face's vertex centroid and r = half its perimeter. Every corner v satisfies
        |v - c| <= mean_j |v - v_j| <= r (any two corners are joined by a path along the boundary
        no longer than half of it), and every other point of the face -- edge midpoints
        included -- lies in the hull of the corners, so also within r of c. Distance to the
        object is 1-Lipschitz, so d(p) >= d(c) - r for every point p on the face. A face is
        selected when d(c) - r <= radius; a face left out therefore has d(p) > radius at every
        point on it, including every vertex subdivision may later insert on its edges.

        r is mean edge length * corner count / 2. The edge length is captured on the edge
        domain first: evaluated in face context, Edge Vertices would hand back face-averaged
        *positions*, not an average of lengths.
        """
        ends = self.node("GeometryNodeInputMeshEdgeVertices", x, y - 100)
        edge_len = self.node("GeometryNodeCaptureAttribute", x + 200, y, domain="EDGE")
        edge_len.capture_items.new("FLOAT", "length")
        self.link(geo, edge_len.inputs["Geometry"])
        self.link(self.vmath(x, y - 200, "DISTANCE", ends.outputs["Position 1"], ends.outputs["Position 2"]),
                  edge_len.inputs["length"])
        corners = self.node("GeometryNodeInputMeshFaceNeighbors", x + 200, y - 200)
        reach = self.math(x + 400, y - 150, "MULTIPLY",
                          self.math(x + 400, y - 250, "MULTIPLY", edge_len.outputs["length"],
                                    corners.outputs["Vertex Count"]), 0.5)
        clear = self.math(x + 600, y - 100, "GREATER_THAN",
                          self.math(x + 600, y - 200, "SUBTRACT", dist, reach), radius)
        keep = self.node("FunctionNodeBooleanMath", x + 800, y - 100, operation="NOT")
        self.link(clear, keep.inputs[0])
        sep = self.node("GeometryNodeSeparateGeometry", x + 800, y, domain="FACE")
        self.link(edge_len.outputs["Geometry"], sep.inputs["Geometry"])
        self.link(keep.outputs[0], sep.inputs["Selection"])
        return sep

    def conform(self, x, y, raw, dist, closest, radius, island_radius, conform, offset, sink_dir,
                level, detail):
        """Conform `raw` (the ground) to the object(s) that `dist`/`closest` measure against.

        The seam is the curve where the two surfaces cross, so the only way to remove it is to
        stop them crossing. Deforming the object cannot do that -- pushing its contact band onto
        the target flattens the silhouette into a pancake with a hard rim. Deform the ground
        instead: slide each ground vertex to the closest point on the object, weighted
        `conform` at contact and 0 at `radius`. Ground under the object drops into a crater that
        hugs its underside; ground just outside rides up its flank as a meniscus.

        How closely the ground can hug the object is capped by its vertex density, so only the
        contact island is subdivided. That leaves T-junctions along the cut, and a T-junction
        cracks open the moment either end of the edge it sits on moves -- so the cut has to fall
        strictly outside the moving zone, new midpoints on it included. island() guarantees
        every point of a face left out has d > island_radius; with radius <= island_radius at
        every point, the conform weight and the detail mask there are exactly 0 (the ramp is
        clamped at its far end). So every vertex on the cut is frozen, and equivalently any
        vertex that can move has all of its faces in the island.

        `radius`, `conform` and `offset` may be fields (the shared ground samples them per vertex
        from the nearest object); `island_radius` must bound `radius` from above everywhere.
        Everything that moves the ground runs on the island alone: the remainder is provably
        never moved, so evaluating proximity for every vertex of a half-million-vertex floor
        only to multiply the result by zero is pure cost.
        """
        # A zero radius would make the ramp divide by zero and report "at contact" everywhere.
        radius = self.math(x, y + 200, "MAXIMUM", radius, 1e-6)
        island_radius = self.math(x, y + 100, "MAXIMUM", island_radius, 1e-6)
        sep = self.island(x, y, raw, dist, island_radius)
        sub = self.node("GeometryNodeSubdivideMesh", x + 1000, y)
        self.link(sep.outputs["Selection"], sub.inputs["Mesh"])
        self.link(level, sub.inputs["Level"])

        # --- real height, but only where there are vertices to carry it ---
        # Subdividing the contact island and then not putting anything in it wastes the density:
        # it only smooths the crater. Displace here instead of over the whole ground, masked by
        # the same falloff so it is zero at the radius -- strictly inside the island, so the cut
        # still cannot tear. This runs BEFORE the conform: the conform has to be the last thing
        # that touches these positions, or the height would push the ground back off the object.
        image, uv_name, midlevel, height = detail
        uv = self.node("GeometryNodeInputNamedAttribute", x + 800, y - 400, data_type="FLOAT_VECTOR")
        self.link(uv_name, uv.inputs["Name"])
        img = self.node("GeometryNodeImageTexture", x + 1000, y - 400)
        self.link(image, img.inputs["Image"])
        self.link(uv.outputs["Attribute"], img.inputs["Vector"])
        # Midlevel defaults to 0 so an unset image, which samples as black, displaces by nothing.
        h = self.node("ShaderNodeMath", x + 1200, y - 400, operation="SUBTRACT")
        self.link(img.outputs["Color"], h.inputs[0])
        self.link(midlevel, h.inputs[1])
        amt = self.math(x + 1400, y - 400, "MULTIPLY", h.outputs["Value"], height)
        amt = self.math(x + 1600, y - 400, "MULTIPLY", amt, self.ramp(x + 1400, y - 600, dist, radius, 1.0, 0.0))
        detail_sp = self.node("GeometryNodeSetPosition", x + 1800, y)
        self.link(sub.outputs["Mesh"], detail_sp.inputs["Geometry"])
        self.link(self.vmath(x + 1600, y - 200, "SCALE", self.nor, scale=amt), detail_sp.inputs["Offset"])

        w = self.ramp(x + 1600, y + 300, dist, radius, conform, 0.0)
        # Land slightly *inside* the object rather than exactly on it, so the conformed ground is
        # buried instead of coplanar -- coplanar would z-fight along the whole contact band.
        sink = self.vmath(x + 1600, y + 200, "SCALE", sink_dir, scale=offset)
        goal = self.vmath(x + 1800, y + 200, "SUBTRACT", closest, sink)
        move = self.vmath(x + 2000, y + 200, "SCALE",
                          self.vmath(x + 2000, y + 300, "SUBTRACT", goal, self.pos), scale=w)
        sp = self.node("GeometryNodeSetPosition", x + 2200, y)
        self.link(detail_sp.outputs["Geometry"], sp.inputs["Geometry"])
        self.link(move, sp.inputs["Offset"])
        # Deliberately no Set Shade Smooth here. Forcing the bent faces smooth hides faceting on
        # a coarse ground, but it also flips shading mode at exactly the radius: smooth inside,
        # whatever the ground already was outside -- a hard crease ring on any ground with real
        # surface detail. Contact Subdivisions fixes the faceting at its source instead.

        dense = self.node("GeometryNodeJoinGeometry", x + 2400, y)
        self.link(sep.outputs["Inverted"], dense.inputs["Geometry"])
        self.link(sp.outputs["Geometry"], dense.inputs["Geometry"])
        # Separate Geometry duplicates the vertices along the cut, which splits their normals and
        # would draw a crease ring on any ground that is not dead flat. Weld them back -- but only
        # vertices on an open edge: every cut vertex is on one on both sides of the join (plus the
        # ground's own rim, which is harmless to include), and merging the whole floor would cost
        # a KD-tree over every vertex of it. The cut vertices are frozen, so both copies are still
        # bit-identical and the tiny distance is enough.
        open_edges = self.node("GeometryNodeInputMeshEdgeNeighbors", x + 2400, y - 200)
        is_open = self.math(x + 2400, y - 300, "COMPARE", open_edges.outputs["Face Count"], 1.0)
        is_open.node.inputs[2].default_value = 0.5
        cut = self.node("GeometryNodeCaptureAttribute", x + 2600, y, domain="EDGE")
        cut.capture_items.new("BOOLEAN", "open")
        self.link(dense.outputs["Geometry"], cut.inputs["Geometry"])
        self.link(is_open, cut.inputs["open"])
        weld = self.node("GeometryNodeMergeByDistance", x + 2800, y)
        self.link(cut.outputs["Geometry"], weld.inputs["Geometry"])
        # Booleans adapt edge -> point with OR, so a vertex is selected if any of its edges is open.
        self.link(cut.outputs["open"], weld.inputs["Selection"])
        weld.inputs["Distance"].default_value = 1e-5
        return weld.outputs["Geometry"]

    def switch(self, x, y, cond, false, true, kind="GEOMETRY"):
        sw = self.node("GeometryNodeSwitch", x, y, input_type=kind)
        self.link(cond, sw.inputs[0])
        self.feed(sw.inputs[1], false)
        self.feed(sw.inputs[2], true)
        return sw.outputs["Output"]

    def set_ground_material(self, x, y, geo, target_mat, ground_mat):
        """Swap the target's active material for its corrected copy on the ground geometry."""
        sel = self.node("GeometryNodeMaterialSelection", x, y - 200)
        self.link(target_mat, sel.inputs["Material"])
        sm = self.node("GeometryNodeSetMaterial", x + 200, y)
        self.link(geo, sm.inputs["Geometry"])
        self.link(sel.outputs["Selection"], sm.inputs["Selection"])
        self.link(ground_mat, sm.inputs["Material"])
        return sm.outputs["Geometry"]


def _build_group(ng):
    _socket(ng, "Geometry", "NodeSocketGeometry", "OUTPUT")
    _socket(ng, "Geometry", "NodeSocketGeometry")
    _socket(ng, "Target", "NodeSocketObject")
    _socket(ng, "Contact Distance", "NodeSocketFloat", default_value=0.3, min_value=0.0, subtype="DISTANCE")
    _socket(ng, "Surface Offset", "NodeSocketFloat", default_value=0.002, subtype="DISTANCE")
    _socket(ng, "Displace Ground", "NodeSocketBool", default_value=True)
    _socket(ng, "Ground Conform", "NodeSocketFloat", default_value=1.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    _socket(ng, "Contact Subdivisions", "NodeSocketInt", default_value=2, min_value=0, max_value=5)
    _socket(ng, "Ground Detail", "NodeSocketFloat", default_value=0.0, subtype="DISTANCE")
    _socket(ng, "Detail Image", "NodeSocketImage")
    _socket(ng, "Detail UV Map", "NodeSocketString", default_value="UVMap")
    _socket(ng, "Detail Midlevel", "NodeSocketFloat", default_value=0.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    _socket(ng, "Normal Blend Distance", "NodeSocketFloat", default_value=0.2, min_value=0.0, subtype="DISTANCE")
    _socket(ng, "Material Blend Distance", "NodeSocketFloat", default_value=0.3, min_value=0.0, subtype="DISTANCE")
    _socket(ng, "Match Target Textures", "NodeSocketBool", default_value=True)
    _socket(ng, "Target UV Map", "NodeSocketString", default_value="UVMap")
    # Internal bindings, configured by the add-on rather than the user. "Shared Ground" defaults
    # to off so a group rebuilt under an old file keeps every existing blend in joined mode.
    _socket(ng, "Target Material", "NodeSocketMaterial")
    _socket(ng, "Ground Material", "NodeSocketMaterial")
    _socket(ng, "Shared Ground", "NodeSocketBool", default_value=False)

    t = _Tree(ng)
    link = t.link
    gin = t.node("NodeGroupInput", -1500, 0)
    gout = t.node("NodeGroupOutput", 3600, 0)
    g = gin.outputs

    # --- every distance is in world metres (see _Tree.self_scale) ---
    scale = t.self_scale(-1500, 700)

    def local(name, y):
        return t.math(-500, y, "DIVIDE", g[name], scale)

    contact = local("Contact Distance", 700)
    offset = local("Surface Offset", 650)
    normal_dist = local("Normal Blend Distance", 600)
    material_dist = local("Material Blend Distance", 550)
    detail_dist = local("Ground Detail", 500)

    raw = t.target(-1300, -260, g["Target"])
    d_src, _ = t.proximity(-900, -260, raw)

    # --- 'blend' attribute for mixing materials in the shader (1 at contact, 0 away) ---
    store = t.node("GeometryNodeStoreNamedAttribute", -300, 0, data_type="FLOAT", domain="POINT")
    store.inputs["Name"].default_value = ATTR
    link(g["Geometry"], store.inputs["Geometry"])
    link(t.ramp(-500, -700, d_src, material_dist, 1.0, 0.0), store.inputs["Value"])

    # --- the settings the shared ground needs, carried on the geometry ---
    # The ground proxy reads every hero through one Collection Info and cannot see their
    # modifiers, yet a pebble and a boulder need different contact radii. So each hero writes
    # its own (Contact Distance, conform weight, Surface Offset) onto its points, in WORLD
    # metres: the proxy converts with its own scale. Displace Ground off zeroes the weight, so
    # a hero that has opted out leaves the ground alone even before it leaves the collection.
    weight = t.math(-500, 300, "MULTIPLY", g["Ground Conform"], g["Displace Ground"])
    params = t.node("ShaderNodeCombineXYZ", -300, 300)
    link(g["Contact Distance"], params.inputs[0])
    link(weight, params.inputs[1])
    link(g["Surface Offset"], params.inputs[2])
    carry = t.node("GeometryNodeStoreNamedAttribute", -100, 0, data_type="FLOAT_VECTOR", domain="POINT")
    carry.inputs["Name"].default_value = PARAMS_ATTR
    link(store.outputs["Geometry"], carry.inputs["Geometry"])
    link(params.outputs[0], carry.inputs["Value"])
    src = carry.outputs["Geometry"]

    d_tgt, p_tgt = t.proximity(0, -560, src)
    sink_dir = t.sample(0, -760, src, t.nor)
    detail = (g["Detail Image"], g["Detail UV Map"], g["Detail Midlevel"], detail_dist)

    def conform(y, ground, sink):
        return t.conform(200, y, ground, d_tgt, p_tgt, contact, contact, g["Ground Conform"],
                         offset, sink, g["Contact Subdivisions"], detail)

    # Joined mode: the whole target, conformed, is joined into this object's output.
    full = conform(-900, raw, sink_dir)
    # Shared mode: the proxy draws the ground, but this object still has to weld its normals to,
    # and project its UVs from, the ground it meets -- so conform just the patch of the target
    # this object can see (anything farther than every blend distance never influences it) and
    # sample from that. Nothing of the patch is output.
    reach = t.math(-300, -2700, "MAXIMUM", contact, t.math(-500, -2700, "MAXIMUM", normal_dist, material_dist))
    patch = t.island(-300, -2600, raw, d_tgt, reach)
    # It sinks along the true normal, as the proxy does, so the two land on the same surface.
    shared_ground = conform(-2900, patch.outputs["Selection"], t.sample(0, -2900, src, t.true_nor))
    conformed = t.switch(3000, -1600, g["Shared Ground"], full, shared_ground)

    # What the hero's shading reads the ground from. With Displace Ground on, the conformed
    # ground it is welded to; with it off, no ground copy is drawn, so welding normals or
    # sampling UVs from one would match the hero to a surface that is not there -- read the
    # raw target instead. Switch only evaluates the branch it picks, so with Displace Ground
    # off nothing of either conform pipeline is computed at all.
    shade_ref = t.switch(3100, -1700, g["Displace Ground"], raw, conformed)

    # Sample UVs from the conformed ground, whose original corner UVs have survived
    # subdivision and displacement. Sampling before the conform makes the two sides slide
    # apart. Keep this downstream of the conform to avoid a source/target dependency cycle.
    target_uv = t.node("GeometryNodeInputNamedAttribute", 3000, -1900, data_type="FLOAT_VECTOR")
    link(g["Target UV Map"], target_uv.inputs["Name"])
    mapped_src = t.node("GeometryNodeStoreNamedAttribute", 3200, -1700, data_type="FLOAT2", domain="CORNER")
    mapped_src.inputs["Name"].default_value = TARGET_UV_ATTR
    link(src, mapped_src.inputs["Geometry"])
    link(t.sample(3100, -1900, shade_ref, target_uv.outputs["Attribute"]), mapped_src.inputs["Value"])
    uv_src = t.switch(3300, -1600, g["Match Target Textures"], src, mapped_src.outputs["Geometry"])

    # --- weld the shading: near the target, adopt the target's normal ---
    # Measured on the hero alone and captured, rather than evaluated on the joined mesh: there
    # it would run two nearest-surface queries for every corner of the ground copy too, only
    # to have each one find itself. Corners of the ground carry weight 0 after the join (a
    # captured attribute missing from one side of a join reads as zero), so they keep exactly
    # their own normal.
    weld_n = t.node("GeometryNodeCaptureAttribute", 3400, -1300, domain="CORNER")
    weld_n.capture_items.new("FLOAT", "weight")
    weld_n.capture_items.new("VECTOR", "normal")
    link(uv_src, weld_n.inputs["Geometry"])
    link(t.ramp(3200, -1250, t.proximity(3000, -1250, shade_ref)[0], normal_dist, 1.0, 0.0),
         weld_n.inputs["weight"])
    link(t.sample(3000, -1400, shade_ref, t.nor), weld_n.inputs["normal"])
    shaded_src = weld_n.outputs["Geometry"]

    # Object/Generated coordinates on joined ground otherwise use the hero's transform
    # and texture space. Replace only the active target material with a corrected copy.
    corrected = t.set_ground_material(3000, -2100, full, g["Target Material"], g["Ground Material"])
    ground_out = t.switch(3300, -2100, g["Match Target Textures"], full, corrected)
    join = t.node("GeometryNodeJoinGeometry", 3300, -100)
    link(ground_out, join.inputs["Geometry"])
    link(shaded_src, join.inputs["Geometry"])
    joined = t.node("FunctionNodeBooleanMath", 3200, 100, operation="NIMPLY")
    link(g["Displace Ground"], joined.inputs[0])
    link(g["Shared Ground"], joined.inputs[1])
    out = t.switch(3400, 0, joined.outputs[0], shaded_src, join.outputs["Geometry"])

    mix = t.node("ShaderNodeMix", 3400, -1000, data_type="VECTOR")
    link(weld_n.outputs["weight"], mix.inputs[0])
    link(t.nor, mix.inputs[4])
    link(weld_n.outputs["normal"], mix.inputs[5])

    # CORNER, not POINT: `FREE` mode has no selection input, so the custom normal is
    # written mesh-wide. On POINT that replaces every corner normal with the averaged
    # vertex normal and flattens sharp edges into a blob. On CORNER, Input Normal is the
    # existing corner normal, so far from the target the write is a no-op.
    sn = t.node("GeometryNodeSetMeshNormal", 3500, 0, mode="FREE", domain="CORNER")
    link(out, sn.inputs["Mesh"])
    link(mix.outputs[1], sn.inputs["Custom Normal"])
    link(sn.outputs["Mesh"], gout.inputs[0])
    return ng


def _build_ground_group(ng):
    """The shared ground: the target conformed to every object in a collection at once.

    Lives on the ground proxy, an empty mesh parented to the target with an identity transform,
    so RELATIVE Object Info yields the target in its own local space and Object texture
    coordinates match the target. It reads the heroes; the heroes read the raw target, never
    the proxy -- no cycle, and one ground surface however many objects sit on it.
    """
    _socket(ng, "Geometry", "NodeSocketGeometry", "OUTPUT")
    _socket(ng, "Geometry", "NodeSocketGeometry")
    _socket(ng, "Target", "NodeSocketObject")
    _socket(ng, "Objects", "NodeSocketCollection")
    _socket(ng, "Contact Subdivisions", "NodeSocketInt", default_value=2, min_value=0, max_value=5)
    _socket(ng, "Ground Detail", "NodeSocketFloat", default_value=0.0, subtype="DISTANCE")
    _socket(ng, "Detail Image", "NodeSocketImage")
    _socket(ng, "Detail UV Map", "NodeSocketString", default_value="UVMap")
    _socket(ng, "Detail Midlevel", "NodeSocketFloat", default_value=0.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    _socket(ng, "Target Material", "NodeSocketMaterial")
    _socket(ng, "Ground Material", "NodeSocketMaterial")

    t = _Tree(ng)
    link = t.link
    gin = t.node("NodeGroupInput", -1500, 0)
    gout = t.node("NodeGroupOutput", 3600, 0)
    g = gin.outputs
    scale = t.self_scale(-1500, 700)
    raw = t.target(-1300, -260, g["Target"])
    coll = t.node("GeometryNodeCollectionInfo", -1300, 300, transform_space="RELATIVE")
    link(g["Objects"], coll.inputs["Collection"])
    heroes = t.node("GeometryNodeRealizeInstances", -1100, 300)
    link(coll.outputs[0], heroes.inputs["Geometry"])
    heroes = heroes.outputs["Geometry"]

    # Per-vertex settings: those of the nearest object, read off its surface (each object
    # stores them uniformly on its points, so the interpolation is exact). Where the nearest
    # object changes from one with a large radius to one with a small radius, the weight jumps;
    # that discontinuity is inherent to conforming one surface to several objects with
    # different settings, and it lies where the two influences meet, not at the cut.
    param = t.node("GeometryNodeInputNamedAttribute", -1100, 500, data_type="FLOAT_VECTOR")
    param.inputs["Name"].default_value = PARAMS_ATTR
    sampled = t.node("ShaderNodeSeparateXYZ", -700, 500)
    link(t.sample(-900, 500, heroes, param.outputs["Attribute"]), sampled.inputs[0])
    radius = t.math(-500, 600, "DIVIDE", sampled.outputs[0], scale)
    offset = t.math(-500, 500, "DIVIDE", sampled.outputs[2], scale)
    # The island needs one bound that is >= the radius at every point (see _Tree.conform), and
    # the sampled radius is not that: a face centre can sit nearest a pebble while one of its
    # corners sits nearest a boulder. The largest radius of any object is.
    own = t.node("ShaderNodeSeparateXYZ", -900, 800)
    link(param.outputs["Attribute"], own.inputs[0])
    stat = t.node("GeometryNodeAttributeStatistic", -700, 800, data_type="FLOAT", domain="POINT")
    link(heroes, stat.inputs["Geometry"])
    link(own.outputs[0], stat.inputs["Attribute"])
    island_radius = t.math(-500, 800, "DIVIDE", stat.outputs["Max"], scale)

    # Sink along the objects' true surface normal: their output carries the welded custom
    # normals, which near the contact point along the ground, not out of the object.
    sink_dir = t.sample(-900, 900, heroes, t.true_nor)

    dist, closest = t.proximity(-900, 100, heroes)
    detail = (g["Detail Image"], g["Detail UV Map"], g["Detail Midlevel"],
              t.math(-500, 400, "DIVIDE", g["Ground Detail"], scale))
    conformed = t.conform(0, 0, raw, dist, closest, radius, island_radius, sampled.outputs[1],
                          offset, sink_dir, g["Contact Subdivisions"], detail)
    # With no objects, or none that may move the ground (all have Displace Ground off or Ground
    # Conform 0, which they write as weight 0), there is nothing to do: pass the target through
    # untouched without evaluating any of the conform. The statistic of an empty geometry is 0.
    weight = t.node("GeometryNodeAttributeStatistic", -700, 1100, data_type="FLOAT", domain="POINT")
    link(heroes, weight.inputs["Geometry"])
    link(own.outputs[1], weight.inputs["Attribute"])
    active = t.math(-500, 1100, "GREATER_THAN", weight.outputs["Max"], 0.0)
    ground = t.switch(3000, 0, active, raw, conformed)
    out = t.set_ground_material(3200, 0, ground, g["Target Material"], g["Ground Material"])
    link(out, gout.inputs[0])
    return ng


def _surface(mat):
    """Active output node of `mat` and the socket feeding its Surface, or (None, None)."""
    tree = node_tree(mat)
    if tree is None:
        return None, None
    out = next((n for n in tree.nodes
                if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if not out or not out.inputs["Surface"].is_linked:
        return None, None
    return out, out.inputs["Surface"].links[0].from_socket


_SKIP_PROPS = {"rna_type", "name", "parent", "internal_links", "dimensions", "select",
               "location", "location_absolute"}


def _copy_struct(src, dst, depth=0):
    """Copy every setting of RNA struct `src` onto `dst`.

    Plain writable properties are assigned. A lot of node state is not plain, though: ColorRamp,
    CurveMapping, ImageUser, ColorMapping and TexMapping hang off read-only pointers, so a loop
    over writable properties walks straight past them and the copy silently comes back with a
    default black-to-white ramp, a straight curve, or frame 1 of an image sequence. Recurse into
    every read-only pointer that holds a settings struct (never an ID: those are shared
    datablocks and are assigned, not copied into). The two that also carry a *collection* --
    ramp elements and curve points -- cannot be reached generically and are rebuilt by hand.
    """
    for prop in src.bl_rna.properties:
        ident = prop.identifier
        if ident in _SKIP_PROPS or prop.type == "COLLECTION":
            continue
        value = getattr(src, ident)
        if prop.type == "POINTER" and prop.is_readonly:
            if value is not None and not isinstance(value, bpy.types.ID) and depth < 4:
                _copy_struct(value, getattr(dst, ident), depth + 1)
            continue
        if prop.is_readonly:
            continue
        try:
            setattr(dst, ident, value)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    if isinstance(src, bpy.types.ColorRamp):
        _copy_color_ramp(src, dst)
    elif isinstance(src, bpy.types.CurveMapping):
        _copy_curve_mapping(src, dst)


def _copy_color_ramp(src, dst):
    elements = dst.elements
    # A ramp cannot drop below one element, and new() inserts sorted by position, so shrink to
    # one, overwrite it, and let new() place the rest.
    while len(elements) > 1:
        elements.remove(elements[-1])
    first = src.elements[0]
    elements[0].position = first.position
    elements[0].color = first.color
    for element in list(src.elements)[1:]:
        elements.new(element.position).color = element.color


def _copy_curve_mapping(src, dst):
    for src_curve, dst_curve in zip(src.curves, dst.curves):
        points = dst_curve.points
        while len(points) < len(src_curve.points):
            points.new(0.5, 0.5)
        while len(points) > len(src_curve.points):
            points.remove(points[-1])
        # Source points are already sorted; assign in order and let update() re-sort once.
        for a, b in zip(src_curve.points, points):
            b.location = a.location
            b.handle_type = a.handle_type
    dst.update()


def _absolute_location(node):
    if hasattr(node, "location_absolute"):
        return tuple(node.location_absolute)
    x, y = node.location
    parent = node.parent
    while parent is not None:
        x, y = x + parent.location[0], y + parent.location[1]
        parent = parent.parent
    return x, y


def _set_absolute_location(node, xy):
    if hasattr(node, "location_absolute"):
        node.location_absolute = xy
        return
    px = py = 0.0
    parent = node.parent
    while parent is not None:
        px, py = px + parent.location[0], py + parent.location[1]
        parent = parent.parent
    node.location = (xy[0] - px, xy[1] - py)


def _copy_nodes(src_tree, dst_tree, offset):
    """Clone every non-output node of `src_tree` into `dst_tree`. Returns {old: new}."""
    m = {}
    for n in src_tree.nodes:
        if n.type == "OUTPUT_MATERIAL":
            continue
        c = dst_tree.nodes.new(n.bl_idname)
        _copy_struct(n, c)
        for a, b in zip(n.inputs, c.inputs):
            try:
                b.default_value = a.default_value
            except Exception:
                pass
        # RGB and Value nodes keep their value on the *output* socket.
        for a, b in zip(n.outputs, c.outputs):
            try:
                b.default_value = a.default_value
            except Exception:
                pass
        m[n] = c
    # Parent only once every frame exists, then place by absolute location: a child's
    # `location` is relative to its frame, so offsetting it as if absolute would shift it twice.
    for n, c in m.items():
        if n.parent in m:
            c.parent = m[n.parent]
    for n, c in m.items():
        x, y = _absolute_location(n)
        _set_absolute_location(c, (x + offset[0], y + offset[1]))
    for l in src_tree.links:
        f, t = m.get(l.from_node), m.get(l.to_node)
        if not f or not t:
            continue
        dst_tree.links.new(f.outputs[list(l.from_node.outputs).index(l.from_socket)],
                           t.inputs[list(l.to_node.inputs).index(l.to_socket)])
    return m


def _target_uv_name(tgt):
    layers = tgt.data.uv_layers
    render = next((layer for layer in layers if layer.active_render), None)
    return render.name if render else (layers.active.name if layers.active else "")


def _match_texture_nodes(tree, tgt, uv_name, project_uv, groups=None, node_names=None):
    """Bind a copied shader to target space; never edit shared shader groups in place."""
    groups = {} if groups is None else groups
    warnings = set()
    nodes = [n for n in tree.nodes if node_names is None or n.name in node_names]
    uv_node_name = None
    generated_node_name = None

    def new(kind):
        node = tree.nodes.new(kind)
        node[GENERATED_KEY] = "target"
        return node

    def uv_output():
        nonlocal uv_node_name
        if uv_node_name is None:
            node = new("ShaderNodeUVMap")
            node.uv_map = TARGET_UV_ATTR if project_uv else uv_name
            uv_node_name = node.name
            if uv_name not in tgt.data.uv_layers:
                warnings.add("Target UV map is missing; check Target UV Map")
        return tree.nodes[uv_node_name].outputs["UV"]

    def generated_output():
        nonlocal generated_node_name
        if generated_node_name is None:
            coord = new("ShaderNodeTexCoord")
            coord.object = tgt
            coord_name = coord.name
            subtract = new("ShaderNodeVectorMath")
            subtract.operation = "SUBTRACT"
            subtract.inputs[1].default_value = tgt.data.texspace_location
            subtract_name = subtract.name
            tree.links.new(tree.nodes[coord_name].outputs["Object"], subtract.inputs[0])
            divide = new("ShaderNodeVectorMath")
            divide.operation = "DIVIDE"
            divide.inputs[1].default_value = tuple(2 * v for v in tgt.data.texspace_size)
            divide_name = divide.name
            tree.links.new(tree.nodes[subtract_name].outputs["Vector"], divide.inputs[0])
            add = new("ShaderNodeVectorMath")
            add.operation = "ADD"
            add.inputs[1].default_value = (0.5, 0.5, 0.5)
            generated_node_name = add.name
            tree.links.new(tree.nodes[divide_name].outputs["Vector"], add.inputs[0])
        return tree.nodes[generated_node_name].outputs["Vector"]

    def redirect(node_name, output_name, replacement):
        destinations = [(link.to_node.name, list(link.to_node.inputs).index(link.to_socket))
                        for link in tree.nodes[node_name].outputs[output_name].links]
        for name, index in destinations:
            tree.links.new(replacement, tree.nodes[name].inputs[index])

    procedural = {"TEX_NOISE", "TEX_VORONOI", "TEX_WAVE", "TEX_CHECKER", "TEX_BRICK",
                  "TEX_GRADIENT", "TEX_MAGIC", "TEX_WHITE_NOISE", "TEX_GABOR"}
    for original in nodes:
        name, kind = original.name, original.type
        if kind == "GROUP" and original.node_tree:
            old = original.node_tree
            if old not in groups:
                copied = old.copy()
                copied.name = old.name + " (Contact Blend)"
                copied.use_fake_user = False
                copied[TEXTURE_DATA_KEY] = True
                groups[old] = copied
                warnings |= _match_texture_nodes(copied, tgt, uv_name, project_uv, groups)
            tree.nodes[name].node_tree = groups[old]
        elif kind == "TEX_COORD":
            if original.from_instancer:
                warnings.add("Instancer texture coordinates are not projected")
                continue
            if original.outputs["Generated"].is_linked:
                redirect(name, "Generated", generated_output())
            if tree.nodes[name].outputs["UV"].is_linked:
                redirect(name, "UV", uv_output())
            if tree.nodes[name].object is None:
                tree.nodes[name].object = tgt
        elif kind in {"UVMAP", "NORMAL_MAP"}:
            node = tree.nodes[name]
            uses_uv = kind == "UVMAP" or node.space == "TANGENT"
            if uses_uv:
                if kind == "UVMAP" and node.from_instancer:
                    warnings.add("Instancer UV coordinates are not projected")
                elif not node.uv_map or node.uv_map == uv_name:
                    node.uv_map = TARGET_UV_ATTR if project_uv else uv_name
                    if uv_name not in tgt.data.uv_layers:
                        warnings.add("Target UV map is missing; check Target UV Map")
                elif project_uv:
                    warnings.add(f"Additional UV map '{node.uv_map}' is not projected")
        elif kind == "ATTRIBUTE" and project_uv:
            if original.attribute_type == "GEOMETRY" and original.attribute_name == uv_name:
                tree.nodes[name].attribute_name = TARGET_UV_ATTR
        elif kind == "TEX_IMAGE" and not original.inputs["Vector"].is_linked:
            output = uv_output()
            tree.links.new(output, tree.nodes[name].inputs["Vector"])
        elif kind in procedural and not original.inputs["Vector"].is_linked:
            output = generated_output()
            tree.links.new(output, tree.nodes[name].inputs["Vector"])
    return warnings


def _cleanup_texture_data():
    """Discard unused copies made by refresh/removal, retaining anything used elsewhere."""
    for mat in list(bpy.data.materials):
        if mat.get(TEXTURE_DATA_KEY) and mat.users == 0:
            bpy.data.materials.remove(mat)
    while True:
        unused = [ng for ng in bpy.data.node_groups if ng.users == 0
                  and (ng.get(TEXTURE_DATA_KEY) or ng.get(GENERATED_KEY) == "breakup")]
        if not unused:
            break
        for ng in unused:
            bpy.data.node_groups.remove(ng)


def _texture_settings(src, tgt):
    mod = find_mod(src)
    uv_name = _target_uv_name(tgt)
    if not mod:
        return True, uv_name, None
    ids = input_ids(mod.node_group)
    if not mod.get("cb_uv_initialized") or mod.get("cb_uv_target") != tgt:
        mod[ids["Target UV Map"]] = uv_name
        mod["cb_uv_initialized"] = True
        mod["cb_uv_target"] = tgt
    return bool(mod[ids["Match Target Textures"]]), mod[ids["Target UV Map"]], mod


def _material_mix(mat):
    tree = node_tree(mat)
    if tree is None:
        return None
    nodes = tree.nodes
    tagged = next((n for n in nodes if n.type == "MIX_SHADER"
                   and n.get(GENERATED_KEY) == "mix"), None)
    if tagged:
        return tagged
    return next((n for n in nodes if n.type == "MIX_SHADER"
                 and not n.get(GENERATED_KEY) and n.name in (GROUP,) + LEGACY_NAMES), None)


def _build_breakup(ng):
    """Edge breakup: roughen the linear `blend` ramp without moving its ends.

    The geometry hands the shader a smooth per-vertex ramp, which reads as an airbrushed band.
    Push it around with noise, scaled by an envelope b*(1-b) that is exactly 0 at b=0 and b=1:
    however strong the breakup, nothing speckles far from the contact (b=0) and nothing
    punches holes where the object meets the ground (b=1). Sharpness then steepens the result
    around 0.5. With Breakup and Sharpness at their default 0 every step reduces to the
    identity exactly -- b + 0, then a map range from [0, 1] to [0, 1] -- so adding this node
    to an existing material changes nothing until someone turns it up.

    Noise is in Object space, so it is the hero's own coordinates: it does not swim when UVs
    change, and it moves with the object rather than with the camera.
    """
    _socket(ng, "Blend", "NodeSocketFloat", "OUTPUT")
    _socket(ng, "Blend", "NodeSocketFloat", default_value=0.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    _socket(ng, "Noise Scale", "NodeSocketFloat", default_value=5.0, min_value=0.0)
    _socket(ng, "Breakup", "NodeSocketFloat", default_value=0.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    _socket(ng, "Sharpness", "NodeSocketFloat", default_value=0.0, min_value=0.0, max_value=1.0, subtype="FACTOR")
    link = ng.links.new
    gin = _node(ng, "NodeGroupInput", -900, 0)
    gout = _node(ng, "NodeGroupOutput", 900, 0)
    g = gin.outputs

    def math(x, y, op, a, b, clamp=False):
        m = _node(ng, "ShaderNodeMath", x, y, operation=op, use_clamp=clamp)
        for sock, v in ((m.inputs[0], a), (m.inputs[1], b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            else:
                link(v, sock)
        return m.outputs["Value"]

    coord = _node(ng, "ShaderNodeTexCoord", -900, -300)
    noise = _node(ng, "ShaderNodeTexNoise", -700, -300)
    link(coord.outputs["Object"], noise.inputs["Vector"])
    link(g["Noise Scale"], noise.inputs["Scale"])
    centred = math(-500, -300, "SUBTRACT", noise.outputs["Fac"], 0.5)
    envelope = math(-500, 0, "MULTIPLY", g["Blend"], math(-700, 100, "SUBTRACT", 1.0, g["Blend"]))
    # Noise Fac sits roughly in 0.2..0.8, so *8 lets Breakup = 1 swing the middle of the ramp
    # (envelope 0.25) all the way to 0 or 1.
    delta = math(-100, -200, "MULTIPLY",
                 math(-300, -200, "MULTIPLY", math(-300, -350, "MULTIPLY", centred, g["Breakup"]),
                      envelope), 8.0)
    broken = math(100, 0, "ADD", g["Blend"], delta, clamp=True)
    # 0.49, not 0.5: at Sharpness 1 the window is [0.49, 0.51] -- a hard edge, but never a
    # zero-width range to divide by.
    lo = math(100, -300, "MULTIPLY", g["Sharpness"], 0.49)
    hi = math(300, -300, "SUBTRACT", 1.0, lo)
    mr = _node(ng, "ShaderNodeMapRange", 500, 0, interpolation_type="LINEAR", clamp=True)
    link(broken, mr.inputs[0])
    link(lo, mr.inputs[1])
    link(hi, mr.inputs[2])
    mr.inputs[3].default_value = 0.0
    mr.inputs[4].default_value = 1.0
    link(mr.outputs[0], gout.inputs["Blend"])


def breakup_group():
    """The shared edge-breakup shader group, found by its stamp and created on first use."""
    ng = next((ng for ng in bpy.data.node_groups if ng.bl_idname == "ShaderNodeTree"
               and ng.get(GENERATED_KEY) == "breakup"), None)
    if ng is None:
        ng = bpy.data.node_groups.new(BREAKUP_GROUP, "ShaderNodeTree")
        ng[GENERATED_KEY] = "breakup"
        _build_breakup(ng)
        ng[VERSION_KEY] = BREAKUP_VERSION
    return ng


def _is_breakup(node):
    return node.type == "GROUP" and node.get(GENERATED_KEY) == "breakup"


def breakup_node(mat):
    """The edge-breakup node in `mat`, whose inputs are the user's breakup settings, or None."""
    tree = node_tree(mat)
    return next((n for n in tree.nodes if _is_breakup(n)), None) if tree else None


def _wire_breakup(tree, mix_name):
    """Put a breakup node between the `blend` Attribute node and the mix factor.

    Only when the factor still comes straight from that Attribute node -- the layout every
    earlier release built. If the user has rewired it, that wiring is theirs; leave it alone.
    """
    socket = tree.nodes[mix_name].inputs[0]
    if not socket.is_linked:
        return None
    upstream = socket.links[0].from_node
    if _is_breakup(upstream):
        return upstream
    if upstream.type != "ATTRIBUTE" or upstream.attribute_name != ATTR:
        return None
    attr_name = upstream.name
    out_idx = list(upstream.outputs).index(socket.links[0].from_socket)
    ax, ay = upstream.location
    mx = tree.nodes[mix_name].location[0]
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = breakup_group()
    node[GENERATED_KEY] = "breakup"
    node.label = "Edge Breakup"
    node.location = ((ax + mx) / 2, ay - 150)
    name = node.name
    tree.links.new(tree.nodes[attr_name].outputs[out_idx], tree.nodes[name].inputs["Blend"])
    tree.links.new(tree.nodes[name].outputs["Blend"], tree.nodes[mix_name].inputs[0])
    return tree.nodes[name]


def _private_material(src, slot):
    """Use an object-linked copy, including when the object's mesh data is shared."""
    mat = slot.material
    shared = any(o is not src and any(s.material == mat for s in o.material_slots)
                 for o in bpy.data.objects)
    if not mat.get(ISOLATED_KEY) or shared or slot.link != "OBJECT":
        mat = mat.copy()
        mat.name += " (Contact Blend)"
        mat[ISOLATED_KEY] = True
        slot.link = "OBJECT"
        slot.material = mat
    return mat


def _prune_generated(tree, names):
    """Delete owned nodes, retaining any branches the user has wired elsewhere."""
    keep = {l.from_node.name for l in tree.links
            if l.from_node.name in names and l.to_node.name not in names}
    while True:
        upstream = {l.from_node.name for l in tree.links
                    if l.to_node.name in keep and l.from_node.name in names}
        if upstream <= keep:
            break
        keep |= upstream
    for name in names - keep:
        node = tree.nodes.get(name)
        if node:
            tree.nodes.remove(node)
    for name in keep:
        node = tree.nodes.get(name)
        if node and GENERATED_KEY in node:
            del node[GENERATED_KEY]


def setup_material(src, tgt, refresh=False):
    """Mix the target's shader into the source's, driven by the `blend` attribute.

    Every slot of the source holding a node material with a linked Surface is blended, each in
    its own private copy: a rock with a separate moss or cap material has those faces touching
    the ground too, and blending only the active slot left them ending in a hard line there.
    Slots without a usable material are skipped; this fails only when none is usable. The
    target always contributes its active material.

    Returns a message on failure, None on success. Idempotent: an already-wired material is
    left alone. Explicit refresh replaces only the generated target branch, preserving the
    source shader, user nodes and breakup settings.
    """
    if src is tgt:
        return "The blend object cannot be its own target"
    tmat = tgt.active_material
    _, tshader = _surface(tmat)
    if not any(_surface(slot.material)[1] for slot in src.material_slots):
        return f"'{src.name}' has no node material with a linked Surface"
    if not tshader:
        return f"'{tgt.name}' has no node material with a linked Surface"
    match_textures, uv_name, mod = _texture_settings(src, tgt)
    groups, done, changed = {}, set(), False
    for slot in src.material_slots:
        if not _surface(slot.material)[1]:
            continue
        smat = _private_material(src, slot)
        if smat.as_pointer() in done:     # one private material sitting in several slots
            continue
        done.add(smat.as_pointer())
        changed |= _blend_material(smat, tmat, tgt, match_textures, uv_name, groups, refresh)
    # A shared blend draws no ground of its own; the proxy carries the corrected ground material.
    if mod and changed and not is_shared(mod):
        ids = input_ids(mod.node_group)
        mod[ids["Target Material"]] = tmat
        mod[ids["Ground Material"]] = _corrected_ground(tmat, tgt, uv_name) if match_textures else tmat
    _cleanup_texture_data()
    return None


def _corrected_ground(tmat, tgt, uv_name=None):
    """A copy of the target material bound to the target's own Object/Generated space.

    The ground copy is evaluated as part of another object, whose transform and texture space
    Object and Generated coordinates would otherwise follow. UVs need no projection: the ground
    keeps the target's own.
    """
    ground = tmat.copy()
    ground.name = tmat.name + " (Contact Blend Ground)"
    ground.use_fake_user = False
    ground[TEXTURE_DATA_KEY] = True
    _match_texture_nodes(ground.node_tree, tgt, uv_name or _target_uv_name(tgt), False)
    return ground


def _blend_material(smat, tmat, tgt, match_textures, uv_name, groups, refresh):
    """Wire (or, on refresh, re-copy) the target shader into one private source material.

    Returns True if anything was changed.
    """
    sout, sshader = _surface(smat)
    _, tshader = _surface(tmat)
    st = smat.node_tree
    # Also recognise the pre-1.1 node name, and move it forward while we are here. Missing it
    # would not raise -- it would silently mix a second copy of the target shader into a
    # material that was already wired, every time the button is pressed.
    mix = _material_mix(smat)
    if mix:
        mix.name = mix.label = GROUP
        if not refresh:
            return False

    # nodes.new() can invalidate existing RNA pointers, so note how to find the sockets
    # again by name+index and re-resolve them after every node has been created
    out_name, sh_name = sout.name, sshader.node.name
    sh_idx = list(sshader.node.outputs).index(sshader)
    tgt_name, tgt_idx = tshader.node.name, list(tshader.node.outputs).index(tshader)

    old_target_nodes = {n.name for n in st.nodes if n.get(GENERATED_KEY) == "target"}
    mix_name = mix.name if mix else None
    offset = (sshader.node.location[0] - 600, sshader.node.location[1] - 900)
    m = _copy_nodes(tmat.node_tree, st, offset)
    for node in m.values():
        node[GENERATED_KEY] = "target"
    copy_name = m[tmat.node_tree.nodes[tgt_name]].name
    warnings = set()
    if match_textures:
        warnings = _match_texture_nodes(st, tgt, uv_name, True, groups=groups,
                                       node_names={node.name for node in m.values()})
    smat["cb_texture_warnings"] = "\n".join(sorted(warnings))
    if mix_name:
        st.links.new(st.nodes[copy_name].outputs[tgt_idx], st.nodes[mix_name].inputs[2])
        _prune_generated(st, old_target_nodes)
        # Materials wired before edge breakup existed get it here, on their next Update.
        _wire_breakup(st, mix_name)
        return True
    base = tuple(st.nodes[out_name].location)

    attr = st.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = ATTR
    attr[GENERATED_KEY] = "attribute"
    attr.location = (base[0] - 700, base[1] - 400)
    attr_name = attr.name
    mix = st.nodes.new("ShaderNodeMixShader")
    mix.name = mix.label = GROUP
    mix[GENERATED_KEY] = "mix"
    mix.location = (base[0] - 200, base[1])
    mix_name = mix.name

    n, mix = st.nodes, st.nodes[mix_name]
    st.links.new(n[attr_name].outputs["Fac"], mix.inputs[0])
    st.links.new(n[sh_name].outputs[sh_idx], mix.inputs[1])     # blend=0: source shader
    st.links.new(n[copy_name].outputs[tgt_idx], mix.inputs[2])  # blend=1: target shader
    st.links.new(mix.outputs[0], n[out_name].inputs["Surface"])
    _wire_breakup(st, mix_name)
    return True


def remove_material_blend(src):
    """Unwrap blend nodes without discarding edits made to the isolated material."""
    for slot in src.material_slots:
        if not _material_mix(slot.material):
            continue
        mat = _private_material(src, slot)
        tree = mat.node_tree
        mix = _material_mix(mat)
        base = mix.inputs[1].links[0].from_socket if mix.inputs[1].is_linked else None
        destinations = [link.to_socket for link in mix.outputs[0].links]
        if base:
            for socket in destinations:
                tree.links.new(base, socket)
        else:
            for link in list(mix.outputs[0].links):
                tree.links.remove(link)
        # Untagged legacy target nodes cannot be distinguished from user nodes: leave
        # them disconnected rather than risk removing part of a hand-edited shader.
        names = {n.name for n in tree.nodes if n.get(GENERATED_KEY)}
        names.add(mix.name)
        _prune_generated(tree, names)


def input_ids(ng):
    return {i.name: i.identifier for i in ng.interface.items_tree
            if i.item_type == "SOCKET" and i.in_out == "INPUT"}


def _is_blend_mod(mod):
    # By stamp, not by name: an appended object brings its group in as "Contact Blend.001",
    # and a pre-1.1 file still has "Blend To Surface" until migrate_names() runs -- enabling
    # the add-on after the file is already open misses the load handler entirely.
    return mod.type == "NODES" and mod.node_group is not None and _is_ours(mod.node_group)


def find_mod(obj):
    return obj and next((m for m in obj.modifiers if _is_blend_mod(m)), None)


def socket(mod, name, default=None):
    """Value of the modifier input called `name`, or `default` if its group has none.

    Draw code and polls run against whatever group a file brings with it -- stale, foreign or
    half-upgraded -- and a missing socket there must degrade, never raise.
    """
    if mod is None or mod.node_group is None:
        return default
    ident = input_ids(mod.node_group).get(name)
    if ident is None:
        return default
    value = mod.get(ident)
    return default if value is None else value


def set_socket(mod, name, value):
    ident = input_ids(mod.node_group).get(name)
    if ident is not None:
        mod[ident] = value
    return ident is not None


def socket_key(mod, name):
    """RNA path of input `name` on `mod`, for layout.prop(), or None."""
    ident = input_ids(mod.node_group).get(name) if mod and mod.node_group else None
    return f'["{ident}"]' if ident else None


def mod_target(mod):
    return socket(mod, "Target")


def displaces(mod):
    return bool(socket(mod, "Displace Ground", False))


def is_shared(mod):
    return bool(socket(mod, "Shared Ground", False))


def _in_layer(obj, layer):
    return layer is not None and layer.objects.get(obj.name) == obj


def is_hidden(obj, layer=None):
    """`obj.hide_get()` that cannot raise.

    The target need not be in the current view layer at all -- it can live in an excluded
    collection or in another scene and still be read by Object Info. hide_get()/hide_set()
    raise for such an object (hide_set always, hide_get on some versions), and this is called
    from panel draw and operator polls, where an exception breaks the whole sidebar. An
    object that is not in the layer cannot draw there, so it counts as hidden.
    """
    layer = layer or bpy.context.view_layer
    if not _in_layer(obj, layer):
        return True
    try:
        return obj.hide_get(view_layer=layer)
    except RuntimeError:
        return True


def _set_hidden(obj, state, layer):
    """hide_set() that skips, rather than aborts on, an object outside `layer`."""
    if not _in_layer(obj, layer):
        return False
    try:
        obj.hide_set(state, view_layer=layer)
    except RuntimeError:
        return False
    return True


def _hide_target(tgt, ctx):
    """Remember visibility once per target and per scene/view layer before hiding."""
    if VISIBILITY_KEY not in tgt:
        tgt[VISIBILITY_KEY] = {"render": tgt.hide_render, "layers": {}}
    state = tgt[VISIBILITY_KEY]
    recorded = any(saved.get("scene") == ctx.scene and saved["layer"] == ctx.view_layer.name
                   for saved in state["layers"].values())
    # Outside this view layer there is no eye flag to record or set; the render flag below
    # still applies, and the rest of the operator must not be skipped over it.
    if not recorded and _in_layer(tgt, ctx.view_layer):
        key = str(len(state["layers"]))
        state["layers"][key] = {"scene": ctx.scene, "layer": ctx.view_layer.name,
                                "hidden": is_hidden(tgt, ctx.view_layer)}
    _set_hidden(tgt, True, ctx.view_layer)
    tgt.hide_render = True


def _restore_target(tgt):
    """Restore only after the last ground-copy user releases the target."""
    if not tgt or VISIBILITY_KEY not in tgt:
        return
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if _is_blend_mod(mod) and mod_target(mod) == tgt and displaces(mod):
                return
    state = tgt[VISIBILITY_KEY]
    # A changed flag belongs to the user; restore only values still set by us.
    if tgt.hide_render:
        tgt.hide_render = state["render"]
    for saved in state["layers"].values():
        scene = saved.get("scene")
        layer = scene.view_layers.get(saved["layer"]) if scene else None
        if layer and _in_layer(tgt, layer) and is_hidden(tgt, layer):
            _set_hidden(tgt, saved["hidden"], layer)
    del tgt[VISIBILITY_KEY]


def _sync_target_visibility(mod, ctx):
    tgt = mod_target(mod)
    old = mod.get(HIDDEN_TARGET_KEY)
    ground_on = displaces(mod)
    if old and (old != tgt or not ground_on):
        del mod[HIDDEN_TARGET_KEY]
        _restore_target(old)
    if tgt and ground_on:
        _hide_target(tgt, ctx)
        mod[HIDDEN_TARGET_KEY] = tgt


def target_leaks(mod):
    """True if the target will still draw in the render and re-cut the seam.

    The eye and the camera are separate flags. Hiding the target with the eye alone makes
    the viewport look perfect while F12 keeps drawing the original flat surface straight
    through the crater -- the seam comes back in the render only. Object Info reads hidden
    objects under both EEVEE and Cycles, so there is no cost to hiding it from both.
    """
    tgt = mod_target(mod)
    if not tgt or not displaces(mod):
        return None
    return tgt if (not tgt.hide_render or not is_hidden(tgt)) else None


def target_displaces(mod):
    """Materials on the target that move it at render time, behind the modifier's back.

    Proximity is measured against the target's *mesh*. Material displacement is applied by
    the renderer after the whole modifier stack, so Geometry Nodes cannot see it: the ground
    conforms to where the surface used to be, then the renderer moves it somewhere else. If
    the displacement is larger than Contact Distance the blend is completely swamped, and the
    viewport -- which mostly ignores it -- keeps looking correct.
    """
    tgt = mod_target(mod)
    out = []
    for mat in (tgt.data.materials if tgt and tgt.data else []) or []:
        tree = node_tree(mat)
        if tree is None:
            continue
        if getattr(mat, "displacement_method", "BUMP") == "BUMP":
            continue
        if any(n.type == "OUTPUT_MATERIAL" and n.inputs["Displacement"].is_linked
               for n in tree.nodes):
            out.append(mat)
    return out


def object_width(obj):
    """Smaller horizontal (world X/Y) extent of `obj`'s own mesh, in metres, or None.

    Measured from the mesh data, not the evaluated object: once the blend exists the evaluated
    mesh has the whole ground joined into it. The smaller side, so a long log or a thin wall
    gets a contact band sized to its thickness rather than to its length.
    """
    import numpy as np
    if obj.type != "MESH" or not obj.data.vertices:
        return None
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world, dtype=np.float64)
    world = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    extent = world.max(axis=0) - world.min(axis=0)
    return float(min(extent[0], extent[1]))


def fit_to_object(mod, obj):
    """Size the distance settings to `obj`, so a pebble and a boulder both start sensibly.

    A fixed 0.3 m Contact Distance swallows a 5 cm pebble whole and is invisible under a 20 m
    boulder. Contact Distance ~1/3 of the width matches the advice that always went with it;
    the rest follow in proportion (Surface Offset lands on the old 0.002 m for a ~0.5 m
    object). Only ever called on a new blend or on explicit request: on Update or a re-apply
    these are the user's values.
    """
    width = object_width(obj)
    if not width:
        return False
    ids = input_ids(mod.node_group)
    contact = min(max(width / 3, 0.001), 50.0)
    mod[ids["Contact Distance"]] = contact
    mod[ids["Material Blend Distance"]] = contact
    mod[ids["Normal Blend Distance"]] = contact * 2 / 3
    mod[ids["Surface Offset"]] = min(max(contact * 0.012, 1e-5), 0.05)
    obj.update_tag()
    return True


# --- shared ground -------------------------------------------------------------------------
# Joined mode gives every object its own full copy of the target. Two rocks on one terrain are
# two ground copies, each flat under the *other* rock, and each cuts straight back through the
# other's crater -- and N copies of a big terrain cost N times the memory and evaluation. A
# modifier on the target reading the objects that read the target would be a dependency cycle,
# so a third object breaks it: the ground proxy, an empty mesh parented to the target whose
# modifier conforms the target to every object in a helper collection at once. The objects keep
# reading the raw target; only the proxy reads them.

GROUND_SETTINGS = ("Contact Subdivisions", "Ground Detail", "Detail Image", "Detail UV Map",
                   "Detail Midlevel")


def ground_mod(obj):
    return obj and next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                         and _is_ground_group(m.node_group)), None)


def is_proxy(obj):
    return obj is not None and bool(obj.get(PROXY_KEY)) and ground_mod(obj) is not None


def proxy_target(obj):
    return socket(ground_mod(obj), "Target") if is_proxy(obj) else None


def real_target(obj):
    """The ground an object stands for: a proxy stands for its target, anything else itself."""
    return proxy_target(obj) or obj if is_proxy(obj) else obj


def find_proxy(tgt):
    return tgt and next((o for o in bpy.data.objects if is_proxy(o) and proxy_target(o) == tgt), None)


def heroes_collection(tgt):
    return tgt and next((c for c in bpy.data.collections if c.get(HEROES_KEY) == tgt), None)


def blends(tgt=None):
    """(object, modifier) for every blended object, optionally only those on `tgt`."""
    out = []
    for obj in bpy.data.objects:
        mod = find_mod(obj)
        if mod and (tgt is None or mod_target(mod) == tgt):
            out.append((obj, mod))
    return out


def shared_heroes(tgt):
    """The objects the proxy of `tgt` should conform to: shared mode, Displace Ground on."""
    return [o for o, m in blends(tgt) if is_shared(m) and displaces(m)]


def is_ground(obj):
    return obj is not None and (is_proxy(obj) or any(True for _ in blends(obj)))


def _remove_proxy(proxy):
    mesh = proxy.data
    bpy.data.objects.remove(proxy, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _sync_membership(coll, heroes):
    want = {o.as_pointer() for o in heroes}
    for obj in list(coll.objects):
        if obj.as_pointer() not in want:
            coll.objects.unlink(obj)
    have = {o.as_pointer() for o in coll.objects}
    for obj in heroes:
        if obj.as_pointer() not in have:
            coll.objects.link(obj)


def _propagate(proxy_mod, heroes):
    """Copy the ground-level settings from the proxy into each object's own (hidden) sockets.

    The proxy is the single source of truth for them, but every object conforms a local patch
    of the ground to weld its normals and project its UVs, and that patch has to be cut and
    displaced the way the proxy cuts and displaces the real one.
    """
    changed = False
    for obj in heroes:
        mod = find_mod(obj)
        # raw values, not socket(): an image cleared on the ground must be cleared here too
        stale = [name for name in GROUND_SETTINGS if _raw(mod, name) != _raw(proxy_mod, name)
                 and input_ids(mod.node_group).get(name) and input_ids(proxy_mod.node_group).get(name)]
        for name in stale:
            set_socket(mod, name, _raw(proxy_mod, name))
        if stale:
            obj.update_tag()
            changed = True
    return changed


def _raw(mod, name):
    ident = input_ids(mod.node_group).get(name)
    return mod.get(ident) if ident else None


def _ensure_proxy(tgt, coll, seed):
    proxy = find_proxy(tgt)
    if proxy is None:
        name = f"{tgt.name} · Contact Blend"
        proxy = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        proxy[PROXY_KEY] = True
        for c in tgt.users_collection:
            if c.library is None:
                c.objects.link(proxy)
        if not proxy.users_collection:
            bpy.context.scene.collection.objects.link(proxy)
    # Repaired on every sync, not just at creation: the identity parent transform is what makes
    # RELATIVE Object Info hand over the target in its own space and Object texture
    # coordinates match the target's, and it keeps the ground on an animated target.
    from mathutils import Matrix
    if proxy.parent != tgt:
        proxy.parent = tgt
    identity = Matrix.Identity(4)
    if proxy.matrix_parent_inverse != identity:
        proxy.matrix_parent_inverse = identity
    if proxy.matrix_basis != identity:
        proxy.matrix_basis = identity
    mod = ground_mod(proxy)
    if mod is None:
        mod = proxy.modifiers.new(GROUND_GROUP, "NODES")
        mod.node_group = ground_group()
        for name in GROUND_SETTINGS:
            value = socket(seed, name)
            if value is not None:
                set_socket(mod, name, value)
    if socket(mod, "Target") != tgt:
        set_socket(mod, "Target", tgt)
    if socket(mod, "Objects") != coll:
        set_socket(mod, "Objects", coll)
    return proxy, mod


def sync_ground(tgt, refresh=False, seed=None):
    """Make the shared ground of `tgt` match the blends that point at it.

    Membership becomes exactly the objects in shared mode with Displace Ground on; the proxy is
    created or repaired; with no such objects left, proxy and helper collection are deleted.
    Target visibility is not touched here: each blend hides and restores it as before.
    Returns the proxy or None.
    """
    heroes = shared_heroes(tgt)
    proxy, coll = find_proxy(tgt), heroes_collection(tgt)
    if not heroes:
        if proxy is not None:
            _remove_proxy(proxy)
        if coll is not None:
            bpy.data.collections.remove(coll)
        return None
    if coll is None:
        # Deliberately linked into no scene: it is a list for Collection Info, not something to
        # show. Objects in it are still in the scene through their own collections.
        coll = bpy.data.collections.new(f"{tgt.name} · Contact Blend Objects")
        coll[HEROES_KEY] = tgt
    _sync_membership(coll, heroes)
    proxy, mod = _ensure_proxy(tgt, coll, seed or find_mod(heroes[0]))
    _propagate(mod, heroes)
    # One corrected ground material per target, however many objects stand on it.
    tmat = tgt.active_material
    if refresh or socket(mod, "Target Material") != tmat or socket(mod, "Ground Material") is None:
        ground = _corrected_ground(tmat, tgt) if _surface(tmat)[1] else tmat
        if tmat is not None:
            set_socket(mod, "Target Material", tmat)
            set_socket(mod, "Ground Material", ground)
        _cleanup_texture_data()
    proxy.update_tag()
    return proxy


def sync_all(refresh=(), seeds=None):
    """sync_ground() every target that has, or had, a shared ground."""
    seeds = seeds or {}
    targets = {m_tgt.as_pointer(): m_tgt for m_tgt in
               [mod_target(m) for _, m in blends() if is_shared(m)]
               + [c.get(HEROES_KEY) for c in bpy.data.collections]
               + [proxy_target(o) for o in bpy.data.objects if is_proxy(o)]
               if m_tgt is not None}
    for ptr, tgt in targets.items():
        sync_ground(tgt, refresh=ptr in {t.as_pointer() for t in refresh}, seed=seeds.get(ptr))
    # A proxy whose target was deleted has nothing to stand for.
    for obj in list(bpy.data.objects):
        if is_proxy(obj) and proxy_target(obj) is None:
            _remove_proxy(obj)


_reconciling = False


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph=None):
    """Keep membership and ground settings current between operator runs.

    Duplicating an object (Shift+D) only links the copy into collections that are in the
    scene, so the helper collection -- in no scene by design -- does not get it, although the
    copy's modifier targets the same ground. Editing a ground setting on the proxy has to reach
    the objects' local patches too. Both are cheap, idempotent comparisons; only differences
    are written, so the update this triggers comes back here with nothing to do. Creating or
    deleting the proxy is left to operators.
    """
    global _reconciling
    if _reconciling:
        return
    colls = [c for c in bpy.data.collections if c.get(HEROES_KEY) is not None]
    if not colls:
        return
    _reconciling = True
    try:
        for coll in colls:
            tgt = coll[HEROES_KEY]
            heroes = shared_heroes(tgt)
            if heroes:
                _sync_membership(coll, heroes)
            proxy = find_proxy(tgt)
            if proxy is not None:
                _propagate(ground_mod(proxy), heroes)
    except (AttributeError, RuntimeError, ReferenceError):
        pass     # restricted context (rendering, file I/O): the next update catches up
    finally:
        _reconciling = False


def share(mod):
    """Switch a joined blend to the shared ground. Its own settings are kept as they are."""
    set_socket(mod, "Shared Ground", True)


# --- what was last applied, so the panel can tell when Update is needed --------------------

def _record_applied(mod):
    rec = {"displace": displaces(mod), "match": bool(socket(mod, "Match Target Textures", True)),
           "uv": str(socket(mod, "Target UV Map", "")), "shared": is_shared(mod)}
    if mod_target(mod) is not None:
        rec["target"] = mod_target(mod)
    mod[APPLIED_KEY] = rec


def out_of_sync(obj, mod):
    """Reasons the blend on `obj` no longer matches what was applied; empty when current."""
    reasons = []
    rec = mod.get(APPLIED_KEY)
    if rec is not None:
        if rec.get("target") != mod_target(mod):
            reasons.append("Target changed")
        if rec.get("displace") != displaces(mod):
            reasons.append("Displace Ground changed")
        if rec.get("match") != bool(socket(mod, "Match Target Textures", True)):
            reasons.append("Match Target Textures changed")
        elif rec.get("match") and rec.get("uv") != str(socket(mod, "Target UV Map", "")):
            reasons.append("Target UV Map changed")
    tgt = mod_target(mod)
    if tgt is not None and is_shared(mod) and displaces(mod):
        if find_proxy(tgt) is None:
            reasons.append("Shared ground is missing")
        else:
            coll = heroes_collection(tgt)
            if coll is None or obj.name not in coll.objects:
                reasons.append("Not part of the shared ground")
    return reasons


def _get_displace(self):
    return displaces(find_mod(self))


def _set_displace(self, value):
    """Displace Ground, applied at once: the ground appears or disappears with the tick.

    Through the raw socket, unticking it left the target hidden and dropped the ground copy, so
    the ground simply vanished until Update was pressed.
    """
    mod = find_mod(self)
    if mod is None or not set_socket(mod, "Displace Ground", bool(value)):
        return
    _sync_target_visibility(mod, bpy.context)
    sync_all()
    if APPLIED_KEY in mod:
        mod[APPLIED_KEY]["displace"] = bool(value)
    self.update_tag()


def _ground_poll(_self, obj):
    return obj.type == "MESH"


# --- which ground, which objects ------------------------------------------------------------

class Resolution:
    def __init__(self, target=None, heroes=(), reason=""):
        self.target, self.heroes, self.reason = target, list(heroes), reason

    @property
    def reapply(self):
        return bool(self.heroes) and all(mod_target(find_mod(h)) == self.target for h in self.heroes)

    @property
    def label(self):
        if self.target is None:
            return "Contact Blend"
        what = f"'{self.heroes[0].name}'" if len(self.heroes) == 1 else f"{len(self.heroes)} objects"
        if self.reapply:
            return f"Re-apply {what} to '{self.target.name}'"
        return f"Blend {what} into '{self.target.name}'"


def resolve(ctx):
    """Work out the ground and the objects to blend from the selection. Used by both the
    operator and the panel label, so the button always says what it will do.

    1. the Scene's Ground picker, if set, is the ground; every other selected mesh is blended;
    2. else exactly one selected mesh besides an active mesh: it is the ground, the active is
       blended (the original convention);
    3. else exactly one selected object is already a ground (a target or its proxy): it is the
       ground, the rest are blended;
    4. else only the active is selected and it has a blend: re-apply to its ground.
    A proxy always stands for its real target; proxies and the target are never blended.
    """
    sel = list(getattr(ctx, "selected_objects", None) or [])
    active = getattr(ctx, "active_object", None)
    picked = getattr(ctx.scene, "contact_blend_ground", None) if ctx.scene else None

    def heroes_of(tgt, skip=()):
        out = [o for o in sel if o.type == "MESH" and not is_proxy(o) and o is not tgt
               and o not in skip and o.library is None]
        if active in out:
            out.remove(active)
            out.insert(0, active)
        return out

    if picked is not None:
        tgt = real_target(picked)
        heroes = heroes_of(tgt, (picked,))
        if not heroes:
            return Resolution(tgt, (), f"Select the objects to blend into '{tgt.name}'")
        return Resolution(tgt, heroes)
    others = [o for o in sel if o is not active and o.type == "MESH"]
    if active is not None and active.type == "MESH" and not is_proxy(active) and len(others) == 1:
        tgt = real_target(others[0])
        if tgt is active:
            return Resolution(None, (), "The blend object cannot be its own target")
        return Resolution(tgt, [active])
    # one pass over the scene, not one per selected object: this runs on every panel redraw
    targets = {m_tgt.as_pointer() for m_tgt in (mod_target(m) for _, m in blends()) if m_tgt}
    grounds = [o for o in sel if is_proxy(o) or o.as_pointer() in targets]
    if len(grounds) == 1 and len(sel) > 1:
        tgt = real_target(grounds[0])
        heroes = heroes_of(tgt, (grounds[0],))
        if heroes:
            return Resolution(tgt, heroes)
    mod = find_mod(active)
    if mod and mod_target(mod) is not None and all(o is active for o in sel):
        return Resolution(mod_target(mod), [active])
    if not sel:
        return Resolution(None, (), "Select a ground, then shift-select the objects to blend")
    return Resolution(None, (), "Select one ground and the objects to blend, or pick a Ground")


def _selected_blends(ctx):
    """Blended objects in the selection, plus every object on a selected ground or proxy."""
    seen, out = set(), []
    objs = list(getattr(ctx, "selected_objects", None) or [])
    active = getattr(ctx, "active_object", None)
    if active is not None and active not in objs:
        objs.append(active)
    for obj in objs:
        found = [obj] if find_mod(obj) else []
        if not found and is_ground(obj):
            found = [o for o, _ in blends(real_target(obj))]
        for o in found:
            if o.as_pointer() not in seen:
                seen.add(o.as_pointer())
                out.append(o)
    return out


# --- diagnostics ----------------------------------------------------------------------------

REPORT_TEXT = "Contact Blend Report"


def diagnose(obj):
    """Every viewport-vs-render and silent-failure check, as report lines for `obj`.

    `obj` may be a blended object, a ground proxy or a target; the latter two report on each
    object standing on that ground.
    """
    import collections as _collections
    from mathutils import Vector
    out = []
    add = out.append
    if obj is None:
        return ["FAIL  no active object"]
    if find_mod(obj) is None and is_ground(obj):
        tgt = real_target(obj)
        add(f"ground   : {tgt.name if tgt else 'NONE'}")
        for hero, _ in blends(tgt):
            add("")
            add("=" * 70)
            out += diagnose(hero)
        if not blends(tgt):
            add("  ^-- nothing is blended into this ground")
        return out
    mod = find_mod(obj)
    if not mod:
        return [f"FAIL  '{obj.name}' has no {GROUP} modifier"]
    ng = mod.node_group
    tgt = mod_target(mod)
    add(f"object   : {obj.name}")
    add(f"target   : {tgt.name if tgt else 'NONE  <-- modifier has no target'}")
    add(f"engine   : {bpy.context.scene.render.engine}")
    add(f"group    : {ng.name}" + ("  <-- pre-1.1 name; reload the file to migrate it"
                                   if ng.name in LEGACY_NAMES else ""))
    add(f"group ver: {group_version(ng)} (current {GROUP_VERSION})  nodes={len(ng.nodes)}")
    if group_version(ng) != GROUP_VERSION:
        add("  ^-- stale: press Update (or reload the file) to rebuild it in place, keeping settings")
    mode = "shared ground" if is_shared(mod) else "joined ground copy"
    add(f"mode     : {mode}" if displaces(mod) else "mode     : Displace Ground off (shading only)")
    for reason in out_of_sync(obj, mod):
        add(f"  ^-- out of date: {reason}; press Update")

    add("")
    add("modifier stack (viewport / render):")
    for m in obj.modifiers:
        add(f"  {'OK ' if m.show_render else 'OFF'} {m.name:<22} type={m.type:<12} "
            f"viewport={m.show_viewport} render={m.show_render}")
        if not m.show_render:
            add(f"       ^-- '{m.name}' is DISABLED IN RENDER (camera icon in its header)")
        if m.type == "SUBSURF" and m.levels != m.render_levels:
            add(f"       ^-- subdivision differs: viewport={m.levels} render={m.render_levels};"
                " the blend is computed on a different mesh at F12")

    add("")
    if tgt:
        add(f"target visibility: eye_hidden={is_hidden(tgt)} hide_render={tgt.hide_render} "
            f"hide_viewport={tgt.hide_viewport}")
        if displaces(mod) and not tgt.hide_render:
            add("  ^-- target STILL RENDERS while a conformed ground is drawn."
                " The flat original cuts through the crater at F12 only.")
        if displaces(mod) and not is_hidden(tgt):
            add("  ^-- target still draws in the viewport too.")
        for coll in tgt.users_collection:
            lc = bpy.context.view_layer.layer_collection.children.get(coll.name)
            if lc and (lc.exclude or lc.holdout or lc.indirect_only):
                add(f"  ^-- collection '{coll.name}': exclude={lc.exclude} holdout={lc.holdout}"
                    f" indirect_only={lc.indirect_only}")
    if tgt and is_shared(mod) and displaces(mod):
        proxy, coll = find_proxy(tgt), heroes_collection(tgt)
        if proxy is None:
            add("shared ground    : MISSING  <-- press Update to recreate it")
        else:
            gm = ground_mod(proxy)
            add(f"shared ground    : '{proxy.name}'  eye_hidden={is_hidden(proxy)} "
                f"hide_render={proxy.hide_render}  group ver={gm.node_group.get(GROUND_VERSION_KEY)}"
                f" (current {GROUND_VERSION})")
            if is_hidden(proxy) or proxy.hide_render:
                add("  ^-- the shared ground is hidden: no ground will draw")
            if proxy.parent != tgt:
                add("  ^-- not parented to the target; press Update to repair")
        members = {o.name for o in coll.objects} if coll else set()
        expected = {o.name for o in shared_heroes(tgt)}
        add(f"ground objects   : {len(members)} listed, {len(expected)} expected")
        for name in sorted(expected - members):
            add(f"  ^-- '{name}' is blended but missing from the shared ground")
        for name in sorted(members - expected):
            add(f"  ^-- '{name}' is listed but no longer blended into this ground")

    add("")
    for mat in (tgt.data.materials if tgt and tgt.data else []) or []:
        if node_tree(mat) is None:
            continue
        linked = any(n.type == "OUTPUT_MATERIAL" and n.inputs["Displacement"].is_linked
                     for n in node_tree(mat).nodes)
        method = getattr(mat, "displacement_method", "BUMP")
        add(f"target material '{mat.name}': displacement_linked={linked} method={method}")
        if linked and method != "BUMP":
            add("  ^-- THIS MOVES THE GROUND AT RENDER TIME, BEHIND THE MODIFIER'S BACK.")
            add("      Rebuild the height as Subdivision + Displace modifiers on the target,")
            add("      and set this material to Bump Only.")

    add("")
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        if ATTR in me.attributes:
            vals = [d.value for d in me.attributes[ATTR].data]
            add(f"attribute '{ATTR}': present  range={min(vals):.3f}..{max(vals):.3f}")
            if max(vals) < 0.5:
                add("  ^-- never gets near 1: Material Blend Distance is too small for this object,"
                    " or it does not touch the target.")
        else:
            add(f"attribute '{ATTR}': MISSING from the evaluated mesh")
        if is_shared(mod):
            add(f"attribute '{PARAMS_ATTR}': "
                + ("present" if PARAMS_ATTR in me.attributes else "MISSING  <-- the shared ground"
                   " cannot read this object's settings"))
        add(f"evaluated mesh   : {len(me.vertices)} verts (own mesh {len(obj.data.vertices)})")
        if socket(mod, "Match Target Textures", False):
            add("projected UVs    : " + ("present" if TARGET_UV_ATTR in me.uv_layers else "MISSING"))
        if not is_shared(mod) and displaces(mod) and tgt:
            add("crack check      : " + _crack_check(obj, me, mod, tgt, _collections, Vector))
    finally:
        ev.to_mesh_clear()
    if is_shared(mod) and displaces(mod) and find_proxy(tgt):
        proxy = find_proxy(tgt)
        pev = proxy.evaluated_get(bpy.context.evaluated_depsgraph_get())
        pme = pev.to_mesh()
        try:
            add("crack check      : " + _crack_check(proxy, pme, ground_mod(proxy), tgt, _collections, Vector))
        finally:
            pev.to_mesh_clear()

    add("")
    for slot in obj.material_slots:
        mat = slot.material
        if node_tree(mat) is None:
            add(f"material slot    : {mat.name if mat else '(empty)'}  not a node material; not blended")
            continue
        mix = _material_mix(mat)
        add(f"material         : {mat.name}  mix={'present' if mix else 'MISSING'}"
            f"  breakup={'present' if breakup_node(mat) else 'none'}")
        for warning in mat.get("cb_texture_warnings", "").splitlines():
            add(f"  ^-- {warning}")
        readers = [n for n in node_tree(mat).nodes if n.type == "ATTRIBUTE" and n.attribute_name == ATTR]
        for n in readers:
            if n.attribute_type != "GEOMETRY":
                add(f"  ^-- {n.name}.attribute_type is {n.attribute_type}, must be GEOMETRY")
    s = bpy.context.scene.render
    if s.use_simplify:
        add("")
        add(f"SIMPLIFY IS ON: max_subdiv viewport={s.simplify_subdivision} "
            f"render={s.simplify_subdivision_render}")
    return out


def _crack_check(obj, me, mod, tgt, _collections, Vector):
    """Open edges on the ground copy away from the target's outline: the subdivision cut. Their
    vertices must not have moved, or the cut tears into slivers of background."""
    gmats = {m.name for m in (tgt.data.materials if tgt.data else []) if m}
    if socket(mod, "Ground Material"):
        gmats.add(socket(mod, "Ground Material").name)
    gi = {i for i, m in enumerate(me.materials) if m and m.name in gmats}
    polys = [p for p in me.polygons if p.material_index in gi]
    if not polys:
        return "could not identify the ground by material; skipped"
    seen = _collections.Counter()
    for p in polys:
        vs = list(p.vertices)
        for a, b in zip(vs, vs[1:] + vs[:1]):
            seen[(min(a, b), max(a, b))] += 1
    bb = [tgt.matrix_world @ Vector(c) for c in tgt.bound_box]
    lo = min(v.x for v in bb), min(v.y for v in bb)
    hi = max(v.x for v in bb), max(v.y for v in bb)

    def on_rim(i):
        c = obj.matrix_world @ me.vertices[i].co
        return (abs(c.x - lo[0]) < 1e-3 or abs(c.x - hi[0]) < 1e-3
                or abs(c.y - lo[1]) < 1e-3 or abs(c.y - hi[1]) < 1e-3)

    inner = {i for e, c in seen.items() if c == 1 for i in e if not on_rim(i)}
    line = f"{len(polys)} ground faces, {len(inner)} T-junction verts"
    if inner:
        zs = [(obj.matrix_world @ me.vertices[i].co).z for i in inner]
        line += f", z spread {max(zs) - min(zs):.6f} (should be ~0 on a flat target)"
    return line


def write_report(obj):
    """Write diagnose(obj) to the REPORT_TEXT text datablock and return it."""
    body = "\n".join(["Contact Blend diagnostic", "=" * 70] + diagnose(obj))
    txt = bpy.data.texts.get(REPORT_TEXT) or bpy.data.texts.new(REPORT_TEXT)
    txt.clear()
    txt.write(body)
    return txt


# --- operators ------------------------------------------------------------------------------

def _finish_blend(ctx, obj, mod, refresh_material=True):
    """Visibility, material and bookkeeping for one blended object after its sockets are set."""
    tgt = mod_target(mod)
    # The ground is drawn by a copy (joined) or by the proxy (shared), because a modifier on
    # the target that reads the object would be a depsgraph cycle. So the original has to stop
    # drawing: left visible it stays flat, cuts straight through the crater, and that
    # intersection is exactly the hard ring the blend is supposed to remove. Object Info still
    # reads hidden objects.
    _sync_target_visibility(mod, ctx)
    err = setup_material(obj, tgt, refresh=True) if refresh_material else None
    _record_applied(mod)
    obj.update_tag()
    return err


class CONTACTBLEND_OT_add(bpy.types.Operator):
    """Blend the selected objects into the ground without booleans"""
    bl_idname = "object.contact_blend"
    bl_label = "Contact Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return resolve(ctx).target is not None

    @classmethod
    def description(cls, ctx, _props):
        r = resolve(ctx)
        return r.label if r.target is not None else r.reason

    def execute(self, ctx):
        r = resolve(ctx)
        if r.target is None:
            self.report({"ERROR"}, r.reason)
            return {"CANCELLED"}
        tgt = r.target
        build_group()
        ground_group()
        errors, created = [], []
        for hero in r.heroes:
            mod = find_mod(hero)
            if mod is None:
                mod = hero.modifiers.new(GROUP, "NODES")
                mod.node_group = build_group()
                fit_to_object(mod, hero)
                share(mod)
                created.append(hero)
            set_socket(mod, "Target", tgt)
        # A joined copy of the ground would cut straight back through the proxy's craters, so
        # once anything on this ground is shared, everything on it is. Converting keeps every
        # setting and is one undo step; joined-only grounds are left alone.
        seed, converted = None, []
        if any(is_shared(m) for _, m in blends(tgt)):
            for obj, mod in blends(tgt):
                if not is_shared(mod):
                    share(mod)
                    seed = seed or mod
                    converted.append(obj)
        for hero in r.heroes:
            err = _finish_blend(ctx, hero, find_mod(hero))
            if err:
                errors.append(err)
        for obj in converted:
            if obj not in r.heroes:
                _finish_blend(ctx, obj, find_mod(obj), refresh_material=False)
        sync_all(refresh=[tgt], seeds={tgt.as_pointer(): seed} if seed else None)
        # Leave the blended objects selected with one of them active, so the panel shows their
        # settings rather than the (hidden) ground's.
        for obj in list(ctx.selected_objects):
            if obj not in r.heroes:
                obj.select_set(False)
        for hero in r.heroes:
            if is_hidden(hero) is False:
                hero.select_set(True)
        if ctx.view_layer.objects.active not in r.heroes and _in_layer(r.heroes[0], ctx.view_layer):
            ctx.view_layer.objects.active = r.heroes[0]
        what = f"'{r.heroes[0].name}'" if len(r.heroes) == 1 else f"{len(r.heroes)} objects"
        msg = f"Blending {what} into '{tgt.name}'"
        if converted:
            msg += f"; moved {len(converted)} joined blend(s) to the shared ground"
        if errors:
            msg += f" (materials not mixed: {errors[0]})"
        self.report({"WARNING"} if errors else {"INFO"}, msg)
        return {"FINISHED"}


class CONTACTBLEND_OT_update(bpy.types.Operator):
    """Apply changed settings: refresh the target shader, visibility and the shared ground"""
    bl_idname = "object.contact_blend_update"
    bl_label = "Update Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return any(mod_target(find_mod(o)) is not None for o in _selected_blends(ctx))

    def execute(self, ctx):
        build_group()
        ground_group()
        targets, errors, done = [], [], 0
        for obj in _selected_blends(ctx):
            mod = find_mod(obj)
            tgt = mod_target(mod)
            if tgt is None:
                continue
            if tgt is obj:
                self.report({"ERROR"}, "The blend object cannot be its own target")
                return {"CANCELLED"}
            err = _finish_blend(ctx, obj, mod)
            if err:
                errors.append(err)
            targets.append(tgt)
            done += 1
        sync_all(refresh=targets)
        self.report({"WARNING"} if errors else {"INFO"},
                    f"Updated {done} object(s)" + (f" (materials unchanged: {errors[0]})" if errors else ""))
        return {"FINISHED"}


class CONTACTBLEND_OT_share(bpy.types.Operator):
    """Move this ground's blends from per-object ground copies to one shared ground, so several
    objects can crater the same ground. Undo or Remove Blend reverts it"""
    bl_idname = "object.contact_blend_share"
    bl_label = "Use Shared Ground"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return any(not is_shared(find_mod(o)) for o in _selected_blends(ctx))

    def execute(self, ctx):
        build_group()
        ground_group()
        targets = {mod_target(find_mod(o)) for o in _selected_blends(ctx)} - {None}
        seeds, moved = {}, 0
        for tgt in targets:
            for obj, mod in blends(tgt):
                if not is_shared(mod):
                    seeds.setdefault(tgt.as_pointer(), mod)
                    share(mod)
                    _finish_blend(ctx, obj, mod, refresh_material=False)
                    moved += 1
        sync_all(refresh=list(targets), seeds=seeds)
        self.report({"INFO"}, f"{moved} object(s) now share one ground")
        return {"FINISHED"}


class CONTACTBLEND_OT_fit(bpy.types.Operator):
    """Size Contact Distance, blend distances and Surface Offset to each selected object's width"""
    bl_idname = "object.contact_blend_fit"
    bl_label = "Fit To Object Size"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return bool(_selected_blends(ctx))

    def execute(self, ctx):
        fitted = [o for o in _selected_blends(ctx) if fit_to_object(find_mod(o), o)]
        if not fitted:
            self.report({"ERROR"}, "Nothing with vertices to measure")
            return {"CANCELLED"}
        if len(fitted) == 1:
            cd = socket(find_mod(fitted[0]), "Contact Distance")
            self.report({"INFO"}, f"Contact Distance {cd:.4g} m for '{fitted[0].name}'")
        else:
            self.report({"INFO"}, f"Fitted {len(fitted)} objects")
        return {"FINISHED"}


class CONTACTBLEND_OT_remove(bpy.types.Operator):
    """Remove the blend, preserving material edits and restoring recorded target visibility"""
    bl_idname = "object.contact_blend_remove"
    bl_label = "Remove Blend"
    bl_options = {"REGISTER", "UNDO"}

    object_name: bpy.props.StringProperty(
        name="Object", description="Remove only this object's blend (default: the selection)",
        options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, ctx):
        return bool(_selected_blends(ctx))

    def targets(self, ctx):
        if self.object_name:
            obj = bpy.data.objects.get(self.object_name)
            return [obj] if obj is not None and find_mod(obj) else []
        objs = list(getattr(ctx, "selected_objects", None) or [])
        active = getattr(ctx, "active_object", None)
        if active is not None and active not in objs:
            objs.append(active)
        return [o for o in objs if find_mod(o)]

    def execute(self, ctx):
        objs = self.targets(ctx)
        if not objs:
            self.report({"ERROR"}, "Nothing selected has a blend")
            return {"CANCELLED"}
        for src in objs:
            mod = find_mod(src)
            targets = [mod_target(mod), mod.get(HIDDEN_TARGET_KEY)]
            src.modifiers.remove(mod)
            # Older files may contain duplicate modifiers. Keep the material blend until
            # the last modifier is removed, rather than breaking a remaining blend.
            if not find_mod(src):
                remove_material_blend(src)
            for tgt in targets:
                _restore_target(tgt)
            src.update_tag()
        sync_all()
        _cleanup_texture_data()
        what = f"'{objs[0].name}'" if len(objs) == 1 else f"{len(objs)} objects"
        self.report({"INFO"}, f"Removed blend from {what}; kept source material edits")
        return {"FINISHED"}


class CONTACTBLEND_OT_select(bpy.types.Operator):
    """Select an object, the ground, or every object blended into the ground"""
    bl_idname = "object.contact_blend_select"
    bl_label = "Select"
    bl_options = {"REGISTER", "UNDO"}

    object_name: bpy.props.StringProperty(options={"SKIP_SAVE", "HIDDEN"})
    mode: bpy.props.EnumProperty(items=(
        ("OBJECT", "Object", "Select this object"),
        ("GROUND", "Ground", "Select the ground (its shared ground, if it has one)"),
        ("OBJECTS", "Objects", "Select every object blended into this ground")),
        options={"SKIP_SAVE"})

    def execute(self, ctx):
        obj = bpy.data.objects.get(self.object_name) if self.object_name else ctx.active_object
        if obj is None:
            return {"CANCELLED"}
        if self.mode == "OBJECT":
            pick = [obj]
        elif self.mode == "GROUND":
            tgt = mod_target(find_mod(obj)) or real_target(obj)
            pick = [find_proxy(tgt) or tgt]
        else:
            pick = [o for o, _ in blends(real_target(obj))]
        pick = [o for o in pick if o is not None and _in_layer(o, ctx.view_layer) and not is_hidden(o)]
        if not pick:
            self.report({"WARNING"}, "Nothing visible to select")
            return {"CANCELLED"}
        for o in list(ctx.selected_objects):
            o.select_set(False)
        for o in pick:
            o.select_set(True)
        ctx.view_layer.objects.active = pick[0]
        return {"FINISHED"}


class CONTACTBLEND_OT_material(bpy.types.Operator):
    """Refresh the target's shader in isolated copies of the source's node materials"""
    bl_idname = "object.contact_blend_material"
    bl_label = "Blend Materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return ctx.active_object is not None and ctx.active_object.type == "MESH"

    def execute(self, ctx):
        src = ctx.active_object
        tgt = mod_target(find_mod(src))
        if not tgt:
            self.report({"ERROR"}, "Run Contact Blend first")
            return {"CANCELLED"}
        err = setup_material(src, tgt, refresh=True)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Mixed '{tgt.name}' shader into '{src.name}'")
        return {"FINISHED"}


class CONTACTBLEND_OT_hide_target(bpy.types.Operator):
    """Hide the target from both the viewport and the render, so it stops re-cutting the seam"""
    bl_idname = "object.contact_blend_hide_target"
    bl_label = "Hide Target From Render"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return target_leaks(find_mod(ctx.active_object)) is not None

    def execute(self, ctx):
        mod = find_mod(ctx.active_object)
        tgt = target_leaks(mod)
        _sync_target_visibility(mod, ctx)
        self.report({"INFO"}, f"'{tgt.name}' hidden from viewport and render")
        return {"FINISHED"}


def proxy_hidden(mod):
    """The shared ground of this blend, if it exists but will not draw or render."""
    tgt = mod_target(mod)
    if tgt is None or not is_shared(mod) or not displaces(mod):
        return None
    proxy = find_proxy(tgt)
    return proxy if proxy is not None and (proxy.hide_render or is_hidden(proxy)) else None


class CONTACTBLEND_OT_show_ground(bpy.types.Operator):
    """Show the shared ground in the viewport and the render again"""
    bl_idname = "object.contact_blend_show_ground"
    bl_label = "Show Shared Ground"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return proxy_hidden(find_mod(ctx.active_object)) is not None

    def execute(self, ctx):
        proxy = proxy_hidden(find_mod(ctx.active_object))
        proxy.hide_render = False
        proxy.hide_viewport = False
        _set_hidden(proxy, False, ctx.view_layer)
        self.report({"INFO"}, f"'{proxy.name}' shown")
        return {"FINISHED"}


class CONTACTBLEND_OT_bump_only(bpy.types.Operator):
    """Switch the target's materials to Bump Only so the renderer stops moving the surface.

    The bumpy look becomes shading-only. To keep real height, undo this and rebuild the
    displacement as geometry instead -- a Subdivision plus Displace modifier on the target,
    above Contact Blend -- so the modifier stack can actually see it
    """
    bl_idname = "object.contact_blend_bump_only"
    bl_label = "Set Target To Bump Only"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return bool(target_displaces(find_mod(ctx.active_object)))

    def execute(self, ctx):
        mats = target_displaces(find_mod(ctx.active_object))
        for m in mats:
            m.displacement_method = "BUMP"
        self.report({"INFO"}, "Bump only: " + ", ".join(m.name for m in mats))
        return {"FINISHED"}


class CONTACTBLEND_OT_diagnose(bpy.types.Operator):
    """Check the active blend for render-only and silent failures; writes the
    'Contact Blend Report' text"""
    bl_idname = "object.contact_blend_diagnose"
    bl_label = "Diagnose"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, ctx):
        obj = ctx.active_object
        return find_mod(obj) is not None or is_ground(obj)

    def execute(self, ctx):
        txt = write_report(ctx.active_object)
        problems = sum(1 for line in txt.as_string().splitlines() if "^--" in line or "FAIL" in line)
        for area in (ctx.screen.areas if getattr(ctx, "screen", None) else []):
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = txt
                break
        self.report({"WARNING"} if problems else {"INFO"},
                    f"{problems} finding(s); see the '{REPORT_TEXT}' text" if problems
                    else f"No problems found; details in the '{REPORT_TEXT}' text")
        return {"FINISHED"}


# --- panel ----------------------------------------------------------------------------------

def _row_prop(layout, mod, name, text=None, search=None):
    """One modifier-input row, with a datablock/UV picker where one applies. Never raises."""
    key = socket_key(mod, name)
    if key is None:
        return None
    text = name if text is None else text
    if search is not None and search[0] is not None:
        layout.prop_search(mod, key, search[0], search[1], text=text)
    else:
        layout.prop(mod, key, text=text)
    return key


def _uv_search(tgt):
    return (tgt.data, "uv_layers") if tgt is not None and tgt.type == "MESH" else (None, None)


def draw_panel(layout, ctx):
    """The whole sidebar panel. A plain function so tests can drive it with a fake layout."""
    obj = ctx.active_object
    r = resolve(ctx)
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    if ctx.scene is not None:
        col.prop(ctx.scene, "contact_blend_ground", text="Ground")
    big = layout.row()
    big.scale_y = 1.4
    big.enabled = r.target is not None
    big.operator(CONTACTBLEND_OT_add.bl_idname, text=r.label, icon="MOD_SIMPLEDEFORM")
    if r.target is None and r.reason:
        layout.label(text=r.reason, icon="INFO")
    mod = find_mod(obj)
    if mod is not None:
        _draw_hero(ctx, layout, obj, mod)
    elif obj is not None and is_ground(obj):
        _draw_ground(ctx, layout, real_target(obj))


def _draw_hero(ctx, layout, obj, mod):
    tgt = mod_target(mod)
    stale = group_version(mod.node_group) != GROUP_VERSION
    reasons = [] if stale else out_of_sync(obj, mod)
    row = layout.row(align=True)
    row.alert = bool(reasons) or stale
    row.operator(CONTACTBLEND_OT_update.bl_idname, text="Upgrade" if stale else "Update",
                 icon="FILE_REFRESH")
    sub = layout.row(align=True)
    sub.operator(CONTACTBLEND_OT_remove.bl_idname, text="Remove", icon="X")
    sub.operator(CONTACTBLEND_OT_fit.bl_idname, text="Fit Size", icon="FULLSCREEN_ENTER")
    sub.operator(CONTACTBLEND_OT_select.bl_idname, text="Select Ground", icon="RESTRICT_SELECT_OFF").mode = "GROUND"
    if stale:
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text="Made by an older version of the add-on", icon="ERROR")
        box.label(text="Upgrade rebuilds it and keeps your settings.")
        return
    if reasons:
        box = layout.box().column(align=True)
        box.alert = True
        for reason in reasons:
            box.label(text=reason, icon="ERROR")
        box.label(text="Press Update to apply.")
    if displaces(mod) and not is_shared(mod):
        box = layout.box().column(align=True)
        box.label(text="This blend draws its own copy of the ground.", icon="INFO")
        box.label(text="Other objects on it will not crater it.")
        box.operator(CONTACTBLEND_OT_share.bl_idname, icon="LINKED")

    header, body = layout.panel("CONTACTBLEND_contact", default_closed=False)
    header.label(text="Contact")
    if body:
        body.use_property_split = True
        body.use_property_decorate = False
        _row_prop(body, mod, "Target", search=(bpy.data, "objects"))
        body.prop(obj, "contact_blend_displace", text="Displace Ground")
        _row_prop(body, mod, "Contact Distance")
        _row_prop(body, mod, "Ground Conform")
        _row_prop(body, mod, "Surface Offset")

    header, body = layout.panel("CONTACTBLEND_shading", default_closed=False)
    header.label(text="Shading")
    if body:
        body.use_property_split = True
        body.use_property_decorate = False
        _row_prop(body, mod, "Normal Blend Distance", text="Normal Blend")
        _row_prop(body, mod, "Material Blend Distance", text="Material Blend")
        _row_prop(body, mod, "Match Target Textures")
        _row_prop(body, mod, "Target UV Map", search=_uv_search(tgt))
        if tgt is not None and len(tgt.material_slots) > 1 and tgt.active_material:
            body.label(text=f"Uses '{tgt.active_material.name}' (the ground's active material)",
                       icon="MATERIAL")
        node = breakup_node(obj.active_material)
        if node is not None:
            body.separator()
            body.label(text="Edge Breakup")
            for name in ("Breakup", "Sharpness", "Noise Scale"):
                body.prop(node.inputs[name], "default_value", text=name)
        mat = obj.active_material
        for warning in (mat.get("cb_texture_warnings", "") if mat else "").splitlines():
            body.label(text=warning, icon="ERROR")
        body.operator(CONTACTBLEND_OT_material.bl_idname, icon="NODE_MATERIAL")

    header, body = layout.panel("CONTACTBLEND_ground", default_closed=True)
    header.label(text="Ground")
    if body:
        body.use_property_split = True
        body.use_property_decorate = False
        if is_shared(mod):
            proxy = find_proxy(tgt)
            if proxy is None:
                body.label(text="Shared ground missing: press Update", icon="ERROR")
            else:
                body.label(text=f"Shared by every object on '{tgt.name}'", icon="LINKED")
                _draw_ground_settings(body, ground_mod(proxy), tgt)
        else:
            _draw_ground_settings(body, mod, tgt)
    _draw_hazards(layout, mod)
    layout.operator(CONTACTBLEND_OT_diagnose.bl_idname, icon="VIEWZOOM")


def _draw_ground_settings(layout, mod, tgt):
    _row_prop(layout, mod, "Contact Subdivisions", text="Subdivisions")
    _row_prop(layout, mod, "Ground Detail", text="Detail Height")
    _row_prop(layout, mod, "Detail Image", search=(bpy.data, "images"))
    _row_prop(layout, mod, "Detail UV Map", search=_uv_search(tgt))
    _row_prop(layout, mod, "Detail Midlevel")


def _draw_hazards(layout, mod):
    # This one only breaks the render, so it is invisible until it is too late: say so.
    leak = target_leaks(mod)
    if leak:
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text=f"'{leak.name}' still draws", icon="ERROR")
        box.label(text="Seam will return in the render.")
        box.operator(CONTACTBLEND_OT_hide_target.bl_idname, icon="RESTRICT_RENDER_ON")
    hidden = proxy_hidden(mod)
    if hidden:
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text=f"Shared ground '{hidden.name}' is hidden", icon="ERROR")
        box.operator(CONTACTBLEND_OT_show_ground.bl_idname, icon="HIDE_OFF")
    # Displace Ground ticked but Ground Conform at zero is a contradiction: the ground is
    # conformed, costing everything it costs, and then multiplied out to nothing.
    if displaces(mod) and socket(mod, "Ground Conform", 1.0) < 0.01:
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text="Ground Conform is ~0", icon="ERROR")
        box.label(text="Ground cannot move; no blend.")
        _row_prop(box, mod, "Ground Conform")
    # Material displacement is applied after the modifier stack, so nothing here can see
    # or compensate for it. Silent in the viewport, ruinous at render.
    for mat in target_displaces(mod):
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text=f"'{mat.name}' displaces at render", icon="ERROR")
        box.label(text="Geometry Nodes cannot see it.")
        box.label(text="Rebuild it as a Displace modifier, or:")
        box.operator(CONTACTBLEND_OT_bump_only.bl_idname, icon="MOD_SMOOTH")


def _draw_ground(ctx, layout, tgt):
    on = blends(tgt)
    proxy = find_proxy(tgt)
    layout.label(text=f"Ground '{tgt.name}'" if tgt else "Ground", icon="MESH_GRID")
    if proxy is not None:
        header, body = layout.panel("CONTACTBLEND_ground_settings", default_closed=False)
        header.label(text="Ground")
        if body:
            body.use_property_split = True
            body.use_property_decorate = False
            _draw_ground_settings(body, ground_mod(proxy), tgt)
        if proxy.hide_render or is_hidden(proxy):
            box = layout.box().column(align=True)
            box.alert = True
            box.label(text=f"'{proxy.name}' is hidden; no ground draws", icon="ERROR")
    elif any(is_shared(m) and displaces(m) for _, m in on):
        box = layout.box().column(align=True)
        box.alert = True
        box.label(text="Shared ground missing", icon="ERROR")
        box.operator(CONTACTBLEND_OT_update.bl_idname, icon="FILE_REFRESH")
    header, body = layout.panel("CONTACTBLEND_objects", default_closed=False)
    header.label(text=f"Objects ({len(on)})")
    if body:
        if not on:
            body.label(text="Nothing is blended into this ground")
        for obj, mod in on:
            row = body.row(align=True)
            row.label(text=obj.name, icon="OBJECT_DATA" if is_shared(mod) else "MOD_BOOLEAN")
            op = row.operator(CONTACTBLEND_OT_select.bl_idname, text="", icon="RESTRICT_SELECT_OFF")
            op.object_name, op.mode = obj.name, "OBJECT"
            op = row.operator(CONTACTBLEND_OT_remove.bl_idname, text="", icon="X")
            op.object_name = obj.name
        if on:
            op = body.operator(CONTACTBLEND_OT_select.bl_idname, text="Select Objects", icon="RESTRICT_SELECT_OFF")
            op.object_name, op.mode = on[0][0].name, "OBJECTS"
        if any(not is_shared(m) for _, m in on):
            body.label(text="Some objects draw their own ground copy.", icon="INFO")
    layout.operator(CONTACTBLEND_OT_diagnose.bl_idname, icon="VIEWZOOM")



class CONTACTBLEND_PT_panel(bpy.types.Panel):
    bl_label = "Contact Blend"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Contact"

    def draw(self, ctx):
        draw_panel(self.layout, ctx)


_classes = (CONTACTBLEND_OT_add, CONTACTBLEND_OT_update, CONTACTBLEND_OT_share,
            CONTACTBLEND_OT_fit, CONTACTBLEND_OT_remove, CONTACTBLEND_OT_select,
            CONTACTBLEND_OT_material, CONTACTBLEND_OT_hide_target, CONTACTBLEND_OT_show_ground,
            CONTACTBLEND_OT_bump_only, CONTACTBLEND_OT_diagnose, CONTACTBLEND_PT_panel)


def _menu(self, ctx):
    self.layout.operator(CONTACTBLEND_OT_add.bl_idname, icon="MOD_SIMPLEDEFORM")


@bpy.app.handlers.persistent
def _on_load(_):
    """Migrate and upgrade on file load, so opening an old .blend is all it takes.

    Deliberately does nothing in a file that has never used the add-on: build_group() would
    otherwise create the node group in every file that gets opened.
    """
    if migrate_names() or _our_groups():
        build_group()
    if any(_is_ground_group(ng) for ng in bpy.data.node_groups):
        ground_group()


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.contact_blend_ground = bpy.props.PointerProperty(
        name="Ground", type=bpy.types.Object, poll=_ground_poll,
        description="Blend every selected object into this ground. Leave empty to use the "
                    "selection: the ground, then shift-select the objects")
    bpy.types.Object.contact_blend_displace = bpy.props.BoolProperty(
        name="Displace Ground", get=_get_displace, set=_set_displace,
        description="Conform the ground to this object. Off: shading and material blend only, "
                    "and the ground stays as it is")
    bpy.types.VIEW3D_MT_object.append(_menu)
    # register() can run in a restricted context where bpy.data is not safe to write, so the
    # rename happens on load rather than here. Enabling the add-on with an old file already
    # open misses that -- find_mod() and setup_material() both accept the old names, so the
    # panel keeps working until the first operator run or reload does the rename.
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.types.VIEW3D_MT_object.remove(_menu)
    del bpy.types.Object.contact_blend_displace
    del bpy.types.Scene.contact_blend_ground
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


def _selftest():
    """blender --background --python contact_blend.py"""
    import collections
    bpy.ops.wm.read_factory_settings(use_empty=True)
    register()

    bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=40, y_subdivisions=40)
    ground = bpy.context.active_object
    gmat = bpy.data.materials.new("ground")
    ensure_node_tree(gmat)
    gmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0, 0, 1)
    ground.data.materials.append(gmat)

    # sunk 0.1 into the ground, so there is a real intersection curve to close
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.4))
    bpy.ops.object.modifier_add(type="SUBSURF")
    bpy.context.object.modifiers["Subdivision"].subdivision_type = "SIMPLE"
    bpy.context.object.modifiers["Subdivision"].levels = 3
    bpy.ops.object.modifier_apply(modifier="Subdivision")
    rock = bpy.context.active_object
    rmat = bpy.data.materials.new("rock")
    ensure_node_tree(rmat)
    rock.data.materials.append(rmat)

    ground.select_set(True)
    bpy.context.view_layer.objects.active = rock
    base = [v.co.copy() for v in rock.data.vertices]
    # cube is flat shaded: these must survive the custom-normal write away from the ground
    base_n = [tuple(round(c, 3) for c in l.normal) for l in rock.data.loops]

    assert bpy.ops.object.contact_blend() == {"FINISHED"}
    mod = rock.modifiers[-1]
    ids = input_ids(mod.node_group)
    mod[ids["Surface Offset"]] = 0.01

    def ev_mesh(obj=rock):
        obj.update_tag()
        return obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()

    # New blends share one ground: the hero carries only itself, and the proxy draws the
    # conformed ground for every object on it.
    assert is_shared(mod) and find_proxy(ground) is not None, "new blend is not on a shared ground"
    rock.contact_blend_displace = False
    assert find_proxy(ground) is None, "Displace Ground off left the shared ground behind"
    assert not ground.hide_render, "target not restored when its only ground went away"
    me = ev_mesh()
    assert len(me.vertices) == len(base), (len(me.vertices), len(base))
    moved = max((v.co - b).length for v, b in zip(me.vertices, base))
    assert moved < 1e-6, f"hero mesh was deformed by {moved}"
    assert ATTR in me.attributes, sorted(me.attributes.keys())
    vals = [d.value for d in me.attributes[ATTR].data]
    assert max(vals) > 0.5 and min(vals) < 0.01, (min(vals), max(vals))

    # flat shading must be preserved on the faces away from the ground; only the
    # corners inside Normal Blend Distance may be bent toward the target normal
    ev_n = [tuple(round(c, 3) for c in l.normal) for l in me.loops]
    bent = sum(1 for a, b in zip(base_n, ev_n) if a != b)
    assert 0 < bent < len(base_n), f"{bent}/{len(base_n)} corners bent (want partial)"

    rock.contact_blend_displace = True
    proxy = find_proxy(ground)
    assert proxy is not None and proxy.parent == ground and ground.hide_render
    gmod = ground_mod(proxy)
    gids = input_ids(gmod.node_group)
    gbase = [v.co.copy() for v in ground.data.vertices]
    # The proxy sits on the target with an identity transform, so its geometry is in the
    # target's own space, where the flat grid is at z = 0.
    floor = 0.0

    def ground_polys(me):
        return list(me.polygons)

    def ground_co(me):
        idx = {i for p in ground_polys(me) for i in p.vertices}
        return [me.vertices[i].co.copy() for i in idx]

    def tjunction_verts(me):
        """Vertices on an edge with only one face -- the subdivision cut, plus the outer rim."""
        seen = collections.Counter()
        for p in ground_polys(me):
            vs = list(p.vertices)
            for a, b in zip(vs, vs[1:] + vs[:1]):
                seen[(min(a, b), max(a, b))] += 1
        rim = 0.5 * 4 - 1e-4      # grid is 4m across; its real border is legitimately open
        return {i for e, c in seen.items() if c == 1 for i in e
                if abs(me.vertices[i].co.x) < rim and abs(me.vertices[i].co.y) < rim}

    gmod[gids["Contact Subdivisions"]] = 0
    assert len(ev_mesh().vertices) == len(base), "the hero carries more than its own mesh"
    me = ev_mesh(proxy)
    assert len(me.vertices) == len(gbase), (len(me.vertices), len(gbase))
    coarse = len(ground_co(me))

    gmod[gids["Contact Subdivisions"]] = 2
    me = ev_mesh(proxy)
    gco = ground_co(me)
    assert len(gco) > coarse * 1.5, f"contact zone not subdivided ({coarse} -> {len(gco)})"

    dip = min(c.z for c in gco) - floor
    assert dip < -0.05, f"ground did not conform to the sunk rock (lowest {dip:.4f})"
    # outside the subdivided island the ground must be untouched and dead flat, otherwise the
    # dense patch and the coarse remainder would crack apart along the cut
    outer = [c for c in gco if c.xy.length > 1.5]
    far = max(abs(c.z - floor) for c in outer)
    assert far < 1e-6, f"ground moved {far:.6f} outside Contact Distance"
    assert len(outer) > 100, f"only {len(outer)} verts probed; test is not biting"

    # every vertex left sitting on a T-junction must be frozen, or the cut cracks open and
    # leaks background at grazing angles
    # nothing may flip the ground's shading mode partway across the blend: a smooth/flat
    # boundary is a visible crease ring on any ground that has surface detail
    smooth = {p.use_smooth for p in ground_polys(me)}
    assert smooth == {ground.data.polygons[0].use_smooth}, \
        f"ground shading is no longer uniform: {smooth}"

    tj = tjunction_verts(me)
    assert tj, "no T-junctions found; the island is not being subdivided"
    slip = max(abs(me.vertices[i].co.z - floor) for i in tj)
    assert slip < 1e-6, f"{len(tj)} T-junction verts, worst moved {slip:.6f} -> cracks"

    # materials: the target's shader is mixed in, driven by the 'blend' attribute
    tree = rock.active_material.node_tree
    mix = tree.nodes.get(GROUP)
    assert mix and mix.type == "MIX_SHADER", sorted(n.name for n in tree.nodes)
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    assert out.inputs["Surface"].links[0].from_node.name == mix.name
    # the factor runs Attribute('blend') -> edge breakup -> mix
    brk = mix.inputs[0].links[0].from_node
    assert brk == breakup_node(rock.active_material) and brk.node_tree == breakup_group(), brk.type
    fac = brk.inputs["Blend"].links[0].from_node
    assert fac.type == "ATTRIBUTE" and fac.attribute_name == ATTR, fac.type
    # input 2 is the copied target shader, and its red base color came across
    tgt_bsdf = mix.inputs[2].links[0].from_node
    assert tuple(tgt_bsdf.inputs["Base Color"].default_value) == (1, 0, 0, 1)
    n_before = len(tree.nodes)
    assert setup_material(rock, ground) is None      # idempotent
    assert len(tree.nodes) == n_before, "re-ran setup_material and duplicated nodes"

    # the operator must hide the target from the render too, not just the viewport -- eye-only
    # is the state that looks perfect in the viewport and seams at F12
    assert ground.hide_render and ground.hide_get(), (ground.hide_render, ground.hide_get())
    assert target_leaks(mod) is None, "clean setup reported as leaking"
    ground.hide_render = False                       # exactly the failure the user hit
    assert target_leaks(mod) is ground, "eye-hidden but render-visible went undetected"
    assert bpy.ops.object.contact_blend_hide_target() == {"FINISHED"}
    assert ground.hide_render and target_leaks(mod) is None, "fix button did not fix it"
    mod[ids["Displace Ground"]] = False              # no copy joined, target must stay visible
    ground.hide_render = False
    assert target_leaks(mod) is None, "warned about a visible target with no ground copy"
    mod[ids["Displace Ground"]] = True

    # material displacement moves the target after the modifier stack, where nothing in this
    # node group can see it -- the blend is computed against a surface that is no longer there
    assert target_displaces(mod) == [], "clean material reported as displacing"
    gt = gmat.node_tree
    d = gt.nodes.new("ShaderNodeDisplacement")
    gt.links.new(d.outputs["Displacement"],
                 next(n for n in gt.nodes if n.type == "OUTPUT_MATERIAL").inputs["Displacement"])
    assert target_displaces(mod) == [], "BUMP-only displacement is harmless, must not warn"
    gmat.displacement_method = "BOTH"
    assert target_displaces(mod) == [gmat], "render-time displacement went undetected"
    assert bpy.ops.object.contact_blend_bump_only() == {"FINISHED"}
    assert gmat.displacement_method == "BUMP" and target_displaces(mod) == []

    # a stale node group must upgrade itself on reload, carrying settings across by name,
    # rather than quietly running the tree the .blend was first built with
    ng = mod.node_group
    assert group_version(ng) == GROUP_VERSION, group_version(ng)
    mod[ids["Contact Distance"]] = 0.1234
    ng[VERSION_KEY] = 0                              # pretend the file predates this build
    n_nodes = len(ng.nodes)
    ng2 = build_group()
    assert ng2 is ng, "rebuilt into a different datablock; modifiers would be orphaned"
    assert group_version(ng) == GROUP_VERSION, "version stamp not updated"
    assert len(ng.nodes) == n_nodes, (len(ng.nodes), n_nodes)
    ids = input_ids(ng)
    assert abs(mod[ids["Contact Distance"]] - 0.1234) < 1e-6, "settings lost across rebuild"
    assert mod[ids["Target"]] == ground, "target lost across rebuild"
    assert build_group() is ng and len(ng.nodes) == n_nodes, "rebuilt again when up to date"
    mod[ids["Contact Distance"]] = 0.3

    # --- a .blend saved under the pre-1.1 name must migrate, not orphan ---
    # Put the file back the way an old release left it: group and mix node under the old name,
    # version stamped under the old key. Everything is found by name, so each of those is a
    # separate way to lose track of a setup that is still evaluating perfectly well.
    legacy, legacy_key = LEGACY_NAMES[0], LEGACY_VERSION_KEYS[0]
    ng.name = legacy
    del ng[VERSION_KEY]
    ng[legacy_key] = GROUP_VERSION
    mix = tree.nodes[GROUP]
    mix.name = mix.label = legacy

    # before anything migrates, the panel must still find the modifier -- enabling the add-on
    # on an already-open file never fires the load handler
    assert find_mod(rock) == mod, "old group name hid the modifier from the panel"
    assert group_version(ng) == GROUP_VERSION, "old version key read as unstamped"
    # ...and Blend Materials must recognise its own work rather than mixing a second time
    n_before = len(tree.nodes)
    assert setup_material(rock, ground) is None
    assert len(tree.nodes) == n_before, "old mix node unrecognised; material wired twice"
    assert tree.nodes.get(GROUP) == mix, "mix node not carried forward to the new name"

    mix.name = mix.label = legacy                    # undo, to exercise migrate_names() itself
    assert migrate_names() == 2, "expected the group and the mix node to move"
    assert ng.name == GROUP, "group not renamed; every modifier would be orphaned"
    assert mod.node_group is ng, "renaming the group broke the modifier's pointer"
    assert ng.get(VERSION_KEY) == GROUP_VERSION and legacy_key not in ng, list(ng.keys())
    assert tree.nodes.get(GROUP) == mix and tree.nodes.get(legacy) is None
    assert migrate_names() == 0, "migration is not idempotent"
    assert build_group() is ng and len(ng.nodes) == n_nodes, "migration forced a rebuild"

    # --- contact-zone displacement ---
    # a flat white height map: every sampled pixel is 1.0, so the offset is purely the mask
    img = bpy.data.images.new("height", 8, 8)
    img.pixels = [1.0] * (8 * 8 * 4)
    flat = [v.co.copy() for v in ev_mesh(proxy).vertices]
    gmod[gids["Detail Image"]] = img
    gmod[gids["Ground Detail"]] = 0.25
    me = ev_mesh(proxy)
    assert len(me.vertices) == len(flat), "detail changed the topology"
    moved_by = [(v.co - b).length for v, b in zip(me.vertices, flat)]
    assert max(moved_by) > 0.05, f"detail image had no effect ({max(moved_by):.4f})"

    # it must stay inside the island: nothing beyond Contact Distance may move, and the
    # vertices sitting on the subdivision cut must still be frozen or the cut tears
    gco2 = ground_co(me)
    outer2 = [c for c in gco2 if c.xy.length > 1.5]
    assert max(abs(c.z - floor) for c in outer2) < 1e-6, "detail leaked outside Contact Distance"
    tj2 = tjunction_verts(me)
    assert tj2, "no T-junctions to check"
    assert max(abs(me.vertices[i].co.z - floor) for i in tj2) < 1e-6, \
        "contact-zone detail tore the subdivision cut"
    detail_max = max(moved_by)
    gmod[gids["Ground Detail"]] = 0.0

    unregister()
    print(f"OK  hero_moved={moved:.6f}  ground dip={dip:.4f} far={far:.6f} "
          f"subdiv={coarse}->{len(gco)}  detail={detail_max:.4f}  "
          f"blend={min(vals):.3f}..{max(vals):.3f}  bent={bent}/{len(base_n)}  "
          f"ground={len(me.vertices)}")


if __name__ == "__main__":
    _selftest()

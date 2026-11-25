# SPDX-License-Identifier: Apache-2.0

bl_info = {
    "name": "Contact Blend",
    "author": "amsy",
    "version": (1, 3),
    "blender": (4, 5, 0),
    "location": "Object > Contact Blend",
    "description": "Boolean-free blend of one mesh into another: the ground conforms to the object, plus custom normal weld and a 'blend' attribute for material mixing",
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
GROUP_VERSION = 7
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


def _rebuild(ng):
    """Rebuild `ng`'s tree in place at GROUP_VERSION, carrying modifier settings across.

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
    _build_group(ng)
    _stamp(ng)
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
    # Internal material bindings are configured by setup_material(), not by the user.
    _socket(ng, "Target Material", "NodeSocketMaterial")
    _socket(ng, "Ground Material", "NodeSocketMaterial")

    link = ng.links.new
    gin = _node(ng, "NodeGroupInput", -1500, 0)
    gout = _node(ng, "NodeGroupOutput", 1700, 0)
    g = gin.outputs

    def math(x, y, op, a, b=None):
        m = _node(ng, "ShaderNodeMath", x, y, operation=op)
        for sock, v in ((m.inputs[0], a), (m.inputs[1], b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif v is not None:
                link(v, sock)
        return m.outputs["Value"]

    # --- every distance is in world metres ---
    # Object Info in RELATIVE space hands us the target in the hero's local frame, so every
    # Proximity distance below is in hero-local units. Left alone, "0.3 m" means 0.3 world
    # metres only at scale 1: a hero scaled to 0.1 would get a 3 cm contact band and, in
    # practice, a blend of 0 everywhere. Divide the user's world-space distances by the hero's
    # own scale instead. The mean of the absolute axes is exact for uniform scale; under
    # non-uniform scale no single number converts distances in every direction, so it is an
    # approximation there.
    me = _node(ng, "GeometryNodeSelfObject", -1500, 600)
    me_info = _node(ng, "GeometryNodeObjectInfo", -1300, 600, transform_space="ORIGINAL")
    link(me.outputs[0], me_info.inputs["Object"])
    sabs = _node(ng, "ShaderNodeVectorMath", -1100, 600, operation="ABSOLUTE")
    link(me_info.outputs["Scale"], sabs.inputs[0])
    smean = _node(ng, "ShaderNodeVectorMath", -900, 600, operation="DOT_PRODUCT")
    link(sabs.outputs["Vector"], smean.inputs[0])
    smean.inputs[1].default_value = (1 / 3, 1 / 3, 1 / 3)
    scale = math(-700, 600, "MAXIMUM", smean.outputs["Value"], 1e-6)

    def local(name, y):
        return math(-500, y, "DIVIDE", g[name], scale)

    contact = local("Contact Distance", 700)
    offset = local("Surface Offset", 650)
    normal_dist = local("Normal Blend Distance", 600)
    material_dist = local("Material Blend Distance", 550)
    detail_dist = local("Ground Detail", 500)

    obj = _node(ng, "GeometryNodeObjectInfo", -1300, -260, transform_space="RELATIVE")
    link(g["Target"], obj.inputs["Object"])
    # A ground that is itself a scatter or collection instance arrives as instances, which
    # Proximity and Separate Geometry cannot see into. Realize them, or the blend is empty.
    realize = _node(ng, "GeometryNodeRealizeInstances", -1100, -260)
    link(obj.outputs["Geometry"], realize.inputs["Geometry"])
    raw = realize.outputs["Geometry"]
    pos = _node(ng, "GeometryNodeInputPosition", -1300, -460)
    nor = _node(ng, "GeometryNodeInputNormal", -1300, -560)

    def ramp(x, y, dist_out, far_socket, near, far):
        """Map proximity distance -> value: `near` at contact, `far` at far_socket range."""
        mr = _node(ng, "ShaderNodeMapRange", x, y, interpolation_type="SMOOTHSTEP", clamp=True)
        link(dist_out, mr.inputs[0])
        mr.inputs[1].default_value = 0.0
        link(far_socket, mr.inputs[2])
        if isinstance(near, float):
            mr.inputs[3].default_value = near
        else:
            link(near, mr.inputs[3])
        mr.inputs[4].default_value = far
        return mr.outputs[0]

    def proximity(x, y, geo_out):
        p = _node(ng, "GeometryNodeProximity", x, y, target_element="FACES")
        link(geo_out, p.inputs["Geometry"])
        link(pos.outputs["Position"], p.inputs["Sample Position"])
        return p.outputs["Distance"], p.outputs["Position"]

    def sample_normal(x, y, geo_out):
        """Normal of `geo_out`'s surface at the point closest to each evaluated point."""
        s = _node(ng, "GeometryNodeSampleNearestSurface", x, y, data_type="FLOAT_VECTOR")
        link(geo_out, s.inputs["Mesh"])
        link(nor.outputs["Normal"], s.inputs["Value"])
        link(pos.outputs["Position"], s.inputs["Sample Position"])
        return s.outputs["Value"]

    def vmath(x, y, op, a, b=None, scale=None):
        v = _node(ng, "ShaderNodeVectorMath", x, y, operation=op)
        link(a, v.inputs[0])
        if b is not None:
            link(b, v.inputs[1])
        if scale is not None:
            link(scale, v.inputs[3])
        return v.outputs["Value" if op in ("DISTANCE", "DOT_PRODUCT", "LENGTH") else "Vector"]

    d_src, _ = proximity(-900, -260, raw)

    # --- 'blend' attribute for mixing materials in the shader (1 at contact, 0 away) ---
    store = _node(ng, "GeometryNodeStoreNamedAttribute", -300, 0, data_type="FLOAT", domain="POINT")
    store.inputs["Name"].default_value = ATTR
    link(g["Geometry"], store.inputs["Geometry"])
    link(ramp(-500, -700, d_src, material_dist, 1.0, 0.0), store.inputs["Value"])
    src = store.outputs["Geometry"]

    # --- target: conform the ground to the object ---
    # The seam is the curve where the two surfaces cross, so the only way to remove it is to
    # stop them crossing. Deforming the object cannot do that -- pushing its contact band
    # onto the target flattens the silhouette into a pancake with a hard rim. Deform the
    # ground instead: slide each ground vertex to the closest point on the object (Proximity
    # Position), weighted 1 at contact and 0 at Contact Distance. Ground under the object
    # drops into a crater that hugs its underside; ground just outside rides up its flank as
    # a meniscus. No intersection curve is left, and the hero mesh is untouched.
    d_tgt, p_tgt = proximity(0, -560, src)

    # How closely the ground can hug the object is capped by its vertex density, so a coarse
    # floor facets the crater no matter how the weights are tuned. Subdivide just the contact
    # island. Subdividing one island leaves T-junctions along the cut, and a T-junction cracks
    # open the moment either end of the edge it sits on moves -- the midpoint follows the
    # conform, the coarse edge opposite it stays straight, and the gap between them shows as a
    # sliver of background at grazing angles. So the cut has to fall strictly outside the
    # moving zone, and that includes the new midpoints subdivision puts *on* the cut.
    #
    # Choosing the island by vertex distance gets neither right: a 4 m quad over a 0.5 m rock
    # has every corner beyond Contact Distance, so the island is empty and the quad can never
    # crater however high Contact Subdivisions goes; and an edge between two far corners can
    # still pass within reach of the object, so its midpoint would move. Test whole faces
    # instead. Let c be a face's vertex centroid and r = half its perimeter. Every corner v
    # satisfies |v - c| <= mean_j |v - v_j| <= r (any two corners are joined by a path along
    # the boundary no longer than half of it), and every other point of the face -- edge
    # midpoints included -- lies in the hull of the corners, so also within r of c. Distance to
    # the object is 1-Lipschitz, so d(p) >= d(c) - r for every point p on the face. Put a face
    # in the island when d(c) - r <= Contact Distance. Then:
    #   - a face left out has d(p) > Contact Distance everywhere on it, so every vertex on the
    #     cut -- original or inserted by subdivision -- has conform and detail weight exactly 0;
    #   - equivalently, any vertex that can move has ALL of its faces in the island, so it sits
    #     strictly inside and never on the cut.
    # r is computed as mean edge length * corner count / 2. The edge length is captured on the
    # edge domain first: evaluated in face context, Edge Vertices would hand back face-averaged
    # *positions*, not an average of lengths.
    ends = _node(ng, "GeometryNodeInputMeshEdgeVertices", -700, -1000)
    edge_len = _node(ng, "GeometryNodeCaptureAttribute", -300, -900, domain="EDGE")
    edge_len.capture_items.new("FLOAT", "length")
    link(raw, edge_len.inputs["Geometry"])
    link(vmath(-500, -1000, "DISTANCE", ends.outputs["Position 1"], ends.outputs["Position 2"]),
         edge_len.inputs["length"])
    corners = _node(ng, "GeometryNodeInputMeshFaceNeighbors", -300, -1100)
    reach = math(-100, -1050, "MULTIPLY",
                 math(-100, -1150, "MULTIPLY", edge_len.outputs["length"],
                      corners.outputs["Vertex Count"]), 0.5)
    clear = math(100, -1000, "GREATER_THAN", math(100, -1100, "SUBTRACT", d_tgt, reach), contact)
    island = _node(ng, "FunctionNodeBooleanMath", 300, -1000, operation="NOT")
    link(clear, island.inputs[0])
    sep = _node(ng, "GeometryNodeSeparateGeometry", 300, -900, domain="FACE")
    link(edge_len.outputs["Geometry"], sep.inputs["Geometry"])
    link(island.outputs[0], sep.inputs["Selection"])
    sub = _node(ng, "GeometryNodeSubdivideMesh", 500, -900)
    link(sep.outputs["Selection"], sub.inputs["Mesh"])
    link(g["Contact Subdivisions"], sub.inputs["Level"])

    # Everything that moves the ground runs on the island alone. The remainder is, by the
    # argument above, never moved, so evaluating proximity and normals for every vertex of a
    # half-million-vertex floor only to multiply the result by zero is pure cost.

    # --- real height, but only where there are vertices to carry it ---
    # Subdividing the contact island and then not putting anything in it wastes the density:
    # it only smooths the crater. Displace here instead of over the whole ground, masked by
    # the same falloff so it is zero at Contact Distance -- which keeps it strictly inside the
    # island, and leaves every vertex on the subdivision cut frozen, so the cut still cannot
    # tear. This runs BEFORE the conform: the conform has to be the last thing that touches
    # these positions, or the height would push the ground back off the object it just hugged.
    uv = _node(ng, "GeometryNodeInputNamedAttribute", -300, -1250, data_type="FLOAT_VECTOR")
    link(g["Detail UV Map"], uv.inputs["Name"])
    img = _node(ng, "GeometryNodeImageTexture", -100, -1250)
    link(g["Detail Image"], img.inputs["Image"])
    link(uv.outputs["Attribute"], img.inputs["Vector"])
    # Midlevel defaults to 0 so an unset image, which samples as black, displaces by nothing.
    h = _node(ng, "ShaderNodeMath", 100, -1250, operation="SUBTRACT")
    link(img.outputs["Color"], h.inputs[0])
    link(g["Detail Midlevel"], h.inputs[1])
    amt = math(300, -1250, "MULTIPLY", h.outputs["Value"], detail_dist)
    amt_masked = math(500, -1250, "MULTIPLY", amt, ramp(300, -1450, d_tgt, contact, 1.0, 0.0))
    detail = _node(ng, "GeometryNodeSetPosition", 700, -900)
    link(sub.outputs["Mesh"], detail.inputs["Geometry"])
    link(vmath(500, -1100, "SCALE", nor.outputs["Normal"], scale=amt_masked),
         detail.inputs["Offset"])

    w = ramp(200, -400, d_tgt, contact, g["Ground Conform"], 0.0)
    # Land slightly *inside* the object rather than exactly on it, so the conformed ground is
    # buried instead of coplanar -- coplanar would z-fight along the whole contact band.
    sink = vmath(200, -760, "SCALE", sample_normal(0, -760, src), scale=offset)
    goal = vmath(400, -700, "SUBTRACT", p_tgt, sink)
    move = vmath(600, -560, "SCALE", vmath(600, -700, "SUBTRACT", goal, pos.outputs["Position"]), scale=w)
    sp = _node(ng, "GeometryNodeSetPosition", 900, -900)
    link(detail.outputs["Geometry"], sp.inputs["Geometry"])
    link(move, sp.inputs["Offset"])
    # Deliberately no Set Shade Smooth here. Forcing the bent faces smooth hides faceting on a
    # coarse ground, but it also flips shading mode at exactly Contact Distance: smooth inside,
    # whatever the ground already was outside. On flat test geometry that is invisible; on a
    # ground with real surface detail it is a hard crease ring at that radius -- the seam moved
    # rather than went away. Contact Subdivisions fixes the faceting at its source, so the
    # ground keeps whatever shading it came with and nothing discontinuous is introduced.

    dense = _node(ng, "GeometryNodeJoinGeometry", 1100, -900)
    link(sep.outputs["Inverted"], dense.inputs["Geometry"])
    link(sp.outputs["Geometry"], dense.inputs["Geometry"])
    # Separate Geometry duplicates the vertices along the cut, which splits their normals and
    # would draw a crease ring on any ground that is not dead flat. Weld them back -- but only
    # vertices on an open edge: every cut vertex is on one on both sides of the join (plus the
    # ground's own rim, which is harmless to include), and merging the whole floor would cost
    # a KD-tree over every vertex of it. The cut vertices are frozen, so both copies are still
    # bit-identical and the tiny distance is enough.
    open_edges = _node(ng, "GeometryNodeInputMeshEdgeNeighbors", 1100, -1100)
    is_open = math(1100, -1200, "COMPARE", open_edges.outputs["Face Count"], 1.0)
    is_open.node.inputs[2].default_value = 0.5
    cut = _node(ng, "GeometryNodeCaptureAttribute", 1300, -900, domain="EDGE")
    cut.capture_items.new("BOOLEAN", "open")
    link(dense.outputs["Geometry"], cut.inputs["Geometry"])
    link(is_open, cut.inputs["open"])
    weld = _node(ng, "GeometryNodeMergeByDistance", 1500, -900)
    link(cut.outputs["Geometry"], weld.inputs["Geometry"])
    # Booleans adapt edge -> point with OR, so a vertex is selected if any of its edges is open.
    link(cut.outputs["open"], weld.inputs["Selection"])
    weld.inputs["Distance"].default_value = 1e-5
    tgt = weld.outputs["Geometry"]

    # What the hero's shading reads the ground from. With Displace Ground on, the conformed
    # ground it is welded to; with it off, no ground copy is drawn, so welding normals or
    # sampling UVs from one would match the hero to a surface that is not there -- read the
    # raw target instead. Switch only evaluates the branch it picks, so with Displace Ground
    # off nothing above (island, subdivision, conform, weld) is computed at all.
    ground = _node(ng, "GeometryNodeSwitch", 1100, -1600, input_type="GEOMETRY")
    link(g["Displace Ground"], ground.inputs[0])
    link(raw, ground.inputs[1])
    link(tgt, ground.inputs[2])
    shade_ref = ground.outputs["Output"]

    # Sample UVs from the final conformed ground, whose original corner UVs have survived
    # subdivision and displacement. Sampling before the conform makes the two sides slide
    # apart. Keep this downstream of the conform to avoid a source/target dependency cycle.
    target_uv = _node(ng, "GeometryNodeInputNamedAttribute", 850, -1800, data_type="FLOAT_VECTOR")
    link(g["Target UV Map"], target_uv.inputs["Name"])
    sample_uv = _node(ng, "GeometryNodeSampleNearestSurface", 1050, -1800, data_type="FLOAT_VECTOR")
    link(shade_ref, sample_uv.inputs["Mesh"])
    link(target_uv.outputs["Attribute"], sample_uv.inputs["Value"])
    link(pos.outputs["Position"], sample_uv.inputs["Sample Position"])
    mapped_src = _node(ng, "GeometryNodeStoreNamedAttribute", 1250, -1600,
                       data_type="FLOAT2", domain="CORNER")
    mapped_src.inputs["Name"].default_value = TARGET_UV_ATTR
    link(src, mapped_src.inputs["Geometry"])
    link(sample_uv.outputs["Value"], mapped_src.inputs["Value"])
    uv_switch = _node(ng, "GeometryNodeSwitch", 1450, -1600, input_type="GEOMETRY")
    link(g["Match Target Textures"], uv_switch.inputs[0])
    link(src, uv_switch.inputs[1])
    link(mapped_src.outputs["Geometry"], uv_switch.inputs[2])

    # --- weld the shading: near the target, adopt the target's normal ---
    # Measured on the hero alone and captured, rather than evaluated on the joined mesh: there
    # it would run two nearest-surface queries for every corner of the ground copy too, only
    # to have each one find itself. Corners of the ground carry weight 0 after the join (a
    # captured attribute missing from one side of a join reads as zero), so they keep exactly
    # their own normal.
    weld_n = _node(ng, "GeometryNodeCaptureAttribute", 1450, -1300, domain="CORNER")
    weld_n.capture_items.new("FLOAT", "weight")
    weld_n.capture_items.new("VECTOR", "normal")
    link(uv_switch.outputs["Output"], weld_n.inputs["Geometry"])
    link(ramp(1250, -1250, proximity(1050, -1250, shade_ref)[0], normal_dist, 1.0, 0.0),
         weld_n.inputs["weight"])
    link(sample_normal(1050, -1400, shade_ref), weld_n.inputs["normal"])
    shaded_src = weld_n.outputs["Geometry"]

    # Object/Generated coordinates on joined ground otherwise use the hero's transform
    # and texture space. Replace only the active target material with a corrected copy.
    material_selection = _node(ng, "GeometryNodeMaterialSelection", 1000, -2100)
    link(g["Target Material"], material_selection.inputs["Material"])
    ground_mat = _node(ng, "GeometryNodeSetMaterial", 1250, -2100)
    link(tgt, ground_mat.inputs["Geometry"])
    link(material_selection.outputs["Selection"], ground_mat.inputs["Selection"])
    link(g["Ground Material"], ground_mat.inputs["Material"])
    ground_switch = _node(ng, "GeometryNodeSwitch", 1450, -2100, input_type="GEOMETRY")
    link(g["Match Target Textures"], ground_switch.inputs[0])
    link(tgt, ground_switch.inputs[1])
    link(ground_mat.outputs["Geometry"], ground_switch.inputs[2])

    join = _node(ng, "GeometryNodeJoinGeometry", 1300, -100)
    link(ground_switch.outputs["Output"], join.inputs["Geometry"])
    link(shaded_src, join.inputs["Geometry"])
    sw = _node(ng, "GeometryNodeSwitch", 1450, 0, input_type="GEOMETRY")
    link(g["Displace Ground"], sw.inputs[0])
    link(shaded_src, sw.inputs[1])
    link(join.outputs["Geometry"], sw.inputs[2])

    mix = _node(ng, "ShaderNodeMix", 1450, -1000, data_type="VECTOR")
    link(weld_n.outputs["weight"], mix.inputs[0])
    link(nor.outputs["Normal"], mix.inputs[4])
    link(weld_n.outputs["normal"], mix.inputs[5])

    # CORNER, not POINT: `FREE` mode has no selection input, so the custom normal is
    # written mesh-wide. On POINT that replaces every corner normal with the averaged
    # vertex normal and flattens sharp edges into a blob. On CORNER, Input Normal is the
    # existing corner normal, so far from the target the write is a no-op.
    sn = _node(ng, "GeometryNodeSetMeshNormal", 1600, 0, mode="FREE", domain="CORNER")
    link(sw.outputs["Output"], sn.inputs["Mesh"])
    link(mix.outputs[1], sn.inputs["Custom Normal"])
    link(sn.outputs["Mesh"], gout.inputs[0])
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
    if mod and changed:
        ids = input_ids(mod.node_group)
        ground = tmat
        if match_textures:
            ground = tmat.copy()
            ground.name = tmat.name + " (Contact Blend Ground)"
            ground.use_fake_user = False
            ground[TEXTURE_DATA_KEY] = True
            _match_texture_nodes(ground.node_tree, tgt, uv_name, False)
        mod[ids["Target Material"]] = tmat
        mod[ids["Ground Material"]] = ground
    _cleanup_texture_data()
    return None


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


def mod_target(mod):
    return mod[input_ids(mod.node_group)["Target"]] if mod and mod.node_group else None


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
            if (_is_blend_mod(mod)
                    and mod_target(mod) == tgt
                    and mod[input_ids(mod.node_group)["Displace Ground"]]):
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
    ground_on = mod[input_ids(mod.node_group)["Displace Ground"]]
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
    if not tgt or not mod[input_ids(mod.node_group)["Displace Ground"]]:
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


class CONTACTBLEND_OT_add(bpy.types.Operator):
    """Blend the active object into the other selected object without booleans"""
    bl_idname = "object.contact_blend"
    bl_label = "Contact Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return ctx.active_object is not None and ctx.active_object.type == "MESH"

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        others = [o for o in ctx.selected_objects if o is not src and o.type == "MESH"]
        if len(others) > 1 or (not others and not mod_target(mod)):
            self.report({"ERROR"}, "Select the target mesh, then shift-select the object to blend (active)")
            return {"CANCELLED"}
        tgt = others[0] if others else mod_target(mod)
        if tgt is src:
            self.report({"ERROR"}, "The blend object cannot be its own target")
            return {"CANCELLED"}
        ng = build_group()
        if not mod:
            mod = src.modifiers.new(GROUP, "NODES")
            mod.node_group = ng
            fit_to_object(mod, src)
        mod[input_ids(mod.node_group)["Target"]] = tgt
        src.update_tag()
        # The conformed ground is joined in as a copy, because a modifier on the target that
        # reads the source would be a depsgraph cycle. So the original has to stop drawing:
        # left visible it stays flat, cuts straight through the crater, and that intersection
        # is exactly the hard ring the blend is supposed to remove. Object Info still reads
        # hidden objects, so the copy keeps working.
        _sync_target_visibility(mod, ctx)
        hid = f"; hid '{tgt.name}'" if mod[input_ids(mod.node_group)["Displace Ground"]] else ""
        err = setup_material(src, tgt, refresh=True)
        self.report({"WARNING"} if err else {"INFO"},
                    f"Blending '{src.name}' into '{tgt.name}'"
                    + (f" (materials not mixed: {err})" if err else "") + hid)
        return {"FINISHED"}


class CONTACTBLEND_OT_update(bpy.types.Operator):
    """Refresh the target shader and synchronize visibility without resetting settings"""
    bl_idname = "object.contact_blend_update"
    bl_label = "Update Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return mod_target(find_mod(ctx.active_object)) is not None

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        tgt = mod_target(mod)
        if tgt is src:
            self.report({"ERROR"}, "The blend object cannot be its own target")
            return {"CANCELLED"}
        build_group()
        _sync_target_visibility(mod, ctx)
        src.update_tag()
        err = setup_material(src, tgt, refresh=True)
        self.report({"WARNING"} if err else {"INFO"},
                    f"Updated '{src.name}'" + (f" (materials unchanged: {err})" if err else ""))
        return {"FINISHED"}


class CONTACTBLEND_OT_fit(bpy.types.Operator):
    """Size Contact Distance, blend distances and Surface Offset to the object's width"""
    bl_idname = "object.contact_blend_fit"
    bl_label = "Fit To Object Size"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return find_mod(ctx.active_object) is not None

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        if not fit_to_object(mod, src):
            self.report({"ERROR"}, f"'{src.name}' has no vertices to measure")
            return {"CANCELLED"}
        cd = mod[input_ids(mod.node_group)["Contact Distance"]]
        self.report({"INFO"}, f"Contact Distance {cd:.4g} m for '{src.name}'")
        return {"FINISHED"}


class CONTACTBLEND_OT_remove(bpy.types.Operator):
    """Remove the blend, preserving material edits and restoring recorded target visibility"""
    bl_idname = "object.contact_blend_remove"
    bl_label = "Remove Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return find_mod(ctx.active_object) is not None

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        targets = [mod_target(mod), mod.get(HIDDEN_TARGET_KEY)]
        src.modifiers.remove(mod)
        # Older files may contain duplicate modifiers. Keep the material blend until
        # the last modifier is removed, rather than breaking a remaining blend.
        if not find_mod(src):
            remove_material_blend(src)
        for tgt in targets:
            _restore_target(tgt)
        _cleanup_texture_data()
        src.update_tag()
        self.report({"INFO"}, f"Removed blend from '{src.name}'; kept source material edits")
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


class CONTACTBLEND_PT_panel(bpy.types.Panel):
    bl_label = "Contact Blend"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Contact"

    def draw(self, ctx):
        col = self.layout.column()
        col.operator(CONTACTBLEND_OT_add.bl_idname, icon="MOD_SIMPLEDEFORM")
        mod = find_mod(ctx.active_object)
        if not mod:
            return
        row = col.row(align=True)
        row.operator(CONTACTBLEND_OT_update.bl_idname, icon="FILE_REFRESH")
        row.operator(CONTACTBLEND_OT_remove.bl_idname, icon="X")
        col.operator(CONTACTBLEND_OT_fit.bl_idname, icon="FULLSCREEN_ENTER")
        col.separator()
        ids = input_ids(mod.node_group)
        for label, names in (
                ("Contact", ("Target", "Contact Distance", "Surface Offset", "Displace Ground",
                             "Ground Conform", "Contact Subdivisions")),
                ("Shading", ("Normal Blend Distance", "Material Blend Distance",
                             "Match Target Textures", "Target UV Map")),
                ("Ground Detail", ("Ground Detail", "Detail Image", "Detail UV Map", "Detail Midlevel"))):
            box = col.box().column()
            box.label(text=label)
            for name in names:
                box.prop(mod, f'["{ids[name]}"]', text=name)
        col.separator()
        col.operator(CONTACTBLEND_OT_material.bl_idname, icon="NODE_MATERIAL")
        mat = ctx.active_object.active_material
        if mat and mat.get("cb_texture_warnings"):
            box = col.box().column()
            for warning in mat["cb_texture_warnings"].splitlines():
                box.label(text=warning, icon="ERROR")

        # This one only breaks the render, so it is invisible until it is too late: say so.
        leak = target_leaks(mod)
        if leak:
            box = col.box().column(align=True)
            box.alert = True
            box.label(text=f"'{leak.name}' still draws", icon="ERROR")
            box.label(text="Seam will return in the render.")
            box.operator(CONTACTBLEND_OT_hide_target.bl_idname, icon="RESTRICT_RENDER_ON")

        # Displace Ground ticked but Ground Conform at zero is a contradiction: the ground copy
        # is built and joined, costing everything it costs, and then multiplied out to nothing.
        ids = input_ids(mod.node_group)
        if mod[ids["Displace Ground"]] and mod[ids["Ground Conform"]] < 0.01:
            box = col.box().column(align=True)
            box.alert = True
            box.label(text="Ground Conform is ~0", icon="ERROR")
            box.label(text="Ground cannot move; no blend.")
            box.prop(mod, f'["{ids["Ground Conform"]}"]', text="Ground Conform")

        # Material displacement is applied after the modifier stack, so nothing here can see
        # or compensate for it. Silent in the viewport, ruinous at render.
        for mat in target_displaces(mod):
            box = col.box().column(align=True)
            box.alert = True
            box.label(text=f"'{mat.name}' displaces at render", icon="ERROR")
            box.label(text="Geometry Nodes cannot see it.")
            box.label(text="Rebuild it as a Displace modifier,")
            box.label(text="or:")
            box.operator(CONTACTBLEND_OT_bump_only.bl_idname, icon="MOD_SMOOTH")


_classes = (CONTACTBLEND_OT_add, CONTACTBLEND_OT_update, CONTACTBLEND_OT_fit,
            CONTACTBLEND_OT_remove,
            CONTACTBLEND_OT_material, CONTACTBLEND_OT_hide_target,
            CONTACTBLEND_OT_bump_only, CONTACTBLEND_PT_panel)


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


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object.append(_menu)
    # register() can run in a restricted context where bpy.data is not safe to write, so the
    # rename happens on load rather than here. Enabling the add-on with an old file already
    # open misses that -- find_mod() and setup_material() both accept the old names, so the
    # panel keeps working until the first operator run or reload does the rename.
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.types.VIEW3D_MT_object.remove(_menu)
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

    def ev_mesh():
        rock.update_tag()
        return rock.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()

    mod[ids["Displace Ground"]] = False
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

    mod[ids["Displace Ground"]] = True
    gbase = [v.co.copy() for v in ground.data.vertices]
    # Object Info is RELATIVE, so the copy arrives in the rock's local space, where the flat
    # plane sits at -location.z. Subdivision breaks any 1:1 vertex correspondence with the
    # original grid, so tell the two halves of the join apart by material instead.
    floor = -rock.location.z

    def ground_polys(me):
        ground_material = mod[ids["Ground Material"]]
        names = {gmat.name, ground_material.name if ground_material else ""}
        gi = {i for i, mat in enumerate(me.materials) if mat and mat.name in names}
        return [p for p in me.polygons if p.material_index in gi]

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

    mod[ids["Contact Subdivisions"]] = 0
    me = ev_mesh()
    assert len(me.vertices) == len(base) + len(gbase), len(me.vertices)
    coarse = len(ground_co(me))

    mod[ids["Contact Subdivisions"]] = 2
    me = ev_mesh()
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
    flat = [v.co.copy() for v in ev_mesh().vertices]
    mod[ids["Detail Image"]] = img
    mod[ids["Ground Detail"]] = 0.25
    me = ev_mesh()
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
    mod[ids["Ground Detail"]] = 0.0

    unregister()
    print(f"OK  hero_moved={moved:.6f}  ground dip={dip:.4f} far={far:.6f} "
          f"subdiv={coarse}->{len(gco)}  detail={detail_max:.4f}  "
          f"blend={min(vals):.3f}..{max(vals):.3f}  bent={bent}/{len(base_n)}  "
          f"joined={len(me.vertices)}")


if __name__ == "__main__":
    _selftest()

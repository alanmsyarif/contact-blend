# SPDX-License-Identifier: Apache-2.0

bl_info = {
    "name": "Contact Blend",
    "author": "amsy",
    "version": (1, 1),
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
GROUP_VERSION = 5
# The stamp moved with the rename. An old group carries the value under the old key, so read
# both and rewrite to the current one -- treating the old key as unstamped would force a
# needless rebuild, and the diagnostic would report a group of unknown vintage.
VERSION_KEY = "cb_version"
LEGACY_VERSION_KEYS = ("bts_version",)


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


def _legacy_groups():
    return [ng for ng in (bpy.data.node_groups.get(n) for n in LEGACY_NAMES)
            if ng is not None and _is_ours(ng)]


def migrate_names():
    """Rename anything this add-on left under a pre-1.1 name. Returns how many it moved.

    Only ever renames into a free name: if the current name is already taken the old datablock
    is left alone rather than being pushed to a `.001` suffix, which would leave two groups
    that both look current and neither of which the panel could tell apart.

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
            if not mat.use_nodes:
                continue
            node = mat.node_tree.nodes.get(old)
            if node and node.type == "MIX_SHADER" and not mat.node_tree.nodes.get(GROUP):
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
    """Return the node group, rebuilding it in place if it predates GROUP_VERSION."""
    migrate_names()
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
        _build_group(ng)
        _stamp(ng)
    elif group_version(ng) != GROUP_VERSION:
        _rebuild(ng)
    # A legacy-named group survives migrate_names() only when the current name was already
    # taken, so two of our groups are live in the same file. Upgrade that one too, rather than
    # leaving its modifiers evaluating a tree that this build knows is stale.
    for other in _legacy_groups():
        if other is not ng and group_version(other) != GROUP_VERSION:
            _rebuild(other)
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

    link = ng.links.new
    gin = _node(ng, "NodeGroupInput", -1100, 0)
    gout = _node(ng, "NodeGroupOutput", 1400, 0)
    g = gin.outputs

    obj = _node(ng, "GeometryNodeObjectInfo", -900, -260, transform_space="RELATIVE")
    link(g["Target"], obj.inputs["Object"])
    pos = _node(ng, "GeometryNodeInputPosition", -900, -460)
    nor = _node(ng, "GeometryNodeInputNormal", -900, -560)

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
        return v.outputs["Vector"]

    d_src, _ = proximity(-700, -260, obj.outputs["Geometry"])

    # --- 'blend' attribute for mixing materials in the shader (1 at contact, 0 away) ---
    store = _node(ng, "GeometryNodeStoreNamedAttribute", -300, 0, data_type="FLOAT", domain="POINT")
    store.inputs["Name"].default_value = ATTR
    link(g["Geometry"], store.inputs["Geometry"])
    link(ramp(-500, -700, d_src, g["Material Blend Distance"], 1.0, 0.0), store.inputs["Value"])
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
    # moving zone. Selecting faces by their centre distance is not enough: a face centred well
    # clear of Contact Distance can still have a corner inside it.
    #
    # Capture "this vertex cannot move" per point, then read it back per face. Booleans
    # interpolate point->face with AND, so the captured value on a face means *every* corner
    # is frozen; NOT of it selects every face with at least one movable corner. Any movable
    # vertex therefore has all of its faces in the island, which puts it strictly inside --
    # so every vertex left on the cut is frozen, and the T-junctions there cannot crack.
    frozen = _node(ng, "ShaderNodeMath", -500, -900, operation="GREATER_THAN")
    link(d_tgt, frozen.inputs[0])
    link(g["Contact Distance"], frozen.inputs[1])
    cap = _node(ng, "GeometryNodeCaptureAttribute", -300, -900, domain="POINT")
    cap.capture_items.new("BOOLEAN", "frozen")
    link(obj.outputs["Geometry"], cap.inputs["Geometry"])
    link(frozen.outputs["Value"], cap.inputs["frozen"])
    movable = _node(ng, "FunctionNodeBooleanMath", -100, -900, operation="NOT")
    link(cap.outputs["frozen"], movable.inputs[0])
    sep = _node(ng, "GeometryNodeSeparateGeometry", 100, -900, domain="FACE")
    link(cap.outputs["Geometry"], sep.inputs["Geometry"])
    link(movable.outputs[0], sep.inputs["Selection"])
    sub = _node(ng, "GeometryNodeSubdivideMesh", 300, -900)
    link(sep.outputs["Selection"], sub.inputs["Mesh"])
    link(g["Contact Subdivisions"], sub.inputs["Level"])
    dense = _node(ng, "GeometryNodeJoinGeometry", 500, -900)
    link(sep.outputs["Inverted"], dense.inputs["Geometry"])
    link(sub.outputs["Mesh"], dense.inputs["Geometry"])
    # Separate Geometry duplicates the vertices along the cut, which splits their normals and
    # would draw a crease ring on any ground that is not dead flat. Weld them back.
    weld = _node(ng, "GeometryNodeMergeByDistance", 650, -900)
    link(dense.outputs["Geometry"], weld.inputs["Geometry"])
    weld.inputs["Distance"].default_value = 1e-5

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
    amt = _node(ng, "ShaderNodeMath", 300, -1250, operation="MULTIPLY")
    link(h.outputs["Value"], amt.inputs[0])
    link(g["Ground Detail"], amt.inputs[1])
    amt_masked = _node(ng, "ShaderNodeMath", 500, -1250, operation="MULTIPLY")
    link(amt.outputs["Value"], amt_masked.inputs[0])
    link(ramp(300, -1450, d_tgt, g["Contact Distance"], 1.0, 0.0), amt_masked.inputs[1])
    detail = _node(ng, "GeometryNodeSetPosition", 700, -1250)
    link(weld.outputs["Geometry"], detail.inputs["Geometry"])
    link(vmath(500, -1100, "SCALE", nor.outputs["Normal"], scale=amt_masked.outputs["Value"]),
         detail.inputs["Offset"])

    w = ramp(200, -400, d_tgt, g["Contact Distance"], g["Ground Conform"], 0.0)
    # Land slightly *inside* the object rather than exactly on it, so the conformed ground is
    # buried instead of coplanar -- coplanar would z-fight along the whole contact band.
    sink = vmath(200, -760, "SCALE", sample_normal(0, -760, src), scale=g["Surface Offset"])
    goal = vmath(400, -700, "SUBTRACT", p_tgt, sink)
    move = vmath(600, -560, "SCALE", vmath(600, -700, "SUBTRACT", goal, pos.outputs["Position"]), scale=w)
    sp = _node(ng, "GeometryNodeSetPosition", 800, -560)
    link(detail.outputs["Geometry"], sp.inputs["Geometry"])
    link(move, sp.inputs["Offset"])
    # Deliberately no Set Shade Smooth here. Forcing the bent faces smooth hides faceting on a
    # coarse ground, but it also flips shading mode at exactly Contact Distance: smooth inside,
    # whatever the ground already was outside. On flat test geometry that is invisible; on a
    # ground with real surface detail it is a hard crease ring at that radius -- the seam moved
    # rather than went away. Contact Subdivisions fixes the faceting at its source, so the
    # ground keeps whatever shading it came with and nothing discontinuous is introduced.
    tgt = sp.outputs["Geometry"]

    join = _node(ng, "GeometryNodeJoinGeometry", 700, -100)
    link(tgt, join.inputs["Geometry"])
    link(src, join.inputs["Geometry"])
    sw = _node(ng, "GeometryNodeSwitch", 900, 0, input_type="GEOMETRY")
    link(g["Displace Ground"], sw.inputs[0])
    link(src, sw.inputs[1])
    link(join.outputs["Geometry"], sw.inputs[2])

    # --- weld the shading: near the target, adopt the target's normal ---
    mix = _node(ng, "ShaderNodeMix", 1100, -1000, data_type="VECTOR")
    link(ramp(900, -1250, proximity(700, -1250, tgt)[0], g["Normal Blend Distance"], 0.0, 1.0), mix.inputs[0])
    link(sample_normal(700, -1000, tgt), mix.inputs[4])
    link(nor.outputs["Normal"], mix.inputs[5])

    # CORNER, not POINT: `FREE` mode has no selection input, so the custom normal is
    # written mesh-wide. On POINT that replaces every corner normal with the averaged
    # vertex normal and flattens sharp edges into a blob. On CORNER, Input Normal is the
    # existing corner normal, so far from the target the write is a no-op.
    sn = _node(ng, "GeometryNodeSetMeshNormal", 1250, 0, mode="FREE", domain="CORNER")
    link(sw.outputs["Output"], sn.inputs["Mesh"])
    link(mix.outputs[1], sn.inputs["Custom Normal"])
    link(sn.outputs["Mesh"], gout.inputs[0])
    return ng


def _surface(mat):
    """Active output node of `mat` and the socket feeding its Surface, or (None, None)."""
    if not mat or not mat.use_nodes:
        return None, None
    out = next((n for n in mat.node_tree.nodes
                if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if not out or not out.inputs["Surface"].is_linked:
        return None, None
    return out, out.inputs["Surface"].links[0].from_socket


_SKIP_PROPS = {"rna_type", "name", "parent", "internal_links", "dimensions", "select", "location"}


def _copy_nodes(src_tree, dst_tree, offset):
    """Clone every non-output node of `src_tree` into `dst_tree`. Returns {old: new}."""
    m = {}
    for n in src_tree.nodes:
        if n.type == "OUTPUT_MATERIAL":
            continue
        c = dst_tree.nodes.new(n.bl_idname)
        for p in n.bl_rna.properties:
            if p.is_readonly or p.identifier in _SKIP_PROPS:
                continue
            try:
                setattr(c, p.identifier, getattr(n, p.identifier))
            except Exception:
                pass
        c.location = (n.location[0] + offset[0], n.location[1] + offset[1])
        for a, b in zip(n.inputs, c.inputs):
            try:
                b.default_value = a.default_value
            except Exception:
                pass
        m[n] = c
    for l in src_tree.links:
        f, t = m.get(l.from_node), m.get(l.to_node)
        if not f or not t:
            continue
        dst_tree.links.new(f.outputs[list(l.from_node.outputs).index(l.from_socket)],
                           t.inputs[list(l.to_node.inputs).index(l.to_socket)])
    return m


def setup_material(src, tgt):
    """Mix the target's shader into the source's, driven by the `blend` attribute.

    Returns a message on failure, None on success. Idempotent: the mix node is named
    GROUP, so re-running on an already-wired material is a no-op.
    """
    smat, tmat = src.active_material, tgt.active_material
    sout, sshader = _surface(smat)
    _, tshader = _surface(tmat)
    if not sshader:
        return f"'{src.name}' has no node material with a linked Surface"
    if not tshader:
        return f"'{tgt.name}' has no node material with a linked Surface"
    st = smat.node_tree
    # Also recognise the pre-1.1 node name, and move it forward while we are here. Missing it
    # would not raise -- it would silently mix a second copy of the target shader into a
    # material that was already wired, every time the button is pressed.
    for name in (GROUP,) + LEGACY_NAMES:
        node = st.nodes.get(name)
        if node and node.type == "MIX_SHADER":
            node.name = node.label = GROUP
            return None

    # nodes.new() can invalidate existing RNA pointers, so note how to find the sockets
    # again by name+index and re-resolve them after every node has been created
    out_name, sh_name = sout.name, sshader.node.name
    sh_idx = list(sshader.node.outputs).index(sshader)
    tgt_name, tgt_idx = tshader.node.name, list(tshader.node.outputs).index(tshader)

    offset = (sshader.node.location[0] - 600, sshader.node.location[1] - 900)
    m = _copy_nodes(tmat.node_tree, st, offset)
    copy_name = m[tmat.node_tree.nodes[tgt_name]].name
    base = tuple(st.nodes[out_name].location)

    attr = st.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = ATTR
    attr.location = (base[0] - 500, base[1] - 400)
    attr_name = attr.name
    mix = st.nodes.new("ShaderNodeMixShader")
    mix.name = mix.label = GROUP
    mix.location = (base[0] - 200, base[1])
    mix_name = mix.name

    n, mix = st.nodes, st.nodes[mix_name]
    st.links.new(n[attr_name].outputs["Fac"], mix.inputs[0])
    st.links.new(n[sh_name].outputs[sh_idx], mix.inputs[1])     # blend=0: source shader
    st.links.new(n[copy_name].outputs[tgt_idx], mix.inputs[2])  # blend=1: target shader
    st.links.new(mix.outputs[0], n[out_name].inputs["Surface"])
    return None


def input_ids(ng):
    return {i.name: i.identifier for i in ng.interface.items_tree
            if i.item_type == "SOCKET" and i.in_out == "INPUT"}


def find_mod(obj):
    # Legacy names included so the panel still finds the modifier in the window between a
    # pre-1.1 file being opened and migrate_names() running -- enabling the add-on after the
    # file is already open misses the load handler entirely.
    names = (GROUP,) + LEGACY_NAMES
    return obj and next((m for m in obj.modifiers if m.type == "NODES"
                         and m.node_group and m.node_group.name in names), None)


def mod_target(mod):
    return mod[input_ids(mod.node_group)["Target"]] if mod and mod.node_group else None


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
    return tgt if (not tgt.hide_render or not tgt.hide_get()) else None


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
        if not mat or not mat.use_nodes:
            continue
        if getattr(mat, "displacement_method", "BUMP") == "BUMP":
            continue
        if any(n.type == "OUTPUT_MATERIAL" and n.inputs["Displacement"].is_linked
               for n in mat.node_tree.nodes):
            out.append(mat)
    return out


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
        others = [o for o in ctx.selected_objects if o is not src and o.type == "MESH"]
        if len(others) != 1:
            self.report({"ERROR"}, "Select the target mesh, then shift-select the object to blend (active)")
            return {"CANCELLED"}
        tgt = others[0]
        ng = build_group()
        mod = src.modifiers.new(GROUP, "NODES")
        mod.node_group = ng
        mod[input_ids(ng)["Target"]] = tgt
        src.update_tag()
        # The conformed ground is joined in as a copy, because a modifier on the target that
        # reads the source would be a depsgraph cycle. So the original has to stop drawing:
        # left visible it stays flat, cuts straight through the crater, and that intersection
        # is exactly the hard ring the blend is supposed to remove. Object Info still reads
        # hidden objects, so the copy keeps working.
        hid = ""
        if mod[input_ids(ng)["Displace Ground"]]:
            tgt.hide_set(True)
            tgt.hide_render = True
            hid = f"; hid '{tgt.name}' (the joined copy replaces it)"
        err = setup_material(src, tgt)
        self.report({"WARNING"} if err else {"INFO"},
                    f"Blending '{src.name}' into '{tgt.name}'"
                    + (f" (materials not mixed: {err})" if err else "") + hid)
        return {"FINISHED"}


class CONTACTBLEND_OT_material(bpy.types.Operator):
    """Wire the target's shader into the active object's material, mixed by 'blend'"""
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
        err = setup_material(src, tgt)
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
        tgt = target_leaks(find_mod(ctx.active_object))
        tgt.hide_set(True)
        tgt.hide_render = True
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
        col.separator()
        for name, ident in input_ids(mod.node_group).items():
            if name != "Geometry":
                col.prop(mod, f'["{ident}"]', text=name)
        col.separator()
        col.operator(CONTACTBLEND_OT_material.bl_idname, icon="NODE_MATERIAL")

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


_classes = (CONTACTBLEND_OT_add, CONTACTBLEND_OT_material, CONTACTBLEND_OT_hide_target,
            CONTACTBLEND_OT_bump_only, CONTACTBLEND_PT_panel)


def _menu(self, ctx):
    self.layout.operator(CONTACTBLEND_OT_add.bl_idname, icon="MOD_SIMPLEDEFORM")


@bpy.app.handlers.persistent
def _on_load(_):
    """Migrate and upgrade on file load, so opening an old .blend is all it takes.

    Deliberately does nothing in a file that has never used the add-on: build_group() would
    otherwise create the node group in every file that gets opened.
    """
    if migrate_names() or bpy.data.node_groups.get(GROUP):
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
    gmat.use_nodes = True
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
    rmat.use_nodes = True
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
        gi = [m.name if m else "" for m in me.materials].index(gmat.name)
        return [p for p in me.polygons if p.material_index == gi]

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
    fac = mix.inputs[0].links[0].from_node
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

# SPDX-License-Identifier: Apache-2.0

bl_info = {
    "name": "Contact Blend",
    "author": "amsy",
    "version": (1, 4, 0),
    "blender": (4, 5, 0),
    "location": "Object > Contact Blend",
    "description": "Boolean-free blend of one mesh into another: the ground conforms to the object, plus custom normal weld and a 'blend' attribute for material mixing",
    "category": "Object",
    "license": "SPDX:Apache-2.0",
}

import bpy
import textwrap

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
MODIFIER_STATE_KEY = "cb_modifier_state"


class _ModifierValues:
    """Blender 5.2 exposes node inputs as RNA values instead of modifier ID properties.

    Keep add-on bookkeeping on the owning object, keyed by the modifier's persistent UID.
    Reading this adapter never creates properties, so operator polls stay read-only.
    """
    def __init__(self, mod):
        self.mod = mod

    def _input(self, key):
        socket = getattr(self.mod.properties.inputs, key, None)
        return socket if socket is not None and hasattr(socket, "value") else None

    def _state(self, create=False):
        obj = self.mod.id_data
        key = str(self.mod.persistent_uid)
        if create:
            if MODIFIER_STATE_KEY not in obj:
                obj[MODIFIER_STATE_KEY] = {}
            if key not in obj[MODIFIER_STATE_KEY]:
                obj[MODIFIER_STATE_KEY][key] = {}
        states = obj.get(MODIFIER_STATE_KEY)
        return states.get(key) if states is not None else None

    def __getitem__(self, key):
        socket = self._input(key)
        if socket is not None:
            return socket.value
        state = self._state()
        if state is not None and key in state:
            return state[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        socket = self._input(key)
        if socket is not None:
            socket.value = value
        else:
            self._state(create=True)[key] = value

    def __delitem__(self, key):
        state = self._state()
        if state is None or key not in state:
            raise KeyError(key)
        del state[key]

    def __contains__(self, key):
        if self._input(key) is not None:
            return True
        state = self._state()
        return state is not None and key in state

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def modifier_values(mod):
    """Return socket values and add-on metadata across supported Blender versions."""
    return _ModifierValues(mod) if hasattr(mod, "properties") else mod


def _draw_modifier_input(layout, mod, identifier, text, search_data=None, search_property=None):
    if hasattr(mod, "properties"):
        owner, path = getattr(mod.properties.inputs, identifier), "value"
    else:
        owner, path = mod, f'["{identifier}"]'
    if search_data is not None and search_property:
        layout.prop_search(owner, path, search_data, search_property, text=text)
    else:
        layout.prop(owner, path, text=text)


def _input_options(mod, identifier):
    if not hasattr(mod, "properties"):
        return {suffix: mod[identifier + suffix]
                for suffix in ("_use_attribute", "_attribute_name")
                if identifier + suffix in mod}
    socket = getattr(mod.properties.inputs, identifier, None)
    return {name: getattr(socket, name) for name in ("type", "attribute_name", "layer_name")
            if hasattr(socket, name)}


def _restore_input_options(mod, identifier, options):
    for name, value in options.items():
        if hasattr(mod, "properties"):
            setattr(getattr(mod.properties.inputs, identifier), name, value)
        else:
            mod[identifier + name] = value


def _release_modifier_inputs(mod):
    """Release typed ID references before 5.2 discards their dynamic RNA storage.

    Blender 5.2.3 leaves their user counts incremented when a modifier or interface
    socket is removed. Assigning None through the public input API releases them.
    """
    if not hasattr(mod, "properties") or not mod.node_group:
        return
    for identifier in input_ids(mod.node_group).values():
        socket = getattr(mod.properties.inputs, identifier, None)
        value_property = socket.bl_rna.properties.get("value") if socket else None
        if value_property and value_property.type == "POINTER":
            socket.value = None


def _socket(ng, name, stype, in_out="INPUT", **kw):
    # Reuse compatible interface sockets during upgrades. Their identifiers appear
    # in animation paths, drivers, and links from any parent node groups.
    s = next((item for item in ng.interface.items_tree
              if item.item_type == "SOCKET" and item.name == name
              and item.in_out == in_out and item.socket_type == stype), None)
    if s is None:
        s = ng.interface.new_socket(name=name, in_out=in_out, socket_type=stype)
    descriptions = {
        "Target": "Ground mesh to blend into. Click Update Blend after choosing a new target",
        "Contact Distance": "Width of the contact effect in the object's local units",
        "Contact Subdivisions": "Full / render subdivision level; each level makes four times as many contact faces",
        "Viewport Subdivisions": "Lower subdivision level for interactive editing; never changes render quality",
        "Use Viewport Quality": "Use a separate subdivision level in the viewport and retain full quality for rendering",
        "Ground Conform": "Strength of the ground deformation. Zero leaves the ground unchanged",
        "Surface Offset": "Small inset inside the hero mesh to prevent overlapping surfaces from flickering",
        "Displace Ground": "Conform and join the ground. Off uses shading only; click Update Blend to restore target visibility",
        "Match Target Textures": "Project target texture coordinates. Click Update Blend after changing this setting",
        "Target UV Map": "Target UV layer to project onto the hero. Click Update Blend after changing the layer",
        "Ground Detail": "Height of the image-based ground detail inside the contact zone",
        "Detail Midlevel": "Neutral height in the detail image; use 0.5 for a centered height map",
    }
    if name in descriptions:
        s.description = descriptions[name]
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

    In place keeps every existing modifier pointing at it. Reuse compatible sockets so
    keyframes, drivers and parent-group links retain their paths. Settings are restored
    by socket name; newly added or renamed sockets receive their new defaults.
    """
    old = input_ids(ng)
    saved = [(obj.name, mod.name,
              {n: (modifier_values(mod)[i], _input_options(mod, i))
               for n, i in old.items() if n != "Geometry" and i in modifier_values(mod)})
             for obj, mod in _users(ng)]
    for _, mod in _users(ng):
        _release_modifier_inputs(mod)
    ng.nodes.clear()
    _build_group(ng)
    _stamp(ng)
    new = input_ids(ng)
    for obj_name, mod_name, vals in saved:
        mod = bpy.data.objects[obj_name].modifiers.get(mod_name)
        for name, (v, options) in vals.items():
            if mod and name in new:
                try:
                    modifier_values(mod)[new[name]] = v
                    _restore_input_options(mod, new[name], options)
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
    _socket(ng, "Viewport Subdivisions", "NodeSocketInt", default_value=2, min_value=0, max_value=5)
    _socket(ng, "Use Viewport Quality", "NodeSocketBool", default_value=False)
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
    # Keep the original subdivision setting for rendering. Viewport-only quality is
    # opt-in, so upgrading an existing blend retains its established density.
    viewport = _node(ng, "GeometryNodeIsViewport", -300, -1100)
    preview = _node(ng, "FunctionNodeBooleanMath", -100, -1100, operation="AND")
    link(viewport.outputs[0], preview.inputs[0])
    link(g["Use Viewport Quality"], preview.inputs[1])
    levels = _node(ng, "GeometryNodeSwitch", 100, -1100, input_type="INT")
    link(preview.outputs[0], levels.inputs[0])
    link(g["Contact Subdivisions"], levels.inputs[1])
    link(g["Viewport Subdivisions"], levels.inputs[2])
    link(levels.outputs["Output"], sub.inputs["Level"])
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

    # Sample UVs from the final conformed ground, whose original corner UVs have survived
    # subdivision and displacement. Sampling before the conform makes the two sides slide
    # apart. Keep this downstream of the conform to avoid a source/target dependency cycle.
    uv_geo = _node(ng, "GeometryNodeSwitch", 850, -1600, input_type="GEOMETRY")
    link(g["Displace Ground"], uv_geo.inputs[0])
    link(obj.outputs["Geometry"], uv_geo.inputs[1])
    link(tgt, uv_geo.inputs[2])
    target_uv = _node(ng, "GeometryNodeInputNamedAttribute", 850, -1800, data_type="FLOAT_VECTOR")
    link(g["Target UV Map"], target_uv.inputs["Name"])
    sample_uv = _node(ng, "GeometryNodeSampleNearestSurface", 1050, -1600, data_type="FLOAT_VECTOR")
    link(uv_geo.outputs["Output"], sample_uv.inputs["Mesh"])
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
    shaded_src = uv_switch.outputs["Output"]

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

    join = _node(ng, "GeometryNodeJoinGeometry", 700, -100)
    link(ground_switch.outputs["Output"], join.inputs["Geometry"])
    link(shaded_src, join.inputs["Geometry"])
    sw = _node(ng, "GeometryNodeSwitch", 900, 0, input_type="GEOMETRY")
    link(g["Displace Ground"], sw.inputs[0])
    link(shaded_src, sw.inputs[1])
    link(join.outputs["Geometry"], sw.inputs[2])

    # --- weld the shading: near the target, adopt the target's normal ---
    mix = _node(ng, "ShaderNodeMix", 1100, -1000, data_type="VECTOR")
    # Shading-only mode must sample the unchanged target. Sampling `tgt` here would
    # evaluate the entire conform/subdivision branch even when its geometry is disabled,
    # and let unused ground-detail settings change the hero's shading normals.
    shading_target = uv_geo.outputs["Output"]
    link(ramp(900, -1250, proximity(700, -1250, shading_target)[0], g["Normal Blend Distance"], 0.0, 1.0), mix.inputs[0])
    link(sample_normal(700, -1000, shading_target), mix.inputs[4])
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


def _upstream_node_names(tree, roots):
    """Visit shader dependencies once, without inspecting RNA on unused nodes."""
    incoming = {}
    for link in tree.links:
        incoming.setdefault(link.to_node.name, []).append(link.from_node.name)
    names, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in names:
            continue
        names.add(name)
        pending.extend(incoming.get(name, ()))
    # Frames are visual parents, not shader dependencies, but keep their hierarchy.
    for name in tuple(names):
        parent = tree.nodes[name].parent
        while parent:
            names.add(parent.name)
            parent = parent.parent
    return names


def _output_node_names(tree):
    # Keep every renderer-specific material output. For shader groups only the active
    # group output is evaluated; inactive alternatives must not force recursive copies.
    material = [n.name for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    if material:
        return _upstream_node_names(tree, material)
    outputs = [n for n in tree.nodes if n.type == "GROUP_OUTPUT"]
    roots = [n.name for n in outputs if n.is_active_output] or [n.name for n in outputs]
    return _upstream_node_names(tree, roots)


def _group_output_node_names(tree, identifiers):
    outputs = [n for n in tree.nodes if n.type == "GROUP_OUTPUT" and n.is_active_output]
    if not outputs:
        outputs = [n for n in tree.nodes if n.type == "GROUP_OUTPUT"]
    root_names = {n.name for n in outputs}
    roots = [link.from_node.name for link in tree.links
             if link.to_node.name in root_names and link.to_socket.identifier in identifiers]
    return _upstream_node_names(tree, roots) | root_names


def _copy_nodes(src_tree, dst_tree, offset, node_names=None):
    """Clone selected shader dependencies. Returns {old: new}, including visual frames."""
    m = {}
    for n in src_tree.nodes:
        if n.type == "OUTPUT_MATERIAL" or (node_names is not None and n.name not in node_names):
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
    for original, copied in m.items():
        if original.parent in m:
            copied.parent = m[original.parent]
            copied.location = original.location
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
            identifiers = frozenset(link.from_socket.identifier for link in tree.links
                                    if link.from_node.name == name
                                    and (node_names is None or link.to_node.name in node_names))
            key = (old, identifiers)
            if key not in groups:
                copied = old.copy()
                copied.name = old.name + " (Contact Blend)"
                copied.use_fake_user = False
                copied[TEXTURE_DATA_KEY] = True
                groups[key] = copied
                relevant = _group_output_node_names(copied, identifiers)
                warnings |= _match_texture_nodes(copied, tgt, uv_name, project_uv, groups,
                                                node_names=relevant)
            tree.nodes[name].node_tree = groups[key]
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
        unused = [ng for ng in bpy.data.node_groups if ng.get(TEXTURE_DATA_KEY) and ng.users == 0]
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
    values = modifier_values(mod)
    initialized = values.get("cb_uv_initialized")
    current = values[ids["Target UV Map"]]
    # Old .blend files can retain their socket values while Blender 5.2 discards the
    # former modifier custom properties. Preserve a valid saved UV choice in that case.
    if not initialized and current in tgt.data.uv_layers:
        uv_name = current
    elif initialized and values.get("cb_uv_target") == tgt:
        uv_name = current
    values[ids["Target UV Map"]] = uv_name
    values["cb_uv_initialized"] = True
    values["cb_uv_target"] = tgt
    return bool(values[ids["Match Target Textures"]]), uv_name, mod


def _material_mix(mat):
    if not mat or not mat.use_nodes:
        return None
    nodes = mat.node_tree.nodes
    tagged = next((n for n in nodes if n.type == "MIX_SHADER"
                   and n.get(GENERATED_KEY) == "mix"), None)
    if tagged:
        return tagged
    return next((n for n in nodes if n.type == "MIX_SHADER"
                 and not n.get(GENERATED_KEY) and n.name in (GROUP,) + LEGACY_NAMES), None)


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
    parents = {n.name: n.parent.name for n in tree.nodes if n.parent}
    keep |= {parent for child, parent in parents.items() if child not in names and parent in names}
    while True:
        upstream = {l.from_node.name for l in tree.links
                    if l.to_node.name in keep and l.from_node.name in names}
        upstream |= {parents[name] for name in keep if name in parents and parents[name] in names}
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

    Returns a message on failure, None on success. Idempotent: the mix node is named
    GROUP, so re-running on an already-wired material is a no-op. Explicit refresh
    replaces only the generated target branch, preserving the source shader and user nodes.
    """
    if src is tgt:
        return "The blend object cannot be its own target"
    smat, tmat = src.active_material, tgt.active_material
    sout, sshader = _surface(smat)
    _, tshader = _surface(tmat)
    if not sshader:
        return f"'{src.name}' has no node material with a linked Surface"
    if not tshader:
        return f"'{tgt.name}' has no node material with a linked Surface"
    smat = _private_material(src, src.material_slots[src.active_material_index])
    sout, sshader = _surface(smat)
    st = smat.node_tree
    # Also recognise the pre-1.1 node name, and move it forward while we are here. Missing it
    # would not raise -- it would silently mix a second copy of the target shader into a
    # material that was already wired, every time the button is pressed.
    mix = _material_mix(smat)
    if mix:
        mix.name = mix.label = GROUP
        if not refresh:
            return None

    # nodes.new() can invalidate existing RNA pointers, so note how to find the sockets
    # again by name+index and re-resolve them after every node has been created
    out_name, sh_name = sout.name, sshader.node.name
    sh_idx = list(sshader.node.outputs).index(sshader)
    tgt_name, tgt_idx = tshader.node.name, list(tshader.node.outputs).index(tshader)

    old_target_nodes = {n.name for n in st.nodes if n.get(GENERATED_KEY) == "target"}
    mix_name = mix.name if mix else None
    offset = (sshader.node.location[0] - 600, sshader.node.location[1] - 900)
    surface_nodes = _upstream_node_names(tmat.node_tree, [tgt_name])
    m = _copy_nodes(tmat.node_tree, st, offset, node_names=surface_nodes)
    for node in m.values():
        node[GENERATED_KEY] = "target"
    copy_name = m[tmat.node_tree.nodes[tgt_name]].name
    match_textures, uv_name, mod = _texture_settings(src, tgt)
    warnings = set()
    if match_textures:
        warnings = _match_texture_nodes(st, tgt, uv_name, True,
                                       node_names={node.name for node in m.values()})
    smat["cb_texture_warnings"] = "\n".join(sorted(warnings))
    if mod:
        ids = input_ids(mod.node_group)
        ground = tmat
        if match_textures:
            ground_nodes = _output_node_names(tmat.node_tree)
            needs_coordinates = any(n.name in ground_nodes and n.type in {
                "GROUP", "TEX_COORD", "UVMAP", "NORMAL_MAP", "TEX_IMAGE", "TEX_NOISE",
                "TEX_VORONOI", "TEX_WAVE", "TEX_CHECKER", "TEX_BRICK", "TEX_GRADIENT",
                "TEX_MAGIC", "TEX_WHITE_NOISE", "TEX_GABOR"} for n in tmat.node_tree.nodes)
            if needs_coordinates:
                ground = tmat.copy()
                ground.name = tmat.name + " (Contact Blend Ground)"
                ground.use_fake_user = False
                ground[TEXTURE_DATA_KEY] = True
                _match_texture_nodes(ground.node_tree, tgt, uv_name, False,
                                     node_names=ground_nodes)
        modifier_values(mod)[ids["Target Material"]] = tmat
        modifier_values(mod)[ids["Ground Material"]] = ground
    if mix_name:
        st.links.new(st.nodes[copy_name].outputs[tgt_idx], st.nodes[mix_name].inputs[2])
        _prune_generated(st, old_target_nodes)
        _cleanup_texture_data()
        if mod:
            modifier_values(mod)["cb_applied_uv"] = uv_name
            modifier_values(mod)["cb_applied_texture_match"] = match_textures
        return None
    base = tuple(st.nodes[out_name].location)

    attr = st.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = ATTR
    attr[GENERATED_KEY] = "attribute"
    attr.location = (base[0] - 500, base[1] - 400)
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
    _cleanup_texture_data()
    if mod:
        modifier_values(mod)["cb_applied_uv"] = uv_name
        modifier_values(mod)["cb_applied_texture_match"] = match_textures
    return None


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


def find_mod(obj):
    # Legacy names included so the panel still finds the modifier in the window between a
    # pre-1.1 file being opened and migrate_names() running -- enabling the add-on after the
    # file is already open misses the load handler entirely.
    names = (GROUP,) + LEGACY_NAMES
    return obj and next((m for m in obj.modifiers if m.type == "NODES"
                         and m.node_group and m.node_group.name in names), None)


def mod_target(mod):
    if not mod or not mod.node_group:
        return None
    identifier = input_ids(mod.node_group).get("Target")
    return modifier_values(mod).get(identifier) if identifier else None


def _hide_target(tgt, ctx):
    """Remember visibility once per target and per scene/view layer before hiding."""
    if VISIBILITY_KEY not in tgt:
        tgt[VISIBILITY_KEY] = {"render": tgt.hide_render, "layers": {}}
    state = tgt[VISIBILITY_KEY]
    recorded = any(saved.get("scene") == ctx.scene and saved["layer"] == ctx.view_layer.name
                   for saved in state["layers"].values())
    if not recorded:
        key = str(len(state["layers"]))
        state["layers"][key] = {"scene": ctx.scene, "layer": ctx.view_layer.name,
                                "hidden": tgt.hide_get(view_layer=ctx.view_layer)}
    tgt.hide_set(True, view_layer=ctx.view_layer)
    tgt.hide_render = True


def _restore_target(tgt):
    """Restore only after the last ground-copy user releases the target."""
    if not tgt or VISIBILITY_KEY not in tgt:
        return
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if (mod.type == "NODES" and mod.node_group
                    and mod.node_group.name in (GROUP,) + LEGACY_NAMES
                    and mod_target(mod) == tgt
                    and modifier_values(mod)[input_ids(mod.node_group)["Displace Ground"]]):
                return
    state = tgt[VISIBILITY_KEY]
    # A changed flag belongs to the user; restore only values still set by us.
    if tgt.hide_render:
        tgt.hide_render = state["render"]
    for saved in state["layers"].values():
        scene = saved.get("scene")
        layer = scene.view_layers.get(saved["layer"]) if scene else None
        if layer and tgt.hide_get(view_layer=layer):
            tgt.hide_set(saved["hidden"], view_layer=layer)
    del tgt[VISIBILITY_KEY]


def _sync_target_visibility(mod, ctx):
    tgt = mod_target(mod)
    old = modifier_values(mod).get(HIDDEN_TARGET_KEY)
    ground_on = modifier_values(mod)[input_ids(mod.node_group)["Displace Ground"]]
    if old and (old != tgt or not ground_on):
        del modifier_values(mod)[HIDDEN_TARGET_KEY]
        _restore_target(old)
    if tgt and ground_on:
        _hide_target(tgt, ctx)
        modifier_values(mod)[HIDDEN_TARGET_KEY] = tgt


def target_leaks(mod):
    """True if the target will still draw in the render and re-cut the seam.

    The eye and the camera are separate flags. Hiding the target with the eye alone makes
    the viewport look perfect while F12 keeps drawing the original flat surface straight
    through the crater -- the seam comes back in the render only. Object Info reads hidden
    objects under both EEVEE and Cycles, so there is no cost to hiding it from both.
    """
    tgt = mod_target(mod)
    if not tgt or not modifier_values(mod)[input_ids(mod.node_group)["Displace Ground"]]:
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


def _editing_problem(ctx):
    src = ctx.active_object
    if src is None:
        return "Select a mesh object to begin"
    if src.type != "MESH":
        return "The active object must be a mesh"
    if getattr(ctx, "mode", getattr(src, "mode", "OBJECT")) != "OBJECT":
        return "Switch to Object Mode to edit a blend"
    if not src.is_editable:
        return "Make the linked mesh object editable first"
    return ""


def _ui_context(ctx):
    src = ctx.active_object
    mod = find_mod(src) if src and src.type == "MESH" else None
    ids = input_ids(mod.node_group) if mod else {}
    values = modifier_values(mod) if mod else None
    tgt = values.get(ids["Target"]) if "Target" in ids else None
    return src, mod, tgt, ids, values


def _ui_message(layout, text, ctx, icon="INFO"):
    region = getattr(ctx, "region", None)
    width = max(22, int(getattr(region, "width", 320) / 8))
    for index, line in enumerate(textwrap.wrap(text, width=width) or [""]):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def _blend_issues(src, mod, ctx, detailed=True):
    """Cheap status for redraws; shader-tree inspection only on explicit diagnosis."""
    ids = input_ids(mod.node_group)
    values = modifier_values(mod)
    tgt = values.get(ids.get("Target", ""))
    if not tgt:
        return [("ERROR", "Choose a target mesh")]
    if tgt is src or tgt.type != "MESH":
        return [("ERROR", "Target must be a different mesh object")]
    issues = []
    ground_on = values.get(ids.get("Displace Ground", ""), True)
    if not mod.show_viewport:
        issues.append(("INFO", "Blend is paused in the viewport"))
    if not mod.show_render:
        issues.append(("WARNING", "Blend is disabled in the render"))
    if ground_on and (not tgt.hide_render or not tgt.hide_get(view_layer=ctx.view_layer)):
        issues.append(("WARNING", "Target is still visible; use Update Blend"))
    if not ground_on and values.get(HIDDEN_TARGET_KEY):
        issues.append(("INFO", "Update Blend to restore target visibility"))
    if ground_on and values.get(ids.get("Ground Conform", ""), 1.0) < 0.01:
        issues.append(("WARNING", "Ground Conform is zero; the ground cannot move"))
    levels = values.get(ids.get("Contact Subdivisions", ""), 2)
    if ground_on and levels >= 4:
        issues.append(("WARNING", f"High subdivision: up to {4 ** levels:,} faces per contact face"))
    matching = values.get(ids.get("Match Target Textures", ""), True)
    uv_name = values.get(ids.get("Target UV Map", ""), "")
    if matching and uv_name and uv_name not in tgt.data.uv_layers:
        issues.append(("WARNING", "Target UV map is missing; choose an available layer"))
    if (values.get("cb_uv_initialized") and
            (values.get("cb_uv_target") != tgt
             or values.get("cb_applied_uv", uv_name) != uv_name
             or values.get("cb_applied_texture_match", matching) != matching)):
        issues.append(("INFO", "Update Blend to apply target or texture changes"))
    mat = src.active_material
    if mat is None:
        issues.append(("WARNING", "Add a source material to blend shaders"))
    if tgt.active_material is None:
        issues.append(("WARNING", "Add a target material to blend shaders"))
    if mat:
        issues.extend(("WARNING", text) for text in mat.get("cb_texture_warnings", "").splitlines())
    if detailed:
        if not _surface(mat)[1]:
            issues.append(("WARNING", "Source material needs a connected Surface shader"))
        if not _surface(tgt.active_material)[1]:
            issues.append(("WARNING", "Target material needs a connected Surface shader"))
        for material in target_displaces(mod):
            issues.append(("WARNING", f"Target material '{material.name}' displaces during render"))
        if ground_on and len(tgt.data.polygons) < 16:
            issues.append(("INFO", "Coarse target mesh: add vertices near the contact if needed"))
    return issues


class CONTACTBLEND_OT_quality(bpy.types.Operator):
    """Set interactive subdivision quality while keeping a separate render level"""
    bl_idname = "object.contact_blend_quality"
    bl_label = "Set Blend Quality"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(items=(
        ("DRAFT", "Draft", "No contact subdivisions while editing; render at level 2"),
        ("BALANCED", "Balanced", "Level 1 while editing; render at level 2"),
        ("FINAL", "Final", "Level 2 while editing; render at level 3")))

    @classmethod
    def poll(cls, ctx):
        if _editing_problem(ctx):
            return False
        mod = find_mod(ctx.active_object)
        return mod is not None and "Viewport Subdivisions" in input_ids(mod.node_group)

    def execute(self, ctx):
        mod = find_mod(ctx.active_object)
        ids, values = input_ids(mod.node_group), modifier_values(mod)
        viewport, render = {"DRAFT": (0, 2), "BALANCED": (1, 2), "FINAL": (2, 3)}[self.preset]
        values[ids["Use Viewport Quality"]] = True
        values[ids["Viewport Subdivisions"]] = viewport
        values[ids["Contact Subdivisions"]] = render
        ctx.active_object.update_tag()
        return {"FINISHED"}


class CONTACTBLEND_OT_diagnostics(bpy.types.Operator):
    """Check blend settings without evaluating or rebuilding the mesh"""
    bl_idname = "object.contact_blend_diagnostics"
    bl_label = "Check Blend"

    @classmethod
    def poll(cls, ctx):
        return find_mod(ctx.active_object) is not None

    def execute(self, ctx):
        src, mod, tgt, ids, values = _ui_context(ctx)
        issues = _blend_issues(src, mod, ctx)
        lines = ["Contact Blend Status", f"Object: {src.name}",
                 f"Target: {tgt.name if tgt else 'Not set'}"]
        lines.extend(f"{severity}: {message}" for severity, message in issues)
        if not issues:
            lines.append("OK: No settings hazards detected")
        text = bpy.data.texts.get("Contact Blend Status") or bpy.data.texts.new("Contact Blend Status")
        text.clear()
        text.write("\n".join(lines))
        self.report({"INFO"}, f"{len(issues)} notice(s). Read 'Contact Blend Status' in the Text Editor")
        return {"FINISHED"}


class CONTACTBLEND_OT_add(bpy.types.Operator):
    """Blend the active object into the other selected object without booleans"""
    bl_idname = "object.contact_blend"
    bl_label = "Contact Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        problem = _editing_problem(ctx)
        if problem:
            cls.poll_message_set(problem)
            return False
        src = ctx.active_object
        others = [o for o in ctx.selected_objects if o is not src and o.type == "MESH"]
        tgt = others[0] if len(others) == 1 else mod_target(find_mod(src))
        return len(others) <= 1 and tgt is not None and tgt.type == "MESH" and tgt is not src

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        others = [o for o in ctx.selected_objects if o is not src and o.type == "MESH"]
        if len(others) > 1 or (not others and not mod_target(mod)):
            self.report({"ERROR"}, "Select the target mesh, then shift-select the object to blend (active)")
            return {"CANCELLED"}
        tgt = others[0] if others else mod_target(mod)
        if not tgt or tgt is src or tgt.type != "MESH":
            self.report({"ERROR"}, "Target must be a different mesh object")
            return {"CANCELLED"}
        ng = build_group()
        if not mod:
            mod = src.modifiers.new(GROUP, "NODES")
            mod.node_group = ng
            modifier_values(mod)[input_ids(ng)["Target UV Map"]] = _target_uv_name(tgt)
        modifier_values(mod)[input_ids(mod.node_group)["Target"]] = tgt
        src.update_tag()
        # The conformed ground is joined in as a copy, because a modifier on the target that
        # reads the source would be a depsgraph cycle. So the original has to stop drawing:
        # left visible it stays flat, cuts straight through the crater, and that intersection
        # is exactly the hard ring the blend is supposed to remove. Object Info still reads
        # hidden objects, so the copy keeps working.
        _sync_target_visibility(mod, ctx)
        hid = f"; hid '{tgt.name}'" if modifier_values(mod)[input_ids(mod.node_group)["Displace Ground"]] else ""
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
        if _editing_problem(ctx):
            return False
        tgt = mod_target(find_mod(ctx.active_object))
        return tgt is not None and tgt.type == "MESH" and tgt is not ctx.active_object

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        tgt = mod_target(mod)
        if not tgt or tgt is src or tgt.type != "MESH":
            self.report({"ERROR"}, "Target must be a different mesh object")
            return {"CANCELLED"}
        build_group()
        _sync_target_visibility(mod, ctx)
        src.update_tag()
        err = setup_material(src, tgt, refresh=True)
        self.report({"WARNING"} if err else {"INFO"},
                    f"Updated '{src.name}'" + (f" (materials unchanged: {err})" if err else ""))
        return {"FINISHED"}


class CONTACTBLEND_OT_remove(bpy.types.Operator):
    """Remove the blend, preserving material edits and restoring recorded target visibility"""
    bl_idname = "object.contact_blend_remove"
    bl_label = "Remove Blend"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return not _editing_problem(ctx) and find_mod(ctx.active_object) is not None

    def invoke(self, ctx, event):
        return ctx.window_manager.invoke_confirm(self, event)

    def execute(self, ctx):
        src = ctx.active_object
        mod = find_mod(src)
        targets = [mod_target(mod), modifier_values(mod).get(HIDDEN_TARGET_KEY)]
        state_key = str(mod.persistent_uid)
        _release_modifier_inputs(mod)
        src.modifiers.remove(mod)
        states = src.get(MODIFIER_STATE_KEY)
        if states is not None:
            if state_key in states:
                del states[state_key]
            if not states:
                del src[MODIFIER_STATE_KEY]
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
    """Refresh the target's shader in an isolated copy of the active source material"""
    bl_idname = "object.contact_blend_material"
    bl_label = "Blend Materials"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return CONTACTBLEND_OT_update.poll(ctx)

    def execute(self, ctx):
        src = ctx.active_object
        tgt = mod_target(find_mod(src))
        if not tgt or tgt is src or tgt.type != "MESH":
            self.report({"ERROR"}, "Choose a different target mesh first")
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
        return not _editing_problem(ctx) and target_leaks(find_mod(ctx.active_object)) is not None

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
        if _editing_problem(ctx):
            return False
        tgt = mod_target(find_mod(ctx.active_object))
        # Poll runs during UI redraw. Inspect nodes only after the user clicks.
        return tgt is not None and tgt.type == "MESH" and any(
            mat and getattr(mat, "displacement_method", "BUMP") != "BUMP"
            for mat in tgt.data.materials)

    def execute(self, ctx):
        mats = target_displaces(find_mod(ctx.active_object))
        if not mats:
            self.report({"INFO"}, "No linked material displacement found on the target")
            return {"CANCELLED"}
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
        col.use_property_split = True
        col.use_property_decorate = False
        problem = _editing_problem(ctx)
        if problem:
            _ui_message(col, problem, ctx)
            return
        src, mod, tgt, ids, values = _ui_context(ctx)
        if not mod:
            col.label(text=f"Object: {src.name}", icon="OBJECT_DATA")
            others = [o for o in ctx.selected_objects if o is not src and o.type == "MESH"]
            if len(others) == 1:
                col.label(text=f"Target: {others[0].name}", icon="MESH_DATA")
            else:
                col.label(text="Select the ground, then the hero mesh")
                col.label(text="The active mesh becomes the hero")
            col.operator(CONTACTBLEND_OT_add.bl_idname, text="Create Contact Blend", icon="MOD_SIMPLEDEFORM")
            return
        if not {"Target", "Contact Distance", "Displace Ground", "Ground Conform", "Surface Offset"} <= ids.keys():
            _ui_message(col, "Blend data is incomplete. Remove this blend and create it again", ctx, "ERROR")
            col.operator(CONTACTBLEND_OT_remove.bl_idname, icon="TRASH")
            return
        col.label(text=src.name, icon="OBJECT_DATA")
        _draw_modifier_input(col, mod, ids["Target"], "Target")
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator(CONTACTBLEND_OT_update.bl_idname, icon="FILE_REFRESH")
        row.prop(mod, "show_viewport", text="", icon="HIDE_OFF", toggle=True)
        _draw_modifier_input(col, mod, ids["Contact Distance"], "Contact Distance")
        _draw_modifier_input(col, mod, ids["Displace Ground"], "Deform Ground")
        ground = col.column()
        ground.enabled = values[ids["Displace Ground"]]
        _draw_modifier_input(ground, mod, ids["Ground Conform"], "Conform Strength")
        _draw_modifier_input(ground, mod, ids["Surface Offset"], "Surface Inset")
        issues = _blend_issues(src, mod, ctx, detailed=False)
        for severity, message in issues:
            notice = col.box()
            notice.alert = severity == "ERROR"
            _ui_message(notice, message, ctx, "ERROR" if severity != "INFO" else "INFO")
        if not issues:
            col.label(text="Geometry updates live", icon="CHECKMARK")
        col.separator()
        col.operator(CONTACTBLEND_OT_remove.bl_idname, icon="TRASH")


class _BlendSubpanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Contact"
    bl_parent_id = "CONTACTBLEND_PT_panel"

    @classmethod
    def poll(cls, ctx):
        if _editing_problem(ctx):
            return False
        mod = find_mod(ctx.active_object)
        required = {"Target", "Contact Distance", "Displace Ground", "Ground Conform", "Surface Offset"}
        return mod is not None and (required | set(getattr(cls, "required_inputs", ()))) <= input_ids(mod.node_group).keys()


class CONTACTBLEND_PT_quality(_BlendSubpanel, bpy.types.Panel):
    bl_label = "Quality & Performance"
    required_inputs = {"Contact Subdivisions"}

    def draw(self, ctx):
        _, mod, _, ids, values = _ui_context(ctx)
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.enabled = values[ids["Displace Ground"]]
        if "Viewport Subdivisions" not in ids:
            col.label(text="Update Blend to enable viewport quality", icon="INFO")
            return
        row = col.row(align=True)
        for label, preset in (("Draft", "DRAFT"), ("Balanced", "BALANCED"), ("Final", "FINAL")):
            levels = {"DRAFT": (0, 2), "BALANCED": (1, 2), "FINAL": (2, 3)}[preset]
            selected = (values[ids["Use Viewport Quality"]]
                        and (values[ids["Viewport Subdivisions"]], values[ids["Contact Subdivisions"]]) == levels)
            row.operator(CONTACTBLEND_OT_quality.bl_idname, text=label, depress=selected).preset = preset
        _draw_modifier_input(col, mod, ids["Use Viewport Quality"], "Separate Viewport Quality")
        viewport = col.column()
        viewport.enabled = values[ids["Use Viewport Quality"]]
        _draw_modifier_input(viewport, mod, ids["Viewport Subdivisions"], "Viewport Subdivisions")
        _draw_modifier_input(col, mod, ids["Contact Subdivisions"], "Render / Full Subdivisions")
        _ui_message(col, "Draft speeds up editing; render quality stays separate", ctx)


class CONTACTBLEND_PT_shading(_BlendSubpanel, bpy.types.Panel):
    bl_label = "Shading & Textures"
    bl_options = {"DEFAULT_CLOSED"}
    required_inputs = {"Normal Blend Distance", "Material Blend Distance", "Match Target Textures", "Target UV Map"}

    def draw(self, ctx):
        _, mod, tgt, ids, values = _ui_context(ctx)
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        _draw_modifier_input(col, mod, ids["Normal Blend Distance"], "Normal Blend Distance")
        _draw_modifier_input(col, mod, ids["Material Blend Distance"], "Material Blend Distance")
        _draw_modifier_input(col, mod, ids["Match Target Textures"], "Match Target Textures")
        uv = col.column()
        uv.enabled = values[ids["Match Target Textures"]]
        _draw_modifier_input(uv, mod, ids["Target UV Map"], "Target UV Map",
                             search_data=tgt.data if tgt and tgt.type == "MESH" else None,
                             search_property="uv_layers")
        col.operator(CONTACTBLEND_OT_material.bl_idname, text="Refresh Material", icon="NODE_MATERIAL")
        _ui_message(col, "Refresh after changing texture settings", ctx)


class CONTACTBLEND_PT_detail(_BlendSubpanel, bpy.types.Panel):
    bl_label = "Ground Detail"
    bl_options = {"DEFAULT_CLOSED"}
    required_inputs = {"Ground Detail", "Detail Image", "Detail UV Map", "Detail Midlevel"}

    def draw(self, ctx):
        _, mod, tgt, ids, values = _ui_context(ctx)
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.enabled = values[ids["Displace Ground"]]
        for name, label in (("Ground Detail", "Height"), ("Detail Image", "Height Map")):
            _draw_modifier_input(col, mod, ids[name], label)
        _draw_modifier_input(col, mod, ids["Detail UV Map"], "UV Map",
                             search_data=tgt.data if tgt and tgt.type == "MESH" else None,
                             search_property="uv_layers")
        _draw_modifier_input(col, mod, ids["Detail Midlevel"], "Midlevel")


class CONTACTBLEND_PT_diagnostics(_BlendSubpanel, bpy.types.Panel):
    bl_label = "Checks & Fixes"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, ctx):
        col = self.layout.column()
        col.operator(CONTACTBLEND_OT_diagnostics.bl_idname, icon="VIEWZOOM")
        col.operator(CONTACTBLEND_OT_hide_target.bl_idname, icon="RESTRICT_RENDER_ON")
        col.operator(CONTACTBLEND_OT_bump_only.bl_idname, icon="MOD_SMOOTH")
        _ui_message(col, "Full geometry report: run contact_blend_diag.py", ctx)


_classes = (CONTACTBLEND_OT_add, CONTACTBLEND_OT_update, CONTACTBLEND_OT_remove,
            CONTACTBLEND_OT_material, CONTACTBLEND_OT_hide_target,
            CONTACTBLEND_OT_bump_only, CONTACTBLEND_OT_quality, CONTACTBLEND_OT_diagnostics,
            CONTACTBLEND_PT_panel, CONTACTBLEND_PT_quality, CONTACTBLEND_PT_shading,
            CONTACTBLEND_PT_detail, CONTACTBLEND_PT_diagnostics)


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
    modifier_values(mod)[ids["Surface Offset"]] = 0.01

    def ev_mesh():
        rock.update_tag()
        return rock.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()

    modifier_values(mod)[ids["Displace Ground"]] = False
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

    modifier_values(mod)[ids["Displace Ground"]] = True
    gbase = [v.co.copy() for v in ground.data.vertices]
    # Object Info is RELATIVE, so the copy arrives in the rock's local space, where the flat
    # plane sits at -location.z. Subdivision breaks any 1:1 vertex correspondence with the
    # original grid, so tell the two halves of the join apart by material instead.
    floor = -rock.location.z

    def ground_polys(me):
        ground_material = modifier_values(mod)[ids["Ground Material"]]
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

    modifier_values(mod)[ids["Contact Subdivisions"]] = 0
    me = ev_mesh()
    assert len(me.vertices) == len(base) + len(gbase), len(me.vertices)
    coarse = len(ground_co(me))

    modifier_values(mod)[ids["Contact Subdivisions"]] = 2
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
    modifier_values(mod)[ids["Displace Ground"]] = False              # no copy joined, target must stay visible
    ground.hide_render = False
    assert target_leaks(mod) is None, "warned about a visible target with no ground copy"
    modifier_values(mod)[ids["Displace Ground"]] = True

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
    modifier_values(mod)[ids["Contact Distance"]] = 0.1234
    ng[VERSION_KEY] = 0                              # pretend the file predates this build
    n_nodes = len(ng.nodes)
    ng2 = build_group()
    assert ng2 is ng, "rebuilt into a different datablock; modifiers would be orphaned"
    assert group_version(ng) == GROUP_VERSION, "version stamp not updated"
    assert len(ng.nodes) == n_nodes, (len(ng.nodes), n_nodes)
    ids = input_ids(ng)
    assert abs(modifier_values(mod)[ids["Contact Distance"]] - 0.1234) < 1e-6, "settings lost across rebuild"
    assert modifier_values(mod)[ids["Target"]] == ground, "target lost across rebuild"
    assert build_group() is ng and len(ng.nodes) == n_nodes, "rebuilt again when up to date"
    modifier_values(mod)[ids["Contact Distance"]] = 0.3

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
    modifier_values(mod)[ids["Detail Image"]] = img
    modifier_values(mod)[ids["Ground Detail"]] = 0.25
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
    modifier_values(mod)[ids["Ground Detail"]] = 0.0

    unregister()
    print(f"OK  hero_moved={moved:.6f}  ground dip={dip:.4f} far={far:.6f} "
          f"subdiv={coarse}->{len(gco)}  detail={detail_max:.4f}  "
          f"blend={min(vals):.3f}..{max(vals):.3f}  bent={bent}/{len(base_n)}  "
          f"joined={len(me.vertices)}")


if __name__ == "__main__":
    _selftest()

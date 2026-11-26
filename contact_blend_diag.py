# SPDX-License-Identifier: Apache-2.0

"""Report every viewport-vs-render difference that can break Contact Blend.

Open in Blender's Text Editor with your real scene loaded, select the blended object (or its
ground), and press Run Script. The report lands in the "Contact Blend Report" text.

With the add-on enabled this runs the add-on's own checks -- the same ones as the Diagnose
button in the sidebar, which know about the shared ground. Without it (an old file opened on a
machine that does not have the add-on, say) it falls back to the copy below, which covers the
joined-ground checks and only reports whether a shared ground exists.
"""

import bpy
import collections
import sys

GROUP = "Contact Blend"
ATTR = "blend"
LEGACY_NAMES = ("Blend To Surface",)   # pre-1.1; the add-on renames these on load
NAMES = (GROUP,) + LEGACY_NAMES
VERSION_KEYS = ("cb_version", "bts_version")
GROUND_VERSION_KEY = "cb_ground_version"
PROXY_KEY = "cb_ground_proxy"


def addon():
    """The loaded Contact Blend add-on module, whatever name it was installed under."""
    for module in list(sys.modules.values()):
        info = getattr(module, "bl_info", None)
        if isinstance(info, dict) and info.get("name") == "Contact Blend" and hasattr(module, "diagnose"):
            return module
    return None


def group_version(ng):
    return next((ng[k] for k in VERSION_KEYS if k in ng), None)


def input_names(ng):
    return {i.name for i in ng.interface.items_tree
            if i.item_type == "SOCKET" and i.in_out == "INPUT"}


def is_ours(ng):
    # Mirrors the add-on: by stamp (or, pre-stamp, by socket layout), never by name -- an
    # appended object's group arrives as "Contact Blend.001".
    return ng.bl_idname == "GeometryNodeTree" and (
        group_version(ng) is not None or "Contact Distance" in input_names(ng))


def value(mod, ident, default=None):
    """A modifier input by socket identifier: an ID property up to 5.1, RNA from 5.2."""
    inputs = getattr(getattr(mod, "properties", None), "inputs", None)
    if inputs is not None:
        item = getattr(inputs, ident or "", None)
        result = item.value if item is not None else None
    else:
        result = mod.get(ident) if ident else None
    return default if result is None else result


def find_mod(obj):
    return obj and next((m for m in obj.modifiers if m.type == "NODES"
                         and m.node_group and is_ours(m.node_group)), None)


def node_tree(mat):
    """Every material is a node material in 5.x; Material.use_nodes is deprecated."""
    return mat.node_tree if mat is not None else None


def is_hidden(obj):
    """hide_get() raises for an object outside the view layer on some versions."""
    layer = bpy.context.view_layer
    if layer.objects.get(obj.name) != obj:
        return True
    try:
        return obj.hide_get()
    except RuntimeError:
        return True


def report(obj):
    out = []
    add = out.append
    mod = find_mod(obj)
    if not mod:
        return [f"FAIL  '{obj.name}' has no {GROUP} modifier"]

    ids = {i.name: i.identifier for i in mod.node_group.interface.items_tree
           if i.item_type == "SOCKET" and i.in_out == "INPUT"}
    tgt = value(mod, ids["Target"])
    add(f"object   : {obj.name}")
    add(f"target   : {tgt.name if tgt else 'NONE  <-- modifier has no target'}")
    add(f"engine   : {bpy.context.scene.render.engine}")
    add(f"group    : {mod.node_group.name}"
        + ("  <-- pre-1.1 name; reload the file to migrate it"
           if mod.node_group.name in LEGACY_NAMES else ""))
    add(f"group ver: {group_version(mod.node_group)}  nodes={len(mod.node_group.nodes)}"
        f"  sockets={len(ids)}")
    if "Shared Ground" in ids and value(mod, ids["Shared Ground"]):
        proxies = [o for o in bpy.data.objects if o.get(PROXY_KEY) and any(
            m.type == "NODES" and m.node_group and GROUND_VERSION_KEY in m.node_group
            and value(m, next((i.identifier for i in m.node_group.interface.items_tree
                            if i.item_type == "SOCKET" and i.name == "Target"), "")) == tgt
            for m in o.modifiers)]
        add("mode     : shared ground; " + (f"drawn by '{proxies[0].name}'" if proxies
                                            else "MISSING  <-- press Update in the sidebar"))
        add("  (enable the add-on for the full shared-ground checks)")
    if group_version(mod.node_group) is None:
        add("  ^-- unstamped: this group predates auto-upgrade. Reload the addon;"
            " build_group() will rebuild it in place and keep your settings.")

    # 1. per-modifier render toggles. show_render off is the single most common cause of
    #    "perfect in the viewport, missing at F12", and it is one click away in the header.
    add("")
    add("modifier stack (viewport / render):")
    for m in obj.modifiers:
        flag = "OK " if m.show_render else "OFF"
        add(f"  {flag} {m.name:<22} type={m.type:<12} viewport={m.show_viewport} render={m.show_render}")
        if not m.show_render:
            add(f"       ^-- '{m.name}' is DISABLED IN RENDER (camera icon in its header)")
        if m.type == "SUBSURF" and m.levels != m.render_levels:
            add(f"       ^-- subdivision differs: viewport={m.levels} render={m.render_levels};"
                f" the blend is computed on a different mesh at F12")

    # 2. target visibility. Eye and camera are separate flags; eye-only looks right in the
    #    viewport while the render still draws the original surface through the crater.
    add("")
    if tgt:
        ground_on = value(mod, ids["Displace Ground"])
        add(f"target visibility: eye_hidden={is_hidden(tgt)} hide_render={tgt.hide_render} "
            f"hide_viewport={tgt.hide_viewport}")
        add(f"Displace Ground  : {ground_on}")
        if ground_on and not tgt.hide_render:
            add("  ^-- target STILL RENDERS while a conformed copy is joined in."
                " The flat original cuts through the crater at F12 only.")
        if ground_on and not is_hidden(tgt):
            add("  ^-- target still draws in the viewport too.")
        if tgt.hide_viewport:
            add("  ^-- hide_viewport (monitor icon) can drop the object from the depsgraph;"
                " prefer the eye + camera icons.")
        for coll in tgt.users_collection:
            lc = bpy.context.view_layer.layer_collection.children.get(coll.name)
            if lc and (lc.exclude or lc.holdout or lc.indirect_only):
                add(f"  ^-- collection '{coll.name}': exclude={lc.exclude} holdout={lc.holdout}"
                    f" indirect_only={lc.indirect_only}")

    # 3. material displacement is applied by the renderer AFTER the modifier stack, so the
    #    node group conforms the ground to a surface the renderer then moves somewhere else.
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
            add(f"      Compare its Scale against Contact Distance"
                f" ({value(mod, ids['Contact Distance']):.4f}); if it is larger, the blend is swamped.")
            add("      Fix: rebuild the height as Subdivision + Displace modifiers on the")
            add("      target, above Contact Blend, and set this material to Bump Only.")

    # 4. the blend attribute has to survive onto the evaluated mesh, and the material has to
    #    actually read it back.
    add("")
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).to_mesh()
    if ATTR in me.attributes:
        vals = [d.value for d in me.attributes[ATTR].data]
        a = me.attributes[ATTR]
        add(f"attribute '{ATTR}': present  domain={a.domain} range={min(vals):.3f}..{max(vals):.3f}")
        if max(vals) < 0.5:
            add(f"  ^-- never gets near 1. Material Blend Distance"
                f" ({value(mod, ids['Material Blend Distance']):.4f}) is too small for this object.")
    else:
        add(f"attribute '{ATTR}': MISSING from the evaluated mesh")
    add(f"evaluated mesh   : {len(me.vertices)} verts, {len(me.materials)} material slots "
        f"({', '.join(m.name if m else '-' for m in me.materials)})")
    if "Match Target Textures" in ids:
        matching = value(mod, ids["Match Target Textures"], True)
        add(f"texture matching : {matching}  target UV={value(mod, ids['Target UV Map'], '')!r}")
        if matching:
            add("projected UVs    : " + ("present" if "cb_target_uv" in me.uv_layers else "MISSING"))

    mat = obj.active_material
    if node_tree(mat) is None:
        add("material         : none, or not using nodes")
    else:
        for warning in mat.get("cb_texture_warnings", "").splitlines():
            add(f"  ^-- {warning}")
        mix = next((n for n in (mat.node_tree.nodes.get(x) for x in NAMES) if n), None)
        add(f"material         : {mat.name}  mix node={'present' if mix else 'MISSING'}")
        if not mix:
            add("  ^-- press Blend Materials; nothing is reading the attribute")
        else:
            readers = [n for n in mat.node_tree.nodes
                       if n.type == "ATTRIBUTE" and n.attribute_name == ATTR]
            add(f"  attribute nodes reading '{ATTR}': {len(readers)}"
                + "".join(f" [{n.name} type={n.attribute_type}]" for n in readers))
            for n in readers:
                if n.attribute_type != "GEOMETRY":
                    add(f"  ^-- {n.name}.attribute_type is {n.attribute_type},"
                        " must be GEOMETRY to read a mesh attribute")

    # 5. cracks. Subdividing only the contact island leaves T-junctions along the cut; they are
    #    harmless while every vertex sitting on them is frozen, and tear open into slivers of
    #    background the moment one of them moves.
    add("")
    gmats = {m.name for m in (tgt.data.materials if tgt and tgt.data else []) if m}
    mapped_ground = value(mod, ids["Ground Material"]) if "Ground Material" in ids else None
    if mapped_ground:
        gmats.add(mapped_ground.name)
    gi = {i for i, m in enumerate(me.materials) if m and m.name in gmats}
    gpolys = [p for p in me.polygons if p.material_index in gi]
    if not gpolys:
        add("crack check: could not identify the ground copy by material; skipped")
    else:
        seen = collections.Counter()
        for p in gpolys:
            vs = list(p.vertices)
            for a, b in zip(vs, vs[1:] + vs[:1]):
                seen[(min(a, b), max(a, b))] += 1
        open_edges = [e for e, c in seen.items() if c == 1]
        # the target's own outline is legitimately open; measure against its real bounds
        bb = [tgt.matrix_world @ __import__("mathutils").Vector(c) for c in tgt.bound_box]
        lo = min(v.x for v in bb), min(v.y for v in bb)
        hi = max(v.x for v in bb), max(v.y for v in bb)
        pad = 1e-3

        def on_rim(i):
            c = obj.matrix_world @ me.vertices[i].co
            return (abs(c.x - lo[0]) < pad or abs(c.x - hi[0]) < pad
                    or abs(c.y - lo[1]) < pad or abs(c.y - hi[1]) < pad)

        inner = {i for e in open_edges for i in e if not on_rim(i)}
        add(f"crack check: {len(gpolys)} ground faces, {len(open_edges)} open edges, "
            f"{len(inner)} T-junction verts away from the target outline")
        if inner:
            zs = [me.vertices[i].co.z for i in inner]
            add(f"  T-junction vert z range: {min(zs):.6f} .. {max(zs):.6f}"
                f"  (spread {max(zs)-min(zs):.6f})")
            add("  If that spread is not ~0 on a flat target, the cut is inside the moving")
            add("  zone and IS tearing. On a displaced target compare it to the displacement.")

    # 6. scene-level simplify silently lowers render or viewport detail
    s = bpy.context.scene.render
    if s.use_simplify:
        add("")
        add(f"SIMPLIFY IS ON: max_subdiv viewport={s.simplify_subdivision} "
            f"render={s.simplify_subdivision_render}")
    return out


TEXT = "Contact Blend Report"

obj = bpy.context.active_object
_addon = addon()
if _addon is not None:
    lines = _addon.diagnose(obj)
else:
    lines = report(obj) if obj else ["FAIL  no active object"]
body = "\n".join(["Contact Blend diagnostic", "=" * 70] + lines)

# print() lands in the system console, which is hidden by default on Windows. Write the
# report into a Text datablock as well so it is readable without hunting for a console.
txt = bpy.data.texts.get(TEXT) or bpy.data.texts.new(TEXT)
txt.clear()
txt.write(body)
print("\n" + body)

screen = getattr(bpy.context, "screen", None)
shown = False
for area in (screen.areas if screen else []):
    if area.type == "TEXT_EDITOR":
        area.spaces.active.text = txt
        area.spaces.active.top = 0
        shown = True
        break

if not shown and not bpy.app.background:
    def _draw(self, ctx):
        self.layout.label(text=f"Written to Text datablock '{TEXT}'")
        self.layout.label(text="Open it in the Text Editor (the file dropdown).")
    bpy.context.window_manager.popup_menu(_draw, title="Contact Blend", icon="INFO")

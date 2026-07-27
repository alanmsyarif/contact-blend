# Contact Blend

A Blender add-on that sinks one mesh into another without booleans no destructive cuts, no
retopology, fully non-destructive and animatable.

Instead of deforming your hero mesh, it conforms the **ground** to it. The ground craters under
the object and rides up its flanks as a meniscus, so there is no intersection curve left to read
as a seam. Shading and materials are cross-faded across the same contact band.

| | |
|---|---|
| **Blender** | 4.5 LTS, 5.0, 5.1 |
| **Location** | `View3D > Sidebar (N) > Contact`, and `Object > Contact Blend` |
| **Renderers** | EEVEE Next and Cycles |

## Install

1. Download `contact_blend.py`.
2. `Edit > Preferences > Add-ons > Install from Disk…`, pick the file, enable it.

The add-on builds a Geometry Nodes group named `Contact Blend` on first use and stamps it with a
version. Later releases rebuild that group **in place** on load, carrying your settings across by
socket name, so updating never means hunting down and deleting the old node group.

<details>
<summary><b>Upgrading from Blend To Surface (1.0)</b></summary>

Remove the old `blend_to_surface.py` from your add-ons first leaving both enabled registers two
panels and two menu entries over the same node group.

Existing `.blend` files need nothing: opening one renames its node group and material mix node in
place, and every modifier follows along with its settings intact.

Operator IDs changed (`object.blend_to_surface` → `object.contact_blend`, and the same for the
three buttons), so custom keymaps and scripts need updating. The `blend` attribute name is
unchanged, so hand-wired shaders reading it still work.
</details>

## Use

1. Select the **target** (the ground, floor, terrain).
2. Shift-select the **object** to sink into it, so it is active.
3. `Object > Contact Blend`, or the button in the sidebar.

The object gets a Geometry Nodes modifier, the target's shader is mixed into the object's
material, and the target is hidden from the viewport and the render.

**The target needs vertices in the contact area.** Conforming is vertex-driven: a 4-vertex plane
cannot crater. Either subdivide it or raise `Contact Subdivisions`.

## Settings

| Setting | Default | What it does |
|---|---|---|
| **Target** | — | The surface to sink into. |
| **Contact Distance** | 0.3 m | Radius of the whole effect; everything else fades to nothing here. Start around ⅓ of the object's width. |
| **Surface Offset** | 0.002 m | How far *inside* the object the conformed ground lands, so it does not z-fight along the contact band. Keep it small and positive. |
| **Displace Ground** | on | Conform the ground at all. Off means shading and material blending only, and the target stays visible. |
| **Ground Conform** | 1.0 | Master multiplier on the conform. **At 0 the ground cannot move and there is no blend** the panel warns about this. |
| **Contact Subdivisions** | 2 | Subdivides only the contact island. Each level is 4× the faces, so 4 is 256× per face; 2 is usually plenty. |
| **Ground Detail** | 0.0 m | Height of the contact-zone displacement. |
| **Detail Image** | | Height map for that displacement. |
| **Detail UV Map** | `UVMap` | Which UV map to sample it through. |
| **Detail Midlevel** | 0.0 | Value that means "no displacement". Set **0.5** for a real height map; the 0 default makes an unset image a no-op. |
| **Normal Blend Distance** | 0.2 m | Over what distance the object's shading normals rotate to match the ground's. |
| **Material Blend Distance** | 0.3 m | Over what distance the object's material fades into the ground's. |

**Buttons.** *Blend Materials* re-runs the shader mixing (idempotent). *Hide Target From Render*
and *Set Target To Bump Only* appear only when those hazards are actually present.

## How it works

- **The ground moves, not the object.** A seam is the curve where two surfaces cross, so the only
  way to remove it is to stop them crossing. Each ground vertex slides to its closest point on the
  object, weighted 1 at contact and 0 at `Contact Distance`. The hero mesh is never touched.
- **Shading is welded.** Near the contact the object adopts the ground's normals over
  `Normal Blend Distance`, written on the corner domain so sharp edges elsewhere survive.
- **Materials cross-fade.** A `blend` attribute is stored per point, 1 at contact and 0 away. The
  target's shader tree is copied into the object's material and mixed against it by that attribute.
- **Detail goes where the vertices are.** `Ground Detail` displaces through a height map, masked by
  the same falloff so it stays strictly inside the subdivided island.
- **The target gets hidden** because the conformed ground is joined into the object as a copy — a
  modifier on the target reading the object would be a dependency cycle. Left visible the original
  stays flat and cuts back through the crater. Object Info still reads hidden objects, so the copy
  keeps working.

The source comments carry the full reasoning, including why the subdivision cut cannot crack and
why each ordering is the way it is.

## Known limits

- **Material displacement on the target is invisible to this add-on.** Renderers apply it after the
  whole modifier stack, so the ground conforms to a surface that is then moved somewhere else, and
  the viewport keeps looking correct. Rebuild the height as `Subdivision` + `Displace` modifiers on
  the target above this one, or use `Ground Detail`.
- **Meniscus smoothness is capped by ground resolution.** `Contact Subdivisions` helps; the crater's
  silhouette against the object stays as fine as the mesh allows.
- **Flat slabs don't chain.** Blending a patch into a floor it lies parallel to does nothing useful
  every vertex is equidistant, so a proximity falloff has nothing to grip.
- Blending several objects into one target joins a copy of that target into each of them.

## Troubleshooting

Run `contact_blend_diag.py` from Blender's Text Editor with the blended object selected. It writes
a report to a Text datablock named **`Contact Blend Report`**, and checks the things that break
only at render time or only silently: per-modifier render toggles, mismatched subdivision levels,
target visibility and collection exclusion, render-time displacement on the target, whether the
`blend` attribute reaches the evaluated mesh and the material reads it, whether the subdivision cut
is tearing, and the node group's version stamp.

## Development

The add-on is self-testing it builds a scene, evaluates the modifier and asserts on the result:

```bash
blender --background --python contact_blend.py
```

```
OK  hero_moved=0.000000  ground dip=-0.0667 far=0.000000 subdiv=1681->6349  detail=0.2619
    blend=0.000..0.980  bent=576/1536  joined=6735
```

Coverage includes: the hero mesh is never deformed, flat shading survives away from the contact,
the ground conforms and stays untouched outside `Contact Distance`, the contact island is
subdivided and its cut cannot tear, ground shading stays uniform, contact-zone detail stays inside
the island, materials mix idempotently, render-visibility and displacement hazards are detected, a
stale node group upgrades in place without losing settings, and a file saved under the 1.0 name
migrates without orphaning its modifier.

## License

[Apache-2.0](LICENSE)


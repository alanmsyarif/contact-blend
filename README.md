# Contact Blend

A Blender add-on that sinks objects into a ground without booleans — no destructive cuts, no
retopology, fully non-destructive and animatable.

Instead of deforming your objects, it conforms the **ground** to them. The ground craters under
each object and rides up its flanks as a meniscus, so there is no intersection curve left to read
as a seam. Shading and materials are cross-faded across the same contact band. Any number of
objects can sit on one ground: they all crater the same surface.

| | |
|---|---|
| **Blender** | 4.5 LTS, 5.0, 5.1 |
| **Location** | `View3D > Sidebar (N) > Contact`, and `Object > Contact Blend` |
| **Renderers** | EEVEE Next and Cycles |

## Install

1. Download `contact_blend.py`.
2. `Edit > Preferences > Add-ons > Install from Disk…`, pick the file, enable it.

The add-on builds two Geometry Nodes groups on first use, `Contact Blend` (on each object) and
`Contact Blend Ground` (on the shared ground), and stamps both with a version. Later releases
rebuild them **in place** on load, carrying your settings across by socket name, so updating never
means hunting down and deleting old node groups.

<details>
<summary><b>Upgrading from 1.x</b></summary>

Existing blends keep their geometry and shading exactly: each object keeps drawing its own joined
copy of the ground. One thing changes on load: the material cross-fade is now measured from where
the object meets the conformed ground rather than the flat original, so the ground's material reaches
up to the visible junction. The sidebar shows **Use Shared Ground** on such an object; press it to move every blend
on that ground to one shared ground, so they stop cutting through each other's craters. Blending a
new object into a ground that has joined blends converts them too, and says so. Settings are kept;
undo reverts the conversion.
</details>

<details>
<summary><b>Upgrading from Blend To Surface (1.0)</b></summary>

Remove the old `blend_to_surface.py` from your add-ons first — leaving both enabled registers two
panels and two menu entries over the same node group.

Existing `.blend` files need nothing: opening one renames its node group and material mix node in
place, and every modifier follows along with its settings intact.

Operator IDs changed (`object.blend_to_surface` → `object.contact_blend`, and the same for the
three buttons), so custom keymaps and scripts need updating. The `blend` attribute name is
unchanged, so hand-wired shaders reading it still work.
</details>

## Use

1. Select the **ground** (the floor, terrain), then shift-select the **object** to sink into it,
   so it is active.
2. Press the big button in the sidebar's **Contact** tab, or `Object > Contact Blend`.

To blend **several objects at once**, pick the ground in the sidebar's **Ground** field, select the
objects, and press the button. Once a ground has a blend, you can also just select it (or rather the
shared ground that now stands in for it) together with new objects.

The button always says what it is about to do — *Blend 'Rock' into 'Ground'*, *Blend 5 objects into
'Ground'*, *Re-apply 'Rock' to 'Ground'* — and when the selection does not make sense it is greyed
out with the reason underneath.

Each object gets a Geometry Nodes modifier, the ground's shader is mixed into the object's
materials, the original ground is hidden from the viewport and the render, and a **shared ground**
object named `<Ground> · Contact Blend` takes its place.

### The shared ground

The shared ground is an empty mesh parented to the original ground. Its modifier draws the ground
conformed to every object blended into it, in one surface. It follows the ground when you move,
rotate, scale or animate it, and its Object texture coordinates match the original's.

- **Duplicating** a blended object (Shift+D) puts the copy on the same ground automatically.
- **Ground-level settings** (subdivisions, detail displacement) live on the shared ground and apply
  to every object on it. Select the shared ground to edit them and to see the list of objects, each
  with select and remove buttons.
- **Removing** the last blend on a ground deletes the shared ground and shows the original again.
- If the shared ground is deleted, the sidebar says so and **Update** recreates it; if it is
  hidden, the sidebar offers to show it again.

Each object's own settings — Contact Distance, Ground Conform, Surface Offset — still apply around
that object: a pebble and a boulder on the same ground each get their own contact band.

### Materials

Every material slot on the object holding a node material is blended, each in its own private,
object-linked copy, so other objects sharing the material or the mesh are untouched. The ground's
**active** material is used; when the ground has several, the sidebar says which. Slots without a
usable node material are skipped.

**Edge Breakup** (in the Shading section) roughens the cross-fade, which is otherwise a smooth
airbrushed band. *Breakup* sets the strength, *Sharpness* hardens the edge and *Noise Scale* sets the
size of the noise, which is in the object's own space. At the defaults (0) the blend is unchanged,
and at any setting the material stays fully the object's away from the contact and fully the
ground's at it — no speckles, no holes.

### Update, Fit and Remove

The sidebar notices when a blend no longer matches what was applied — Target, Match Target Textures
or Target UV Map changed, Displace Ground toggled from the modifier panel, the shared ground
missing — names the reason and highlights **Update**. Displace Ground toggled in the sidebar takes
effect immediately: the ground appears or disappears and the original is hidden or shown with it.

- **Update** refreshes the copied ground shader, visibility and the shared ground, keeping all
  settings and your edits to the object's materials. It acts on every selected blended object (or
  every object on a selected ground).
- **Fit Size** sets Contact Distance, the blend distances and Surface Offset from the object's
  width. New blends get this automatically; Update and re-applying never overwrite your values.
- **Remove** removes the modifier and reconnects the object's shader, keeping the isolated material
  and your edits. Generated nodes are removed unless you have wired them into other parts of the
  shader. Original ground visibility is restored after the last blend on it is removed; visibility
  you have changed by hand is preserved.
- Running **Contact Blend** again on a blended object reuses its modifier; selecting a different
  ground retargets it.

Visibility records are saved in the `.blend` file, including the scene/view layers in which the
add-on hid the ground. For blends created before 1.2, original visibility is unknown and is left
unchanged on removal unless it has since been recorded. Untagged legacy shader nodes are left
disconnected on removal because they may contain user edits.

### Coarse grounds

Conforming moves vertices, so the crater can only be as fine as the ground's mesh. A coarse ground —
even a single quad — craters once **Subdivisions** is raised: only the faces that can reach an
object are subdivided, and the cut around them is guaranteed not to move, so it cannot crack.

### Match the ground's textures

**Match Target Textures** is enabled by default. The copied ground shader uses the ground's
coordinates rather than the object's UVs, origin, or bounding box:

- **UV textures** sample the selected ground UV map from the conformed ground onto the object's
  face corners. The object's own UV maps are preserved. The ground's render UV map is selected
  initially; use **Target UV Map** to choose another.
- **Object coordinates** use the ground's transform. Explicit references to mapping objects, such as
  empties, are preserved.
- **Generated coordinates** use the ground's texture space on both sides of the contact. Press
  **Update** after changing the ground's mesh bounds or texture-space settings.

Unconnected image textures, common procedural textures, Texture Coordinate and UV Map nodes, and
tangent-space Normal Map UV references are handled. Shared shader groups are copied before their
internal coordinate nodes are adjusted. The shared ground gets one corrected copy of the ground's
active material; the original material is never edited.

Only one ground UV map is projected at a time. Additional explicit UV maps and instancer coordinates
are left unchanged and reported in the sidebar, as is a missing UV map. UV sampling is per corner:
UV seams or sharply curved contacts may need more object vertices. On a shared ground, corners
farther from the ground than every blend distance are projected from the edge of the object's
conformed patch; the ground shader is invisible there anyway. Disable texture matching and update the blend to keep the original coordinate
behavior for materials requiring a custom setup.

## Settings

Distances are in **world metres**, whatever the object's scale (non-uniform scale is approximated by
its average).

**Contact** — per object

| Setting | New blend | What it does |
|---|---|---|
| **Target** | — | The ground to sink into. |
| **Displace Ground** | on | Conform the ground at all. Off means shading and material blending only; the ground is drawn unchanged. |
| **Contact Distance** | ⅓ of the object's width | Radius of the effect around this object; the ground is untouched beyond it. |
| **Ground Conform** | 1.0 | How far the ground moves onto the object. **At 0 the ground cannot move and there is no blend** — the sidebar warns about this. |
| **Surface Offset** | 1.2% of Contact Distance | How far *inside* the object the ground lands, so it does not z-fight along the contact band (0.002 m for a 0.5 m object). |

**Shading** — per object

| Setting | New blend | What it does |
|---|---|---|
| **Normal Blend** | ⅔ of Contact Distance | Over what distance the object's shading normals rotate to match the ground's. |
| **Material Blend** | = Contact Distance | Over what distance, measured from where the object meets the ground, its material fades into the ground's. |
| **Match Target Textures** | on | Match UV, Object, and Generated coordinates in the copied ground shader. |
| **Target UV Map** | ground's render UV | UV map to project onto the object. |
| **Breakup / Sharpness / Noise Scale** | 0 / 0 / 5 | Edge breakup of the material cross-fade, stored on the material. |

**Ground** — shared by every object on the ground

| Setting | Default | What it does |
|---|---|---|
| **Subdivisions** | 2 | Subdivides only the faces that can reach an object. Each level is 4× the faces there. |
| **Detail Height** | 0.0 m | Height of the contact-zone displacement. |
| **Detail Image** | — | Height map for that displacement. |
| **Detail UV Map** | `UVMap` | Which ground UV map to sample it through. |
| **Detail Midlevel** | 0.0 | Value that means "no displacement". Set **0.5** for a real height map; the 0 default makes an unset image a no-op. |

Contact Distance, Surface Offset and the blend distances are fitted to the object's width when a
blend is created (⅓ of its smaller horizontal side, clamped to 1 mm–50 m), and **Fit Size** re-applies
that on demand.

**Hazards.** The sidebar warns, with a one-click fix where there is one, when the original ground
still renders, when the shared ground is hidden or missing, when Ground Conform is ~0, and when the
ground's material displaces at render time. **Diagnose** writes a full report to a text datablock.

## How it works

- **The ground moves, not the object.** A seam is the curve where two surfaces cross, so the only
  way to remove it is to stop them crossing. Each ground vertex slides to its closest point on the
  nearest object, weighted 1 at contact and 0 at that object's Contact Distance. The objects' meshes
  are never touched.
- **One ground for many objects.** A modifier on the ground that read the objects, which read the
  ground, would be a dependency cycle. The shared ground breaks it: the objects read the original
  ground, and only the shared ground reads the objects, through a helper collection that is not
  shown in the scene. Each object writes its own Contact Distance, Ground Conform and Surface Offset
  onto its geometry (`cb_params`), and the shared ground reads them from the nearest object.
- **Shading is welded.** Near the contact each object adopts the ground's normals over Normal Blend
  distance, written on the corner domain so sharp edges elsewhere survive. To have a ground to weld
  to, each object conforms the small patch of ground it can see, with the same settings the shared
  ground uses, and samples normals and UVs from it.
- **Materials cross-fade.** A `blend` attribute is stored per point: 1 where the object meets the
  ground *as drawn* — the conformed ground, whose meniscus has climbed the object's flank — and 0
  at Material Blend distance above it. (With Displace Ground off it is measured against the
  original.) The ground's shader tree is copied into each of the object's materials and mixed
  against it by that attribute, through the Edge Breakup node.
- **Subdivision cannot crack.** Only faces that can come within reach of an object are subdivided,
  by a conservative per-face bound, so every vertex on the cut — including those subdivision adds —
  provably stays put. Detail displacement is masked by the same falloff.
- **The original ground gets hidden** because the shared ground draws it. Left visible, the original
  stays flat and cuts back through every crater. Object Info still reads hidden objects.

The source comments carry the full reasoning, including the proof that the cut cannot crack.

## Known limits

- **Material displacement on the ground is invisible to this add-on.** Renderers apply it after the
  whole modifier stack, so the ground conforms to a surface that is then moved somewhere else, and
  the viewport keeps looking correct. Rebuild the height as `Subdivision` + `Displace` modifiers on
  the ground, or use Detail Height.
- **Where two objects' contact bands meet**, each ground vertex follows the nearest object, so the
  ground can fold into a steep sliver along the line where the nearest object changes — visible as
  a bright speck when it catches the light inside a shadow. Keep Contact Distances from overlapping
  between neighbours, or move them apart. The subdivided
  area around every object is sized by the largest Contact Distance on that ground.
- **The object's welded shading is computed against its own patch.** Where two objects' contact
  bands overlap, an object's normals and projected UVs ignore the other's crater.
- **Meniscus smoothness is capped by ground resolution.** Subdivisions helps; the crater's silhouette
  against the object stays as fine as the mesh allows.
- **Flat slabs don't chain.** Blending a patch into a floor it lies parallel to does nothing useful —
  every vertex is equidistant, so a proximity falloff has nothing to grip.
- **Non-uniform object scale** is approximated by its average when converting distances.

## Troubleshooting

Press **Diagnose** in the sidebar (or run `contact_blend_diag.py` from the Text Editor) with a
blended object or its ground selected. It writes a report to a Text datablock named
**`Contact Blend Report`** and checks the things that break only at render time or only silently:
per-modifier render toggles, mismatched subdivision levels, ground visibility and collection
exclusion, the shared ground's visibility and object list, render-time displacement on the ground,
whether `blend` and `cb_params` reach the evaluated mesh and the materials read them, whether the
subdivision cut is tearing, and both node groups' version stamps. The standalone script uses the
add-on's checks when the add-on is enabled, and a reduced copy otherwise.

## Development

The add-on is self-testing — it builds a scene, evaluates the modifier and asserts on the result:

```bash
blender --background --python-exit-code 1 --python contact_blend.py
blender --background --python-exit-code 1 --python tests/test_workflow.py
blender --background --python-exit-code 1 --python tests/test_textures.py
blender --background --python-exit-code 1 --python tests/test_geometry.py
blender --background --python-exit-code 1 --python tests/test_shared.py
```

```
OK  hero_moved=0.000000  ground dip=-0.0715 far=0.000000 subdiv=1681->7621  detail=0.2619
    blend=0.000..0.984  bent=576/1536  ground=7501
```

The self-test covers: the object's mesh is never deformed and carries only itself, flat shading
survives away from the contact, the shared ground conforms and stays untouched outside Contact
Distance, the contact island is subdivided and its cut cannot tear, ground shading stays uniform,
contact-zone detail stays inside the island, materials mix idempotently through the breakup node,
render-visibility and displacement hazards are detected, a stale node group upgrades in place
without losing settings, and a file saved under the 1.0 name migrates without orphaning its modifier.

- `tests/test_workflow.py` — shared materials and linked meshes, repeated application and refresh,
  user shader edits, retargeting, per-view-layer visibility, legacy removal, save/reload, node-state
  fidelity of copied shaders, every material slot, edge breakup, appended/linked groups found by
  stamp, grounds outside the view layer, and fitted distances.
- `tests/test_textures.py` — transformed grounds, UV sampling after conforming (shared and joined),
  private shader groups, disabled matching, small CPU Cycles renders against the original ground
  pattern, and the breakup node's identity and end-point guarantees.
- `tests/test_geometry.py` — coarse grounds, the crack-free cut, welding on curved grounds, Displace
  Ground off, world-metre distances under object scale, and instanced grounds; every test runs on
  both the shared ground and a legacy joined copy.
- `tests/test_shared.py` — two and five objects on one ground, per-object Contact Distance, a
  moving/scaled/rotated ground, removal, duplication, a deleted shared ground, ground settings,
  save/reload, multi-selection, how the selection is resolved, the panel in every state (drawn with
  a recording layout, since there is no UI headless), the Diagnose report, and a 1.x joined blend
  (`tests/fixtures/joined_v7.blend`, written by that release) evaluating the same and converting
  cleanly.

For a headless environment with the official `bpy` package installed, the same checks run directly
with that Python interpreter:

```bash
python -B contact_blend.py
python -B tests/test_workflow.py
python -B tests/test_textures.py
python -B tests/test_geometry.py
python -B tests/test_shared.py
```

## License

[Apache-2.0](LICENSE)

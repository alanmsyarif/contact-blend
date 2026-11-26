# SPDX-License-Identifier: Apache-2.0
"""Build the Contact Blend demo scenes from Poly Haven (CC0) assets, and render them.

Run with the Python interpreter that has the `bpy` module (or `blender -b -P`):

    python demo/build_demos.py                     # every scene: download, build, save, render
    python demo/build_demos.py --scene forest      # one scene
    python demo/build_demos.py --no-render         # build and save the .blend files only
    python demo/build_demos.py --preview           # quick low-resolution renders
    python demo/build_demos.py --wire              # also the wireframe set and the breakdown

Assets are cached in demo/assets/. Each scene is built with the add-on's real operators -- the
Ground picker plus `object.contact_blend`, exactly what an artist's clicks do -- then tuned
through the same settings the sidebar shows. "Before" renders are the identical scene and camera
with the objects merely sunk into the ground; "after" adds the blend.
"""

import argparse
import json
import math
import pathlib
import random
import re
import sys
import time
import urllib.request

import bpy
from mathutils import Euler, Vector
from mathutils.bvhtree import BVHTree

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import contact_blend as cb  # noqa: E402

ASSETS = HERE / "assets"
RENDERS = HERE / "renders"
API = "https://api.polyhaven.com"
USER_AGENT = "Mozilla/5.0 (compatible; asset-fetch/1.0)"
USED = {}          # asset id -> kind, for CREDITS.md


# --- Poly Haven ------------------------------------------------------------------------------

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def api(path):
    cache = ASSETS / "api" / (path.strip("/").replace("/", "_") + ".json")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(_get(f"{API}/{path}"))
    return json.loads(cache.read_text())


def _download(url, dest):
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(_get(url))
        tmp.rename(dest)
    return dest


def model(asset, res="2k"):
    """The asset's .blend (textures beside it, where its relative paths expect them)."""
    USED[asset] = "model"
    entry = api(f"files/{asset}")["blend"][res]["blend"]
    folder = ASSETS / "models" / asset
    for rel, inc in entry.get("include", {}).items():
        _download(inc["url"], folder / rel)
    return _download(entry["url"], folder / pathlib.Path(entry["url"]).name)


def texture(asset, res="2k", disp_res=None):
    """{map: path} for a texture set: colour, normal (GL), roughness (JPG), height (16-bit PNG).
    The height map can be fetched at its own resolution: a 16-bit 8k PNG is a gigabyte in
    memory once decoded."""
    USED[asset] = "texture"
    files = api(f"files/{asset}")
    out = {}
    for key, kind, fmt in (("Diffuse", "diff", "jpg"), ("nor_gl", "nor", "jpg"),
                           ("Rough", "rough", "jpg"), ("Displacement", "disp", "png")):
        if key in files:
            url = files[key][disp_res or res if kind == "disp" else res][fmt]["url"]
            out[kind] = _download(url, ASSETS / "textures" / asset / pathlib.Path(url).name)
    return out


def hdri(asset, res="2k"):
    USED[asset] = "hdri"
    url = api(f"files/{asset}")["hdri"][res]["hdr"]["url"]
    return _download(url, ASSETS / "hdris" / pathlib.Path(url).name)


def texture_size(asset, default=2.0):
    """Real-world size of a texture set in metres, from its metadata."""
    dims = api(f"info/{asset}").get("dimensions")
    return (max(dims) / 1000.0) if dims else default


def write_credits():
    lines = ["# Demo asset credits", "",
             "Every asset in the demo scenes comes from [Poly Haven](https://polyhaven.com) and is "
             "released under [CC0](https://creativecommons.org/publicdomain/zero/1.0/) (public "
             "domain); credit is not required, but given here gladly.", "",
             "| Asset | Type | Author(s) | Page |", "|---|---|---|---|"]
    # a single-scene run must not drop the other scenes' assets from the list
    credits = HERE / "CREDITS.md"
    used = {asset: kind for kind, asset in re.findall(
        r"\| (\w+) \| [^|]* \| https://polyhaven.com/a/(\w+) \|",
        credits.read_text())} if credits.exists() else {}
    used.update(USED)
    for asset, kind in sorted(used.items()):
        info = api(f"info/{asset}")
        authors = ", ".join(sorted(info.get("authors", {})))
        lines.append(f"| {info.get('name', asset)} | {kind} | {authors} | "
                     f"https://polyhaven.com/a/{asset} |")
    lines += ["", "License for all of the above: CC0 1.0 Universal (public domain dedication).", ""]
    (HERE / "CREDITS.md").write_text("\n".join(lines))


# --- scene building --------------------------------------------------------------------------

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    return scene


def world(scene, path, rotation_deg, strength=1.0):
    w = bpy.data.worlds.new("World")
    scene.world = w
    tree = w.node_tree
    tree.nodes.clear()
    coord = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value = (0, 0, math.radians(rotation_deg))
    env = tree.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(path))
    bg = tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = tree.nodes.new("ShaderNodeOutputWorld")
    tree.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    tree.links.new(env.outputs["Color"], bg.inputs["Color"])
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])


def _image(path, colour=True):
    img = bpy.data.images.load(str(path), check_existing=True)
    if not colour:
        img.colorspace_settings.name = "Non-Color"
    return img


def ground_material(name, maps, tile, variation=True, shade=(0.9, 1.07), micro=0.0):
    """A ground shader with its maps at real-world scale and the tiling broken up.

    The same texture repeating every couple of metres is the first thing that gives a ground
    away. Low-frequency noise varies its brightness and roughness, so no two tiles in frame look
    alike. Only those: a second, rotated lookup of the maps (tried first) disagrees with the
    relief the Displace modifier builds from the height map, and a rotated normal map lights
    every bump from the wrong side -- the ground read as brushed metal.
    """
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(bsdf.outputs[0], out.inputs["Surface"])
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    a = tree.nodes.new("ShaderNodeMapping")
    a.inputs["Scale"].default_value = (1 / tile, 1 / tile, 1)
    tree.links.new(uv.outputs["UV"], a.inputs["Vector"])

    def sampled(path, colour):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = _image(path, colour)
        tree.links.new(a.outputs["Vector"], node.inputs["Vector"])
        return node.outputs["Color"]

    colour = sampled(maps["diff"], True)
    rough = sampled(maps["rough"], False) if "rough" in maps else None
    if variation:
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 0.25          # patches of a few metres
        noise.inputs["Detail"].default_value = 4
        tree.links.new(uv.outputs["UV"], noise.inputs["Vector"])
        lo, hi = shade
        shade = tree.nodes.new("ShaderNodeMapRange")       # a gentle brightness drift
        shade.inputs[1].default_value, shade.inputs[2].default_value = 0.3, 0.7
        shade.inputs[3].default_value, shade.inputs[4].default_value = lo, hi
        tree.links.new(noise.outputs["Fac"], shade.inputs[0])
        mul = tree.nodes.new("ShaderNodeMix")
        mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
        mul.inputs["Factor"].default_value = 1.0
        tree.links.new(colour, mul.inputs[6])
        tree.links.new(shade.outputs[0], mul.inputs[7])
        colour = mul.outputs[2]
        if rough is not None:                              # darker, damper patches are glossier
            wet = tree.nodes.new("ShaderNodeMath")
            wet.operation = "MULTIPLY_ADD"
            tree.links.new(rough, wet.inputs[0])
            tree.links.new(shade.outputs[0], wet.inputs[1])
            wet.inputs[2].default_value = 0.0
            rough = wet.outputs[0]
    tree.links.new(colour, bsdf.inputs["Base Color"])
    if rough is not None:
        tree.links.new(rough, bsdf.inputs["Roughness"])
    if "nor" in maps:
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(sampled(maps["nor"], False), nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    if micro:
        # Grain-scale relief as true micro-displacement, diced per pixel by Cycles' adaptive
        # subdivision (see micro_displace): grains that cast shadows in a raking sun. The
        # add-on conforms the ground's geometry; this rides on top of it, ground and meniscus.
        disp = tree.nodes.new("ShaderNodeDisplacement")
        disp.inputs["Midlevel"].default_value = 0.5
        disp.inputs["Scale"].default_value = micro
        tree.links.new(sampled(maps["disp"], False), disp.inputs["Height"])
        tree.links.new(disp.outputs[0], out.inputs["Displacement"])
        mat.displacement_method = "DISPLACEMENT"
    return mat


def micro_displace(obj, pixel=1.0):
    """Dice `obj` per pixel at render time (adaptive subdivision, last in its stack) so its
    material's displacement makes real geometry."""
    sub = obj.modifiers.new("Micro", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    # 0 levels in the viewport and at render: Cycles dices it itself. Render levels left at the
    # default 2 were applied wherever the ground is read as geometry -- the add-on's Object
    # Info of it included -- and the contact conform ran on 15 million faces.
    sub.levels = sub.render_levels = 0
    sub.use_adaptive_subdivision = True
    sub.adaptive_pixel_size = pixel
    return sub


def ground(name, size, cells, maps, tile, height, material=None):
    """A grid with real displacement as geometry: a Displace modifier driven by the height map
    (never material displacement, which the add-on cannot see)."""
    # Stored coarse and subdivided by a modifier: the same density at render time, a fraction of
    # the file size.
    bpy.ops.mesh.primitive_grid_add(size=size, x_subdivisions=cells // 8, y_subdivisions=cells // 8)
    obj = bpy.context.object
    sub = obj.modifiers.new("Density", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = sub.render_levels = 3
    obj.name = name
    obj.data.uv_layers.active.name = "UVMap"
    # grid UVs span 0..1 over the whole grid; scale them to metres so `tile` is in metres
    for loop in obj.data.uv_layers["UVMap"].data:
        loop.uv = (loop.uv[0] * size, loop.uv[1] * size)
    tex = bpy.data.textures.new(name + " height", "IMAGE")
    tex.image = _image(maps["disp"], False)
    tex.extension = "REPEAT"
    tex.repeat_x = tex.repeat_y = 1
    disp = obj.modifiers.new("Height", "DISPLACE")
    disp.texture = tex
    disp.texture_coords = "UV"
    disp.uv_layer = "UVMap"
    disp.strength = height
    disp.mid_level = 0.5
    # the Displace modifier reads UVs as 0..1 per image: scale the coordinates it sees
    tex_map = obj.data.uv_layers.new(name="DispUV")
    for a, b in zip(obj.data.uv_layers["UVMap"].data, tex_map.data):
        b.uv = (a.uv[0] / tile, a.uv[1] / tile)
    disp.uv_layer = "DispUV"
    obj.data.uv_layers.active = obj.data.uv_layers["UVMap"]
    obj.data.uv_layers["UVMap"].active_render = True
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(material)
    return obj


def apron(floor, size, cell, maps, tile, height):
    """The same ground continued, coarse, out to `size` metres around `floor`, so a camera that
    sees the horizon sees ground meet the sky's own horizon and not the edge of a tile.

    A grid with a hole one cell smaller than `floor`, its UVs and Displace matching `floor`'s
    exactly so the two surfaces agree at the seam; it sits 1 cm low so `floor` wins where they
    overlap. It is not the blend's ground: nothing out there touches an object.
    """
    import bmesh
    inner = floor.dimensions.x
    n = int(size / cell)
    bpy.ops.mesh.primitive_grid_add(size=size, x_subdivisions=n, y_subdivisions=n,
                                    location=(floor.location.x, floor.location.y, -0.01))
    obj = bpy.context.object
    obj.name = floor.name + " apron"
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    half = inner / 2 - cell
    bmesh.ops.delete(bm, geom=[f for f in bm.faces
                               if max(abs(c) for c in f.calc_center_median()[:2]) < half],
                     context="FACES")
    uv, disp_uv = bm.loops.layers.uv["UVMap"], bm.loops.layers.uv.new("DispUV")
    for f in bm.faces:
        for loop in f.loops:
            co = loop.vert.co
            loop[uv].uv = (co.x + inner / 2, co.y + inner / 2)       # floor's UVs, in metres
            loop[disp_uv].uv = ((co.x + inner / 2) / tile, (co.y + inner / 2) / tile)
    bm.to_mesh(obj.data)
    bm.free()
    disp = obj.modifiers.new("Height", "DISPLACE")
    disp.texture = floor.modifiers["Height"].texture
    disp.texture_coords, disp.uv_layer = "UV", "DispUV"
    disp.strength, disp.mid_level = height, 0.5
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(floor.active_material)
    return obj


def append_objects(blend_path, rename=None, names=None):
    """Append a model's meshes. Scans ship with several levels of detail; only the finest
    (LOD0, or the only one) of each piece is kept."""
    with bpy.data.libraries.load(str(blend_path), link=False) as (src, dst):
        wanted = [n for n in src.objects if names is None or any(k in n for k in names)]
        lod0 = [n for n in wanted if "LOD" not in n or n.endswith("LOD0")]
        dst.objects = lod0
    out = []
    for obj in dst.objects:
        if obj is None or obj.type != "MESH":
            continue
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = None
        obj.location = (0, 0, 0)
        out.append(obj)
    if rename:
        for i, obj in enumerate(out):
            obj.name = f"{rename}{'' if len(out) == 1 else '.' + str(i + 1)}"
    return out


def sun_rotation(path, azimuth_deg):
    """World rotation (degrees about Z) that puts the HDRI's sun at `azimuth_deg`, measured
    counter-clockwise from +X. The sun is the brightest pixel of the equirectangular map."""
    import numpy as np
    img = bpy.data.images.load(str(path), check_existing=True)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    lum = px.reshape(h, w, 4)[..., :3].sum(axis=2)
    row, col = np.unravel_index(np.argmax(lum), lum.shape)
    u, v = (col + 0.5) / w, (row + 0.5) / h
    # Blender's equirectangular lookup: u = atan2(y, -x) / 2pi + 0.5
    # (checked by rendering a sphere lit by the result: a 180 degree error here lit every
    # earlier version of these scenes from the opposite side to the one asked for)
    phi = (u - 0.5) * 2 * math.pi
    sun_azimuth = math.degrees(math.atan2(math.sin(phi), -math.cos(phi))) + 180.0
    elevation = math.degrees((v - 0.5) * math.pi)
    print(f"{path.name}: sun at azimuth {sun_azimuth:.0f}, elevation {elevation:.0f}", flush=True)
    return sun_azimuth - azimuth_deg


def place(obj, location, rotation_deg=(0, 0, 0), scale=1.0, sink=0.3):
    """Put a scan down like a real embedded stone: rotate, scale, then lower it so `sink` of
    its height is below ground level (z = 0 at the given location)."""
    obj.rotation_euler = Euler([math.radians(a) for a in rotation_deg])
    obj.scale = (scale, scale, scale)
    obj.location = (0, 0, 0)
    bpy.context.view_layer.update()
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = min(c.z for c in corners)
    high = max(c.z for c in corners)
    centre = sum(corners, Vector()) / 8
    obj.location = (location[0] - centre.x, location[1] - centre.y,
                    location[2] - low - sink * (high - low))
    bpy.context.view_layer.update()
    return obj


def scatter(templates, floor, count, area, scale, seed, avoid=(), sink=0.15, tilt=6.0,
            name="Scatter"):
    """`count` linked duplicates of `templates` dropped onto `floor` inside `area` ((x0, x1),
    (y0, y1)), away from the `avoid` discs ((x, y), r), each turned at random and sunk `sink`
    of its height. Linked duplicates share their mesh: dozens cost one mesh's memory."""
    rng = random.Random(seed)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    surface = BVHTree.FromObject(floor, depsgraph)
    to_floor = floor.matrix_world.inverted()
    placed, tries = [], 0
    while len(placed) < count and tries < count * 60:
        tries += 1
        x, y = rng.uniform(*area[0]), rng.uniform(*area[1])
        if any((Vector((x, y)) - Vector(c)).length < r for c, r in avoid):
            continue
        if any((Vector((x, y)) - o.location.xy).length < 0.6 * max(o.dimensions.xy) for o in placed):
            continue
        hit, *_ = surface.ray_cast(to_floor @ Vector((x, y, 50.0)), Vector((0, 0, -1)))
        if hit is None:
            continue
        z = (floor.matrix_world @ hit).z
        obj = rng.choice(templates).copy()
        bpy.context.scene.collection.objects.link(obj)
        obj.name = f"{name}.{len(placed):03d}"
        place(obj, (x, y, z), (rng.uniform(-tilt, tilt), rng.uniform(-tilt, tilt), rng.uniform(0, 360)),
              rng.uniform(*scale), sink=sink)
        placed.append(obj)
    for t in templates:
        bpy.data.objects.remove(t)          # the copies keep the mesh
    return placed


def blend_into(ground_obj, objs):
    """What an artist does: pick the Ground in the sidebar, select the objects, press the button."""
    scene = bpy.context.scene
    scene.contact_blend_ground = ground_obj
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    assert bpy.ops.object.contact_blend() == {"FINISHED"}
    scene.contact_blend_ground = None
    return cb.find_proxy(ground_obj)


def tune(obj, **settings):
    mod = cb.find_mod(obj)
    for name, value in settings.items():
        assert cb.set_socket(mod, name.replace("_", " ").title(), value), name
    obj.update_tag()


def tune_ground(proxy, **settings):
    mod = cb.ground_mod(proxy)
    for name, value in settings.items():
        assert cb.set_socket(mod, name, value), name
    proxy.update_tag()


def breakup(obj, strength, sharpness, scale):
    for slot in obj.material_slots:
        node = cb.breakup_node(slot.material)
        if node is not None:
            node.inputs["Breakup"].default_value = strength
            node.inputs["Sharpness"].default_value = sharpness
            node.inputs["Noise Scale"].default_value = scale


def camera(scene, name, location, look_at, lens=50, fstop=None, focus=None):
    cam_data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cam_data)
    scene.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam_data.lens = lens
    cam_data.sensor_width = 36
    if fstop:
        cam_data.dof.use_dof = True
        cam_data.dof.aperture_fstop = fstop
        cam_data.dof.focus_distance = focus if focus else _focus(location, direction)
    return cam


def _focus(location, direction):
    """Focus where a photographer would: on the first surface along the line of sight to the
    subject, not on its centre -- the near flank and its junction are what must be sharp."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, where, *_ = bpy.context.scene.ray_cast(depsgraph, Vector(location), direction.normalized())
    return (where - Vector(location)).length if hit else direction.length


def render_settings(scene, samples, exposure=0.0, preview=False):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24 if preview else samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.02 if preview else 0.005
    scene.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    scene.cycles.denoising_prefilter = "ACCURATE"
    scene.cycles.dicing_rate = 1.0
    scene.cycles.offscreen_dicing_scale = 16.0       # micro-displacement: dice what is seen
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.sample_clamp_indirect = 6.0      # no fireflies from the sun's bounce
    scene.cycles.max_bounces = 8
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Base Contrast"
    scene.view_settings.exposure = exposure
    scene.render.resolution_x, scene.render.resolution_y = (640, 360) if preview else (1920, 1080)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 92


def render(scene, cam, path, size=(1920, 1080), samples=None, preview=False):
    scene.camera = cam
    if not preview:
        scene.render.resolution_x, scene.render.resolution_y = size
        if samples:
            scene.cycles.samples = samples
    scene.render.filepath = str(path)
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path.name} in {time.time() - t0:.0f} s", flush=True)


def _label(draw, xy, text):
    x, y = xy
    draw.text((x + 1, y + 1), text, fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255))


def composite(before, after, out):
    """Left half before, right half after, split by a thin line, with small labels."""
    from PIL import Image, ImageDraw
    a, b = Image.open(before), Image.open(after)
    w, h = a.size
    img = a.copy()
    img.paste(b.crop((w // 2, 0, w, h)), (w // 2, 0))
    draw = ImageDraw.Draw(img)
    draw.line([(w // 2, 0), (w // 2, h)], fill=(255, 255, 255), width=max(2, w // 640))
    _label(draw, (16, h - 32), "without Contact Blend")
    _label(draw, (w // 2 + 16, h - 32), "with Contact Blend")
    img.save(out, quality=92)


def breakdown(render_before, render_after, wire_before, wire_after, out, size=(1920, 1080)):
    """Four-up: renders on top, wireframes below; without Contact Blend left, with it right."""
    from PIL import Image, ImageDraw
    w, h = size[0] // 2, size[1] // 2
    img = Image.new("RGB", size)
    tiles = ((render_before, 0, 0, "render, without Contact Blend"),
             (render_after, w, 0, "render, with Contact Blend"),
             (wire_before, 0, h, "wireframe, without"),
             (wire_after, w, h, "wireframe, with"))
    draw = ImageDraw.Draw(img)
    for path, x, y, text in tiles:
        img.paste(Image.open(path).convert("RGB").resize((w, h), Image.LANCZOS), (x, y))
    for path, x, y, text in tiles:
        _label(draw, (x + 14, y + h - 28), text)
    draw.line([(w, 0), (w, size[1])], fill=(255, 255, 255), width=2)
    draw.line([(0, h), (size[0], h)], fill=(255, 255, 255), width=2)
    img.save(out, quality=92)


def wire_material():
    """Clay with dark anti-aliased edges of the *evaluated* triangles (the Wireframe node sees
    the geometry Cycles renders: the conformed, subdivided ground and every welded junction)."""
    mat = bpy.data.materials.get("Wire clay") or bpy.data.materials.new("Wire clay")
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    clay = tree.nodes.new("ShaderNodeBsdfDiffuse")
    clay.inputs["Color"].default_value = (0.42, 0.42, 0.40, 1)
    ink = tree.nodes.new("ShaderNodeBsdfDiffuse")
    ink.inputs["Color"].default_value = (0.015, 0.015, 0.02, 1)
    wire = tree.nodes.new("ShaderNodeWireframe")
    wire.use_pixel_size = True
    wire.inputs["Size"].default_value = 0.55
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(wire.outputs[0], mix.inputs[0])
    tree.links.new(clay.outputs[0], mix.inputs[1])
    tree.links.new(ink.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def render_wire(scene, cam, path, preview):
    """The wireframe view from `cam`: every material overridden by wire clay, depth of field
    and per-pixel dicing off so the edges are the mesh's own and sharp."""
    layer = bpy.context.view_layer
    layer.material_override = wire_material()
    dof = cam.data.dof.use_dof
    cam.data.dof.use_dof = False
    adaptive = [m for o in scene.objects for m in o.modifiers
                if m.type == "SUBSURF" and getattr(m, "use_adaptive_subdivision", False)]
    for m in adaptive:
        m.use_adaptive_subdivision = False
    samples = scene.cycles.samples
    try:
        render(scene, cam, path, (1280, 720), 48, preview)
    finally:
        layer.material_override = None
        cam.data.dof.use_dof = dof
        for m in adaptive:
            m.use_adaptive_subdivision = True
        scene.cycles.samples = samples


def record(name, heroes, proxy, timings):
    """Every setting the blend actually used, per object and on the shared ground, plus the
    measured timings: demo/renders/<scene>.json, for anyone quoting the renders."""
    def inputs(mod):
        out = {}
        for item in mod.node_group.interface.items_tree:
            if item.item_type != "SOCKET" or item.in_out != "INPUT":
                continue
            v = cb.socket(mod, item.name)
            if isinstance(v, (int, float, bool, str)):
                out[item.name] = round(v, 6) if isinstance(v, float) else v
            elif v is not None and hasattr(v, "name"):
                out[item.name] = v.name
        return out
    data = {"objects": {}, "ground": inputs(cb.ground_mod(proxy)), "timings_s": timings}
    for obj in heroes:
        entry = {"settings": inputs(cb.find_mod(obj)), "breakup": {}}
        for slot in obj.material_slots:
            node = cb.breakup_node(slot.material) if slot.material else None
            if node:
                entry["breakup"][slot.material.name] = {
                    i.name: round(i.default_value, 6) for i in node.inputs
                    if i.name != "Blend" and not i.is_linked}
        data["objects"][obj.name] = entry
    (RENDERS / f"{name}.json").write_text(json.dumps(data, indent=2))


def save_light(name):
    """Save demo/<name>.blend with every texture packed, images over 2k packed as 2k copies:
    the renders use full resolution, the file stays small enough to keep in the repository."""
    for img in bpy.data.images:
        if img.source == "FILE" and max(img.size) > 2048:
            w, h = img.size
            f = 2048 / max(w, h)
            img.scale(max(1, int(w * f)), max(1, int(h * f)))
            img.pack()
    bpy.ops.file.pack_all()
    path = HERE / f"{name}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)
    print(f"saved {path.name}: {path.stat().st_size / 1e6:.1f} MB", flush=True)
    return path.stat().st_size / 1e6


# --- the scenes ------------------------------------------------------------------------------

SCENES = {}


def scene(fn):
    SCENES[fn.__name__] = fn
    return fn


def lod0(asset, names=None):
    return append_objects(model(asset), names=names)


@scene
def forest(args):
    """Mossy rocks and pine roots settled into a leaf-littered forest floor, low warm sun
    through the trees, seen from a crouch."""
    scene = reset()
    sky = hdri("autumn_forest_04")
    world(scene, sky, rotation_deg=sun_rotation(sky, 115), strength=1.0)
    maps = texture("forest_leaves_04", "4k")
    tile = texture_size("forest_leaves_04")
    floor = ground("Forest floor", 14, 1400, maps, tile, height=0.05,
                   material=ground_material("Forest floor", maps, tile))
    rocks = lod0("rock_moss_set_02", names=("rock11", "rock08", "rock09"))
    big = next(o for o in rocks if "rock11" in o.name)
    mid = next(o for o in rocks if "rock08" in o.name)
    small = next(o for o in rocks if "rock09" in o.name)
    big.name, mid.name, small.name = "Mossy boulder", "Mossy rock", "Small mossy rock"
    place(big, (0.0, 0.0, 0.0), (0, 0, 20), sink=0.3)
    place(mid, (1.9, 0.9, 0.0), (0, 0, 75), sink=0.3)
    place(small, (-1.55, -0.55, 0.0), (0, 0, -40), scale=0.6, sink=0.35)
    roots = lod0("pine_roots", names=("pine_roots_a",))[0]
    roots.name = "Pine roots"
    place(roots, (-0.15, -1.45, 0.0), (0, 0, 15), sink=0.45)
    branch = lod0("dry_branches_medium_01", names=("_a",))[0]
    branch.name = "Fallen branch"
    place(branch, (1.3, -1.35, 0.0), (0, 0, 70), sink=0.35)
    blended = [big, mid, small, roots, branch]
    keep_clear = [((0, 0), 1.3), ((1.9, 0.9), 0.8), ((-1.55, -0.55), 0.5), ((-0.15, -1.45), 0.9),
                  ((1.3, -1.35), 0.6), ((3.0, -3.6), 1.2)]      # (the camera's own spot)
    # the background: a fallen trunk and a stump, ferns, so the far ground is not a bare plane
    log = lod0("dead_tree_trunk")[0]
    place(log, (-1.8, 4.2, 0.0), (0, 0, 15), sink=0.25)
    stump = lod0("tree_stump_01")[0]
    place(stump, (3.4, 3.8, 0.0), (0, 0, 40), sink=0.1)
    keep_clear += [((-1.8, 4.2), 1.6), ((3.4, 3.8), 1.0)]
    scatter(lod0("fern_02"), floor, 16, ((-6, 6), (1.5, 6.5)), (0.7, 1.15), 3, keep_clear, sink=0.05,
            name="Fern")
    scatter(lod0("dry_branches_medium_01", names=("_b", "_c")), floor, 14, ((-4, 4), (-3, 4)),
            (0.6, 1.0), 5, keep_clear, sink=0.3, tilt=4, name="Twig")
    scatter(lod0("rock_moss_set_02", names=("rock09", "rock12")), floor, 10, ((-5, 5), (-3, 5)),
            (0.12, 0.3), 7, keep_clear, sink=0.35, tilt=20, name="Stone")
    apron(floor, 90, 0.1, maps, tile, height=0.05)
    return floor, blended, {"cams": [
        # crouching, 50 mm: the boulder's base and the roots against it are the subject
        ("hero", (3.0, -3.6, 0.85), (0.3, 0.0, 0.05), 50, 8),
        ("compare", (1.5, -3.3, 0.55), (0.1, -1.2, 0.15), 60, 8)]}


@scene
def boulders(args):
    """Namaqualand boulders of very different sizes on gravelly sand under a low sunrise sun;
    the front pair's contact bands overlap."""
    scene = reset()
    sky = hdri("klippad_sunrise_2")
    world(scene, sky, rotation_deg=sun_rotation(sky, 150), strength=1.0)
    maps = texture("gravelly_sand", "4k")
    tile = texture_size("gravelly_sand")
    floor = ground("Gravelly sand", 18, 1500, maps, tile, height=0.04,
                   material=ground_material("Gravelly sand", maps, tile, shade=(0.95, 1.04)))
    layout = [  # asset, position, heading, sink -- real scale throughout
        ("namaqualand_boulder_03", (0.0, 0.0), 15, 0.22),
        ("namaqualand_boulder_05", (2.05, -0.55), 120, 0.25),     # leans on the first one's band
        ("namaqualand_boulder_04", (-3.6, 2.4), 200, 0.25),
        ("namaqualand_boulder_06", (3.7, 2.6), 70, 0.3),
        ("namaqualand_boulder_02", (-1.9, -2.9), 300, 0.3),
    ]
    rocks = []
    for i, (asset, xy, heading, sink) in enumerate(layout):
        obj = append_objects(model(asset), rename=f"Boulder {i + 1}")[0]
        rocks.append(place(obj, (xy[0], xy[1], 0.0), (0, 0, heading), sink=sink))
    small = lod0("namaqualand_rocks_01")
    rng = random.Random(11)
    spots = [(1.0, -1.9), (-0.9, -1.6), (2.9, -1.4), (0.4, -3.1)]
    for i, (obj, xy) in enumerate(zip(small, spots)):
        obj.name = f"Small rock {i + 1}"
        place(obj, (xy[0], xy[1], 0.0), (0, 0, rng.uniform(0, 360)), scale=rng.uniform(1.0, 1.6), sink=0.3)
        rocks.append(obj)
    keep_clear = [((0, 0), 2.2), ((2.05, -0.55), 1.1), ((-3.6, 2.4), 1.8), ((3.7, 2.6), 1.0),
                  ((-1.9, -2.9), 1.5)] + [(xy, 0.4) for xy in spots] + [((1.2, -6.0), 1.5)]
    scatter(lod0("namaqualand_stones_01"), floor, 70, ((-8, 8), (-6, 8)), (0.7, 1.4), 13, keep_clear,
            sink=0.35, tilt=25, name="Stone")
    scatter(lod0("cheiridopsis_succulent"), floor, 7, ((-8, 8), (1, 8)), (0.8, 1.1), 17, keep_clear,
            sink=0.05, name="Succulent")
    cliff = append_objects(model("namaqualand_cliff_02"), rename="Outcrop")[0]
    place(cliff, (-14.0, 60.0, 0.0), (0, 0, 200), scale=1.6, sink=0.25)
    apron(floor, 400, 0.5, maps, tile, height=0.04)
    return floor, rocks, {"cams": [
        ("hero", (2.4, -7.6, 1.45), (0.2, 0.0, 0.55), 45, 11),
        ("compare", (1.0, -3.9, 0.95), (1.15, -0.6, 0.25), 55, 11)]}


@scene
def macro(args):
    """A stone half-buried in sand: low raking sun, every grain a shadow-casting bump."""
    scene = reset()
    sky = hdri("rogland_sunset")
    world(scene, sky, rotation_deg=sun_rotation(sky, 60), strength=1.0)
    maps = texture("sand_01", "8k", disp_res="4k")
    tile = texture_size("sand_01")
    floor = ground("Sand", 1.2, 960, maps, tile, height=0.0,
                   material=ground_material("Sand", maps, tile, shade=(0.96, 1.03), micro=0.0025))
    micro_displace(floor)
    apron(floor, 12, 0.02, maps, tile, height=0.0)
    stone = append_objects(model("stone_01"), rename="Stone")[0]
    place(stone, (0.0, 0.0, 0.0), (6, -10, 40), sink=0.45)
    return floor, [stone], {"cams": [
        ("hero", (0.2, -0.27, 0.085), (0.0, 0.0, 0.015), 100, 16),
        ("compare", (0.2, -0.27, 0.085), (0.0, 0.0, 0.015), 100, 16)],
        "after_blend": lambda proxy: micro_displace(proxy)}


TUNING = {
    # The add-on's fitted defaults throughout (Fit Size: distances from each object's size,
    # Edge Breakup with crevices); only the shared ground's own settings are chosen here.
    "forest": dict(objects={}, ground={"Contact Subdivisions": 2}),
    "boulders": dict(objects={}, ground={"Contact Subdivisions": 2}),
    # Half the default rise: at 1:1 the sand climbed over the stone's low lobe like a pillow.
    "macro": dict(objects={"Ground Conform": 0.5}, ground={"Contact Subdivisions": 2}),
}


def build(name, args):
    print(f"== {name}", flush=True)
    t_start = time.time()
    timings = {}
    floor, rocks, extra = SCENES[name](args)
    scene = bpy.context.scene
    cams = {}
    for cam_name, loc, target, lens, fstop in extra["cams"]:
        cams[cam_name] = camera(scene, f"{name} {cam_name}", loc, target, lens, fstop)
    render_settings(scene, args.samples, preview=args.preview)
    RENDERS.mkdir(parents=True, exist_ok=True)
    suffix = "_preview" if args.preview else ""
    out = {k: RENDERS / f"{name}_{k}{suffix}.jpg" for k in
           ("before", "after", "hero", "compare", "wire_before", "wire_after", "wire_compare", "breakdown")}
    timings["build_scene"] = round(time.time() - t_start, 1)

    def timed(key, fn, *a):
        t0 = time.time()
        fn(*a)
        timings[key] = round(time.time() - t0, 1)

    if not args.no_render:
        timed("render_before", render, scene, cams["compare"], out["before"], (1280, 720), 128, args.preview)
        if args.wire:
            timed("render_wire_before", render_wire, scene, cams["compare"], out["wire_before"], args.preview)
    t0 = time.time()
    proxy = blend_into(floor, rocks)
    tuning = TUNING[name]
    import os
    if os.environ.get("CB_DEMO_TUNE"):            # experiments: {"objects": {...}, "ground": {...}}
        tuning = {**tuning, **json.loads(os.environ["CB_DEMO_TUNE"])}
    if os.environ.get("CB_DEMO_NO_MICRO") and "after_blend" in extra:
        extra.pop("after_blend")
    for rock in rocks:
        for socket_name, value in tuning["objects"].items():
            assert cb.set_socket(cb.find_mod(rock), socket_name, value), socket_name
        rock.update_tag()
    tune_ground(proxy, **tuning["ground"])
    if "after_blend" in extra:
        extra["after_blend"](proxy)
    bpy.context.view_layer.update()
    timings["blend_and_evaluate"] = round(time.time() - t0, 1)
    if not args.no_render:
        timed("render_after", render, scene, cams["compare"], out["after"], (1280, 720), 128, args.preview)
        if args.wire:
            timed("render_wire_after", render_wire, scene, cams["compare"], out["wire_after"], args.preview)
        timed("render_hero", render, scene, cams["hero"], out["hero"], (1920, 1080), args.samples, args.preview)
        composite(out["before"], out["after"], out["compare"])
        if args.wire:
            composite(out["wire_before"], out["wire_after"], out["wire_compare"])
            breakdown(out["before"], out["after"], out["wire_before"], out["wire_after"], out["breakdown"])
    timings["total_to_save"] = round(time.time() - t_start, 1)
    record(name + suffix, rocks, proxy, timings)
    timings["blend_file_mb"] = round(save_light(name), 1) if not args.preview else None
    record(name + suffix, rocks, proxy, timings)
    print(f"timings {name}: {timings}", flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scene", choices=sorted(SCENES), action="append")
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--samples", type=int, default=192, help="hero image samples")
    parser.add_argument("--wire", action="store_true",
                        help="also render the wireframe pair, its split composite and the four-up breakdown")
    args = parser.parse_args(argv)
    cb.register()
    try:
        for name in args.scene or list(SCENES):
            build(name, args)
    finally:
        write_credits()
        cb.unregister()


if __name__ == "__main__":
    main()

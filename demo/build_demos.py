# SPDX-License-Identifier: Apache-2.0
"""Build the Contact Blend demo scenes from Poly Haven (CC0) assets, and render them.

Run with the Python interpreter that has the `bpy` module (or `blender -b -P`):

    python demo/build_demos.py                     # every scene: download, build, save, render
    python demo/build_demos.py --scene forest      # one scene
    python demo/build_demos.py --no-render         # build and save the .blend files only
    python demo/build_demos.py --preview           # quick low-resolution renders

Assets are cached in demo/assets/. Each scene is built with the add-on's real operators -- the
Ground picker plus `object.contact_blend`, exactly what an artist's clicks do -- then tuned
through the same settings the sidebar shows. "Before" renders are the identical scene and camera
with the objects merely sunk into the ground; "after" adds the blend.
"""

import argparse
import json
import math
import pathlib
import sys
import time
import urllib.request

import bpy
from mathutils import Euler, Vector

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


def texture(asset, res="2k"):
    """{map: path} for a texture set: colour, normal (GL), roughness (JPG), height (16-bit PNG)."""
    USED[asset] = "texture"
    files = api(f"files/{asset}")
    out = {}
    for key, kind, fmt in (("Diffuse", "diff", "jpg"), ("nor_gl", "nor", "jpg"),
                           ("Rough", "rough", "jpg"), ("Displacement", "disp", "png")):
        if key in files:
            url = files[key][res][fmt]["url"]
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
    for asset, kind in sorted(USED.items()):
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


def ground_material(name, maps, tile, variation=True):
    """A ground shader with its maps at real-world scale and the tiling broken up.

    The same texture repeating every couple of metres is the first thing that gives a ground
    away. A second lookup, rotated and offset, is blended in by low-frequency noise, so no two
    tiles in frame look alike.
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
    vectors = [a.outputs["Vector"]]
    mask = None
    if variation:
        b = tree.nodes.new("ShaderNodeMapping")
        b.inputs["Location"].default_value = (0.37, 0.71, 0)
        b.inputs["Rotation"].default_value = (0, 0, math.radians(57))
        b.inputs["Scale"].default_value = (0.83 / tile, 0.83 / tile, 1)
        tree.links.new(uv.outputs["UV"], b.inputs["Vector"])
        vectors.append(b.outputs["Vector"])
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 0.35
        noise.inputs["Detail"].default_value = 3
        tree.links.new(uv.outputs["UV"], noise.inputs["Vector"])
        ramp = tree.nodes.new("ShaderNodeMapRange")
        ramp.inputs[1].default_value = 0.42
        ramp.inputs[2].default_value = 0.58
        tree.links.new(noise.outputs["Fac"], ramp.inputs[0])
        mask = ramp.outputs[0]

    def sampled(path, colour, kind="RGBA"):
        lookups = []
        for v in vectors:
            node = tree.nodes.new("ShaderNodeTexImage")
            node.image = _image(path, colour)
            tree.links.new(v, node.inputs["Vector"])
            lookups.append(node.outputs["Color"])
        if mask is None:
            return lookups[0]
        mix = tree.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        tree.links.new(mask, mix.inputs["Factor"])
        tree.links.new(lookups[0], mix.inputs[6])
        tree.links.new(lookups[1], mix.inputs[7])
        return mix.outputs[2]

    tree.links.new(sampled(maps["diff"], True), bsdf.inputs["Base Color"])
    if "rough" in maps:
        tree.links.new(sampled(maps["rough"], False), bsdf.inputs["Roughness"])
    if "nor" in maps:
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(sampled(maps["nor"], False), nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


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
    phi = (u - 0.5) * 2 * math.pi
    sun_azimuth = math.degrees(math.atan2(math.sin(phi), -math.cos(phi)))
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
    scene.cycles.adaptive_threshold = 0.02 if preview else 0.008
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


def render(scene, cam, path):
    scene.camera = cam
    scene.render.filepath = str(path)
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path.name} in {time.time() - t0:.0f} s", flush=True)


def composite(before, after, out):
    """Left half before, right half after, split by a thin line, with small labels."""
    from PIL import Image, ImageDraw
    a, b = Image.open(before), Image.open(after)
    w, h = a.size
    img = a.copy()
    img.paste(b.crop((w // 2, 0, w, h)), (w // 2, 0))
    draw = ImageDraw.Draw(img)
    draw.line([(w // 2, 0), (w // 2, h)], fill=(255, 255, 255), width=max(2, w // 640))
    for text, x in (("without Contact Blend", 16), ("with Contact Blend", w // 2 + 16)):
        draw.text((x + 1, h - 31), text, fill=(0, 0, 0))
        draw.text((x, h - 32), text, fill=(255, 255, 255))
    img.save(out, quality=92)


# --- the scenes ------------------------------------------------------------------------------

SCENES = {}


def scene(fn):
    SCENES[fn.__name__] = fn
    return fn


@scene
def forest(args):
    """Two mossy boulders settled into a forest floor, soft light under the canopy."""
    scene = reset()
    sky = hdri("forest_slope")
    world(scene, sky, rotation_deg=sun_rotation(sky, 150), strength=1.0)
    maps = texture("forest_ground_04")
    tile = texture_size("forest_ground_04")
    floor = ground("Forest floor", 16, 1600, maps, tile, height=0.08,
                   material=ground_material("Forest floor", maps, tile))
    rocks = append_objects(model("rock_moss_set_01"), names=("rock02", "rock03"))
    big, small = sorted(rocks, key=lambda o: -o.dimensions.length)
    big.name, small.name = "Mossy boulder", "Mossy rock"
    place(big, (0.0, 0.0, 0.0), (0, 0, 35), sink=0.22)
    place(small, (2.55, 1.4, 0.0), (0, 0, -60), sink=0.28)
    return floor, [big, small], {"cams": [
        ("hero", (6.2, -6.0, 1.75), (1.0, 0.5, 0.45), 40, 8),
        ("compare", (3.7, -2.4, 0.75), (1.7, 0.75, 0.2), 70, 11)]}


@scene
def boulders(args):
    """Namaqualand boulders of very different sizes on dry, stony ground in hard sun."""
    scene = reset()
    sky = hdri("goegap")
    world(scene, sky, rotation_deg=sun_rotation(sky, 110), strength=1.0)
    maps = texture("dry_ground_rocks")
    tile = texture_size("dry_ground_rocks")
    floor = ground("Dry ground", 18, 1500, maps, tile, height=0.06,
                   material=ground_material("Dry ground", maps, tile))
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
    return floor, rocks, {"cams": [
        ("hero", (7.5, -9.0, 1.9), (0.0, 0.2, 0.5), 35, 11),
        ("compare", (3.6, -3.4, 0.7), (1.4, -0.3, 0.25), 60, 11)]}


@scene
def macro(args):
    """A pebble half-buried in sand: low, close, shallow depth of field."""
    scene = reset()
    sky = hdri("kloofendal_43d_clear")
    world(scene, sky, rotation_deg=sun_rotation(sky, 75), strength=1.0)
    maps = texture("sand_01")
    tile = texture_size("sand_01")
    floor = ground("Sand", 1.2, 1200, maps, tile, height=0.006,
                   material=ground_material("Sand", maps, tile))
    stone = append_objects(model("stone_01"), rename="Stone")[0]
    place(stone, (0.0, 0.0, 0.0), (6, -10, 40), sink=0.45)
    return floor, [stone], {"cams": [
        ("hero", (0.23, -0.31, 0.055), (0.0, 0.0, 0.012), 100, 3.5),
        ("compare", (0.23, -0.31, 0.055), (0.0, 0.0, 0.012), 100, 3.5)]}


TUNING = {
    # Applied after the operator's fitted distances, as an artist would after a first look.
    # "objects" are each object's own settings; "ground" lives on the shared ground.
    "forest": dict(objects={}, ground={"Contact Subdivisions": 2}, breakup=(0.55, 0.35, 6.0)),
    "boulders": dict(objects={}, ground={"Contact Subdivisions": 2}, breakup=(0.5, 0.3, 4.0)),
    "macro": dict(objects={"Contact Distance": 0.012, "Material Blend Distance": 0.006,
                           "Normal Blend Distance": 0.006},
                  ground={"Contact Subdivisions": 2, "Ground Detail": 0.0015, "Detail Midlevel": 0.5,
                          "Detail UV Map": "DispUV", "Detail Image": ("sand_01", "disp")},
                  breakup=(0.6, 0.4, 60.0)),
}


def build(name, args):
    print(f"== {name}", flush=True)
    floor, rocks, extra = SCENES[name](args)
    scene = bpy.context.scene
    cams = {}
    for cam_name, loc, target, lens, fstop in extra["cams"]:
        cams[cam_name] = camera(scene, f"{name} {cam_name}", loc, target, lens, fstop)
    render_settings(scene, args.samples, preview=args.preview)
    RENDERS.mkdir(parents=True, exist_ok=True)
    suffix = "_preview" if args.preview else ""
    if not args.no_render:
        render(scene, cams["compare"], RENDERS / f"{name}_before{suffix}.jpg")
    proxy = blend_into(floor, rocks)
    tuning = TUNING[name]
    for rock in rocks:
        mod = cb.find_mod(rock)
        for socket_name, value in tuning["objects"].items():
            assert cb.set_socket(mod, socket_name, value), socket_name
        rock.update_tag()
        breakup(rock, *tuning["breakup"])
    ground_settings = dict(tuning["ground"])
    if "Detail Image" in ground_settings:
        asset, kind = ground_settings["Detail Image"]
        ground_settings["Detail Image"] = _image(texture(asset)[kind], False)
    tune_ground(proxy, **ground_settings)
    bpy.context.view_layer.update()
    bpy.ops.file.pack_all()
    blend_path = HERE / f"{name}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), compress=True)
    print(f"saved {blend_path.name}: {blend_path.stat().st_size / 1e6:.1f} MB", flush=True)
    if not args.no_render:
        after = RENDERS / f"{name}_after{suffix}.jpg"
        render(scene, cams["compare"], after)
        render(scene, cams["hero"], RENDERS / f"{name}_hero{suffix}.jpg")
        composite(RENDERS / f"{name}_before{suffix}.jpg", after, RENDERS / f"{name}_compare{suffix}.jpg")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scene", choices=sorted(SCENES), action="append")
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--samples", type=int, default=256)
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

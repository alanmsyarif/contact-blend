"""Build the installable Contact Blend extension: dist/contact_blend-<version>.zip

    python tools/build_dist.py

The zip is a Blender extension (Blender 5.0+): install it with
Edit > Preferences > Get Extensions > Install from Disk, or drag it into
the Blender window.  It needs nothing but the Python standard library.
"""
import ast
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "contact_blend.py"
PACKAGE_ID = "contact_blend"


def read_bl_info():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "bl_info":
            return ast.literal_eval(node.value)
    raise SystemExit("bl_info not found in contact_blend.py")


def manifest(info):
    version = ".".join(str(v) for v in (tuple(info["version"]) + (0, 0))[:3])
    blender = ".".join(str(v) for v in info["blender"])
    licence = info["license"].split(":", 1)[-1]
    tagline = "Sink objects into a ground without booleans"
    return version, "\n".join([
        'schema_version = "1.0.0"',
        f'id = "{PACKAGE_ID}"',
        f'version = "{version}"',
        f'name = "{info["name"]}"',
        f'tagline = "{tagline}"',
        f'maintainer = "{info["author"]}"',
        'type = "add-on"',
        'tags = ["Object", "Geometry Nodes", "Material"]',
        f'blender_version_min = "{blender}"',
        f'license = ["SPDX:{licence}"]',
        "",
    ])


def main():
    version, text = manifest(read_bl_info())
    out = ROOT / "dist" / f"{PACKAGE_ID}-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    stamp = (2026, 1, 1, 0, 0, 0)          # fixed, so rebuilds are byte-identical
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (
            ("__init__.py", SOURCE.read_bytes()),
            ("blender_manifest.toml", text.encode()),
            ("LICENSE", (ROOT / "LICENSE").read_bytes()),
        ):
            z.writestr(zipfile.ZipInfo(name, stamp), data, zipfile.ZIP_DEFLATED)
    print(f"{out.relative_to(ROOT)}  {out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

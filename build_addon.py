"""
Packs the Blender add-on (extension, Blender 4.2+) into an installable .zip:

    python build_addon.py            -> blender_addon/io_recolove.zip

The zip contains blender_addon/io_recolove/__init__.py + copies of the core
(fpac.py, fedmodel.py, gxt.py, texfile.py). Run it again after changing any
of those files - the copies inside the zip do not update by themselves.

Install: Blender > Edit > Preferences > Get Extensions > (v menu, top right)
> Install from Disk... > pick io_recolove.zip
(or use the "Install Blender add-on" button of the RecoLove Toolkit).
"""
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "blender_addon", "io_recolove")
OUT = os.path.join(HERE, "blender_addon", "io_recolove.zip")
CORE = ["fpac.py", "fedmodel.py", "gxt.py", "texfile.py"]

MANIFEST = """schema_version = "1.0.0"
id = "io_recolove"
version = "{version}"
name = "RecoLove FED/TEX"
tagline = "Import and export RecoLove PS Vita models and textures"
maintainer = "RecoLove Modding Toolkit"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
"""


def addon_version():
    with open(os.path.join(PKG, "__init__.py"), encoding="utf-8") as f:
        m = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', f.read())
    return ".".join(m.groups()) if m else "1.0.0"


def build(out=OUT):
    version = addon_version()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("blender_manifest.toml", MANIFEST.format(version=version))
        z.write(os.path.join(PKG, "__init__.py"), "__init__.py")
        for name in CORE:
            z.write(os.path.join(HERE, name), name)
    print("OK: %s (version %s)" % (out, version))
    return out


if __name__ == "__main__":
    build()

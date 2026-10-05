"""
Blender integration:
  * find_blender()  - locates blender.exe (4.2+)
  * launch()        - opens Blender with the RecoLove add-on loaded (no
                      install needed) and a project imported, ready for
                      "Save to project"
  * install_addon() - installs/updates the add-on permanently in Blender
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_ZIP_NAME = "io_recolove.zip"


def _bundle_dir():
    """Folder with bundled data (PyInstaller) or the source folder."""
    return getattr(sys, "_MEIPASS", HERE)


def addon_zip_path():
    """The add-on .zip: bundled in the exe, or built from source."""
    p = os.path.join(_bundle_dir(), ADDON_ZIP_NAME)
    if os.path.isfile(p):
        return p
    p = os.path.join(HERE, "blender_addon", ADDON_ZIP_NAME)
    if not os.path.isfile(p):
        import build_addon
        build_addon.build()
    return p


def addon_parent_dir():
    """Folder that contains an importable 'io_recolove' package."""
    if not getattr(sys, "frozen", False):
        return os.path.join(HERE, "blender_addon")  # source tree (imports the core from recolove_tools)
    base = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "RecoLoveToolkit", "addon")
    pkg = os.path.join(base, "io_recolove")
    os.makedirs(pkg, exist_ok=True)
    with zipfile.ZipFile(addon_zip_path()) as z:
        z.extractall(pkg)
    return base


def find_blender(preferred=None):
    """blender.exe path: the configured one (if it exists) or the newest
    version installed in Program Files (>= 4.2, required by the add-on)."""
    if preferred and os.path.isfile(preferred):
        return preferred
    cands = []
    roots = {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files"),
             r"C:\Program Files"}
    for root in roots:
        for exe in glob.glob(os.path.join(root, "Blender Foundation", "*", "blender.exe")):
            m = re.search(r"(\d+)\.(\d+)", os.path.basename(os.path.dirname(exe)))
            ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            cands.append((ver, exe))
    steam = glob.glob(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
    cands += [((9, 9), s) for s in steam]
    cands.sort(reverse=True)
    for ver, exe in cands:
        if ver >= (4, 2):
            return exe
    return None


_SCRIPT = r'''
import sys, bpy
sys.path.insert(0, {addon_parent!r})
if not hasattr(bpy.types, "IMPORT_OT_recolove_fed"):
    import io_recolove
    try:
        io_recolove.register()
    except Exception as e:
        print("[RecoLove] register:", e)
fed = {fed!r}
if fed:
    def _do():
        try:
            for o in list(bpy.data.objects):  # start from an empty scene
                if o.name in ("Cube", "Light", "Camera"):
                    bpy.data.objects.remove(o)
            bpy.ops.import_scene.recolove_fed(filepath=fed, tex_path={tex!r}, save_fed={save_fed!r},
                                              save_tex={save_tex!r})
        except Exception as e:
            print("[RecoLove] import error:", e)
        return None
    bpy.app.timers.register(_do, first_interval=0.5)
'''


def launch(blender_exe, fed_path=None, tex_path="", save_fed="", save_tex=""):
    exe = find_blender(blender_exe)
    if not exe:
        raise RuntimeError("Blender 4.2 or newer was not found.\nInstall Blender (blender.org) or set the "
                           "path of blender.exe in the settings.")
    script = os.path.join(tempfile.gettempdir(), "recolove_blender_start.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_SCRIPT.format(addon_parent=addon_parent_dir(), fed=fed_path or "", tex=tex_path or "",
                               save_fed=save_fed or "", save_tex=save_tex or ""))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([exe, "--python", script], creationflags=flags)


_INSTALL = r'''
import bpy, addon_utils
for legacy in ("io_recolove_fed",):
    try:
        bpy.ops.preferences.addon_disable(module=legacy)
    except Exception:
        pass
res = bpy.ops.extensions.package_install_files(filepath={zip!r}, repo="user_default", enable_on_install=True)
bpy.ops.wm.save_userpref()
ok = any(m.endswith("io_recolove") for m in bpy.context.preferences.addons.keys())
print("RECOLOVE_INSTALL", res, "ENABLED" if ok else "NOT_ENABLED")
'''


def install_addon(blender_exe):
    """Installs/updates the add-on as a Blender extension. Returns (ok, log)."""
    exe = find_blender(blender_exe)
    if not exe:
        raise RuntimeError("Blender 4.2 or newer was not found.")
    script = os.path.join(tempfile.gettempdir(), "recolove_blender_install.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_INSTALL.format(zip=addon_zip_path()))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([exe, "-b", "--python", script], capture_output=True, text=True, timeout=300,
                       creationflags=flags)
    out = (r.stdout or "") + (r.stderr or "")
    ok = "RECOLOVE_INSTALL" in out and "ENABLED" in out and "NOT_ENABLED" not in out
    return ok, out

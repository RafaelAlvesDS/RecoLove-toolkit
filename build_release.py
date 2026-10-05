"""
Builds the shareable release:

    python build_release.py [output_folder]

  -> <output_folder>/RecoLoveToolkit.exe              single-file app (GUI + command line)
     <output_folder>/RecoLove_Blender_addon.zip       the Blender add-on (also inside the exe)
     <output_folder>/README.txt                       how to use
     <output_folder>/RecoLoveToolkit_source.zip       the Python source

Default output folder: <Desktop>/RecoLove Toolkit. Requires PyInstaller
(python -m pip install pyinstaller) and Pillow (only for the icon).
"""
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = ["recolove_toolkit.py", "app.py", "cli.py", "helptext.py", "workspace.py", "cpkfile.py", "fpac.py",
           "fedmodel.py", "gxt.py", "texfile.py", "glb.py", "png_io.py", "quickrender.py", "tmc.py", "doa_swap.py",
           "blender_launch.py", "build_addon.py", "build_release.py"]


def make_icon(path):
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):  # vertical gradient pink -> purple
        t = y / size
        c = (int(236 - 90 * t), int(72 + 10 * t), int(153 + 60 * t), 255)
        d.line([(0, y), (size, y)], fill=c)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8, 8, size - 8, size - 8], radius=56, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 120)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 - 6), "RL", font=font, fill=(255, 255, 255, 255), anchor="mm")
    d.ellipse([170, 170, 214, 214], fill=(255, 255, 255, 235))  # little "heart" dot
    out.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def readme_text():
    import helptext
    parts = ["%s %s" % (helptext.APP_NAME, helptext.VERSION), "=" * 40, "",
             "Double-click RecoLoveToolkit.exe. Everything you need is explained in its Help button.",
             "This file is a copy of that help.", ""]
    for title, text in helptext.TABS:
        parts += ["", "-" * 70, title.upper(), "-" * 70, text]
    parts += ["", "-" * 70, "FILES", "-" * 70,
              "RecoLoveToolkit.exe          the tool (no installation; it creates a 'RecoLoveToolkit_Workspace'",
              "                             folder next to itself for your projects and builds)",
              "RecoLove_Blender_addon.zip   optional manual install of the Blender add-on",
              "                             (Blender > Edit > Preferences > Get Extensions > Install from Disk)",
              "RecoLoveToolkit_source.zip   Python source (Python 3.9+ with numpy; run recolove_toolkit.py)", ""]
    return "\n".join(parts)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Desktop", "RecoLove Toolkit")
    sys.path.insert(0, HERE)
    import build_addon
    build = os.path.join(HERE, "_build")
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    addon = build_addon.build()
    icon = os.path.join(build, "toolkit.ico")
    make_icon(icon)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "RecoLoveToolkit", "--icon", icon,
           "--add-data", "%s%s." % (addon, os.pathsep),
           "--add-data", "%s%s." % (icon, os.pathsep),
           "--distpath", os.path.join(build, "dist"), "--workpath", os.path.join(build, "work"),
           "--specpath", build,
           "--exclude-module", "PIL", "--exclude-module", "matplotlib", "--exclude-module", "scipy",
           "--exclude-module", "pandas", "--exclude-module", "IPython",
           os.path.join(HERE, "recolove_toolkit.py")]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=HERE)

    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(os.path.join(build, "dist", "RecoLoveToolkit.exe"), os.path.join(out_dir, "RecoLoveToolkit.exe"))
    shutil.copyfile(addon, os.path.join(out_dir, "RecoLove_Blender_addon.zip"))
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(readme_text())
    with zipfile.ZipFile(os.path.join(out_dir, "RecoLoveToolkit_source.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for name in SOURCES:
            z.write(os.path.join(HERE, name), "recolove_tools/" + name)
        z.write(os.path.join(HERE, "blender_addon", "io_recolove", "__init__.py"),
                "recolove_tools/blender_addon/io_recolove/__init__.py")
        z.write(os.path.join(HERE, "README.md"), "recolove_tools/README.md")
    shutil.rmtree(build, ignore_errors=True)
    print("\nRelease ready in: %s" % out_dir)
    for n in sorted(os.listdir(out_dir)):
        print("  %-32s %8.1f MB" % (n, os.path.getsize(os.path.join(out_dir, n)) / 1e6))


if __name__ == "__main__":
    main()

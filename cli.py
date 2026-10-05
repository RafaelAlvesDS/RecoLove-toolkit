#!/usr/bin/env python3
"""
RecoLove Modding Toolkit - command line.

  list        <game_dir> [search words]          list the models (id, character, part, costume)
  extract     <game_dir> <id|name> <out_dir>     extract a model .fed + its .tex from the game
  info        <fed> [--tex T]                    slots, bones, materials -> textures
  export-glb  <fed> <out.glb> [--tex T]          glTF with skeleton, weights, morphs, textures
  render      <fed> <out.png> [--tex T] [--view xy|-xy|zy|xz]
  unpack-tex  <tex> <png_dir>                    tex_NN.png (NN = texture slot)
  repack-tex  <tex> <png_dir> <out.tex>          re-encodes only the edited PNGs
  swap-body   <fed> <tex> <DOA.TMC> <out_dir> [--slot N] [--material N|new] [--hide 1,2] [--tex-size 1024] [--doa-hands]
  set-material <fed> <slot> <part> <material> <out.fed>
  hide-slot   <fed> <slot>[,<slot>...] <out.fed>
  build-cpk   <game_dir> <out_dir> <file> [<file> ...]
                                                 builds CharaModel.cpk/CharaTex.cpk replacing the
                                                 given files (names must start with the 4-digit id,
                                                 e.g. 0085_mdb_02mar_000_0_S_def_00.fed)
  blender     <fed> [--tex T]                    opens Blender with the model imported
  install-addon                                  installs the Blender add-on
  save-addon  <out.zip>                          saves the Blender add-on .zip

Run without arguments to open the graphical interface.
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _tex_for(fed_path, tex):
    import texfile
    if tex:
        return tex
    here = os.path.dirname(os.path.abspath(fed_path))
    for d in (here, os.path.join(os.path.dirname(here), "CharaTex_out")):
        c = texfile.find_tex_candidates(fed_path, d)
        if c:
            return c[0]
    return None


def cmd_list(a):
    import workspace
    g = workspace.Game(a.game)
    words = [w.lower() for w in a.words]
    for e in g.models:
        text = " ".join((e.character, e.part, e.costume, e.basename)).lower()
        if all(w in text for w in words):
            print("%04d  %-22s %-9s %-28s %s" % (e.id, e.character, e.part, e.costume, e.basename))


def cmd_extract(a):
    import workspace
    g = workspace.Game(a.game)
    key = a.model.lower()
    hits = [e for e in g.models if key in (str(e.id), "%04d" % e.id, e.name.lower(), e.filename.lower())]
    if not hits:
        raise SystemExit("model not found: %s (use 'list')" % a.model)
    e = hits[0]
    os.makedirs(a.out_dir, exist_ok=True)
    out = [(e, os.path.join(a.out_dir, e.filename))]
    t = g.textures_for(e)
    if t:
        out.append((t[0], os.path.join(a.out_dir, t[0].filename)))
    for ent, path in out:
        with open(path, "wb") as f:
            f.write(g.read(ent))
        print("OK:", path)


def cmd_info(a):
    import fedmodel
    import texfile
    m = fedmodel.Model.load(a.fed)
    print(m.summary())
    tex_path = _tex_for(a.fed, a.tex)
    tex = texfile.TexFile.load(tex_path) if tex_path else None
    print("\n.tex: %s" % (tex_path or "(not found - use --tex)"))
    for mat in m.materials:
        slot = mat.texture_slot(tex)
        extra = ""
        if slot is not None and tex is not None and tex.has(slot):
            i = tex.info(slot)
            extra = " (%dx%d)" % (i.width, i.height)
        print("  material %2d  shader=%d color=#%02x%02x%02x%02x texture=%s%s" % (
            mat.index, mat.shader, mat.color[0], mat.color[1], mat.color[2], mat.color[3],
            ("slot %d" % slot) if slot is not None else "-", extra))


def cmd_export_glb(a):
    import fedmodel
    import glb
    import texfile
    tex_path = _tex_for(a.fed, a.tex)
    n = glb.export_model_glb(fedmodel.Model.load(a.fed), a.out, texfile.TexFile.load(tex_path) if tex_path else None,
                             texfile.strip_index(a.fed))
    print("OK: %s (%d bytes, textures: %s)" % (a.out, n, os.path.basename(tex_path) if tex_path else "none"))


def cmd_render(a):
    import fedmodel
    import glb
    import quickrender
    import texfile
    tex_path = _tex_for(a.fed, a.tex)
    W, H, img = quickrender.render_model(fedmodel.Model.load(a.fed), texfile.TexFile.load(tex_path) if tex_path else None,
                                         a.view, width=700)
    with open(a.out, "wb") as f:
        f.write(glb.png_bytes(img))
    print("OK: %s (%dx%d)" % (a.out, W, H))


def cmd_unpack_tex(a):
    import texfile
    for s in texfile.unpack_pngs(a.tex, a.out_dir):
        print("  slot %2d -> %s" % (s["slot"], ("%s (%dx%d)" % (s["png"], s["width"], s["height"])) if s["png"] else "(empty)"))
    print("OK:", a.out_dir)


def cmd_repack_tex(a):
    import texfile
    changed = texfile.repack_pngs(a.tex, a.png_dir, a.out)
    print("OK: %d texture(s) re-encoded %s -> %s" % (len(changed), changed, a.out))


def cmd_swap_body(a):
    import doa_swap
    hide = [int(x) for x in a.hide.split(",") if x.strip()] if a.hide is not None else None
    mat = a.material if a.material in (None, "new") else int(a.material)
    os.makedirs(a.out_dir, exist_ok=True)
    doa_swap.swap_body(a.fed, a.tex, a.tmc, os.path.join(a.out_dir, os.path.basename(a.fed)),
                       os.path.join(a.out_dir, os.path.basename(a.tex)), a.slot, mat, hide, a.tex_size, a.object,
                       doa_hands=a.doa_hands)


def cmd_set_material(a):
    import fedmodel
    m = fedmodel.Model.load(a.fed)
    old = m.set_submesh_material(a.slot, a.part, a.material)
    m.save(a.out)
    print("OK: slot %d part %d material %d -> %d (%s)" % (a.slot, a.part, old, a.material, a.out))


def cmd_hide_slot(a):
    import fedmodel
    m = fedmodel.Model.load(a.fed)
    for s in a.slots.split(","):
        m.hide_slot(int(s))
    m.save(a.out)
    print("OK: hidden slots %s (%s)" % (a.slots, a.out))


def cmd_build_cpk(a):
    import workspace
    g = workspace.Game(a.game)
    os.makedirs(a.out_dir, exist_ok=True)
    groups = {}
    for f in a.files:
        base = os.path.basename(f)
        try:
            fid = int(base[:4])
        except ValueError:
            raise SystemExit("%s: the name must start with the 4-digit id (e.g. 0085_...)" % base)
        archive = workspace.MODEL_CPK if base.lower().endswith(".fed") else workspace.TEX_CPK
        with open(f, "rb") as fh:
            groups.setdefault(archive, {})[fid] = fh.read()
    for archive, files in groups.items():
        out = os.path.join(a.out_dir, archive + ".cpk")
        g.cpk(archive).replace(files, out)
        print("OK: %s (%d file(s) replaced)" % (out, len(files)))


def cmd_blender(a):
    import blender_launch
    blender_launch.launch(None, os.path.abspath(a.fed), _tex_for(a.fed, a.tex) or "")
    print("Blender started.")


def cmd_install_addon(a):
    import blender_launch
    ok, out = blender_launch.install_addon(None)
    print(out[-1500:])
    print("OK: add-on installed" if ok else "Could not confirm the installation (see above)")


def cmd_save_addon(a):
    import blender_launch
    shutil.copyfile(blender_launch.addon_zip_path(), a.out)
    print("OK:", a.out)


def build_parser():
    p = argparse.ArgumentParser(prog="RecoLoveToolkit", description="RecoLove Modding Toolkit (command line)",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, *args, **opts):
        sp = sub.add_parser(name)
        for x in args:
            if isinstance(x, tuple):
                sp.add_argument(x[0], **x[1])
            else:
                sp.add_argument(x)
        sp.set_defaults(func=func)
        return sp

    add("list", cmd_list, "game", ("words", {"nargs": "*"}))
    add("extract", cmd_extract, "game", "model", "out_dir")
    add("info", cmd_info, "fed", ("--tex", {"default": None}))
    add("export-glb", cmd_export_glb, "fed", "out", ("--tex", {"default": None}))
    add("render", cmd_render, "fed", "out", ("--tex", {"default": None}), ("--view", {"default": "xy"}))
    add("unpack-tex", cmd_unpack_tex, "tex", "out_dir")
    add("repack-tex", cmd_repack_tex, "tex", "png_dir", "out")
    add("swap-body", cmd_swap_body, "fed", "tex", "tmc", "out_dir", ("--slot", {"type": int, "default": None}),
        ("--material", {"default": None}), ("--hide", {"default": None}), ("--tex-size", {"type": int, "default": 1024}),
        ("--object", {"default": None}), ("--doa-hands", {"action": "store_true"}))
    add("set-material", cmd_set_material, "fed", ("slot", {"type": int}), ("part", {"type": int}),
        ("material", {"type": int}), "out")
    add("hide-slot", cmd_hide_slot, "fed", "slots", "out")
    add("build-cpk", cmd_build_cpk, "game", "out_dir", ("files", {"nargs": "+"}))
    add("blender", cmd_blender, "fed", ("--tex", {"default": None}))
    add("install-addon", cmd_install_addon)
    add("save-addon", cmd_save_addon, "out")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

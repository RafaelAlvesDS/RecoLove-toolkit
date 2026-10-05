"""
RecoLove .tex file = FPACTEX container (see fpac.py) with N indexed SLOTS;
each slot is a GXT texture (see gxt.py) or EMPTY.

The slot index is exactly the texture index used by the materials of the
.fed (FPACMAT, see fedmodel.Material). Empty slots exist on purpose: when a
material's `texA` slot is empty the game uses `texB` (validated on all 2005
materials of every .fed/.tex pair in the game).
"""
import os
import re

try:
    from . import fpac
except ImportError:
    import fpac
try:
    from . import gxt
except ImportError:
    import gxt


class TexFile(object):
    def __init__(self, data):
        self.root = fpac.parse(data)
        if self.root.name != "FPACTEX":
            raise ValueError("not an FPACTEX file (found %s)" % self.root.name)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    @property
    def slots(self):
        return self.root.children

    def __len__(self):
        return len(self.root.children)

    def has(self, i):
        return 0 <= i < len(self.slots) and bool(self.slots[i])

    def info(self, i):
        return gxt.read_info(self.slots[i])

    def decode(self, i):
        """-> (w, h, numpy uint8 [h,w,4] RGBA, row 0 = top)"""
        return gxt.decode(self.slots[i])

    def replace_rgba(self, i, rgba, width, height):
        """Encodes and replaces slot i. The size may change (the container is
        rewritten); if it is the same the original GXT header is kept."""
        old = self.slots[i] if self.has(i) else None
        self.root.children[i] = gxt.encode(rgba, width, height, header_template=old)

    def to_bytes(self):
        return fpac.serialize(self.root)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())


# ---------------------------------------------------------------------
# .tex <-> folder of PNGs (for Photoshop & co.)
# ---------------------------------------------------------------------

MANIFEST = "manifest.json"


def png_name(slot):
    return "tex_%02d.png" % slot


def unpack_pngs(tex_path, out_dir):
    """Extracts every slot as tex_NN.png (NN = slot index = the index the
    materials use) + manifest.json with a hash of the original pixels, so
    we know later which PNGs were edited."""
    import hashlib
    import json
    try:
        from . import glb
    except ImportError:
        import glb
    tex = TexFile.load(tex_path)
    os.makedirs(out_dir, exist_ok=True)
    slots = []
    for i in range(len(tex)):
        if not tex.has(i):
            slots.append({"slot": i, "png": None})
            continue
        w, h, rgba = tex.decode(i)
        name = png_name(i)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(glb.png_bytes(rgba))
        slots.append({"slot": i, "png": name, "width": w, "height": h,
                      "sha1": hashlib.sha1(rgba.tobytes()).hexdigest()})
    with open(os.path.join(out_dir, MANIFEST), "w") as f:
        json.dump({"source_tex": os.path.abspath(tex_path), "slots": slots}, f, indent=2)
    return slots


def repack_pngs(tex_path, png_dir, out_path):
    """Re-encodes ONLY the PNGs whose pixels changed since unpack_pngs (DXT5
    is lossy - re-encoding untouched images would only degrade them).
    Returns the list of slots that were rewritten."""
    import hashlib
    import json
    import numpy as np
    try:
        from . import png_io
    except ImportError:
        import png_io
    tex = TexFile.load(tex_path)
    man_path = os.path.join(png_dir, MANIFEST)
    known = {}
    if os.path.isfile(man_path):
        with open(man_path, "r") as f:
            for s in json.load(f).get("slots", []):
                if s.get("png"):
                    known[s["png"]] = s.get("sha1")
    changed = []
    for i in range(len(tex)):
        path = os.path.join(png_dir, png_name(i))
        if not os.path.isfile(path):
            continue
        w, h, data = png_io.read_png_rgba(path)
        rgba = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 4)
        digest = hashlib.sha1(rgba.tobytes()).hexdigest()
        if known.get(png_name(i)) == digest:
            continue
        if not known.get(png_name(i)) and tex.has(i):
            _w, _h, orig = tex.decode(i)
            if orig.shape == rgba.shape and (orig == rgba).all():
                continue
        tex.replace_rgba(i, rgba, w, h)
        changed.append(i)
    tex.save(out_path)
    return changed


# ---------------------------------------------------------------------
# finding the .tex of a .fed by name
# ---------------------------------------------------------------------

def strip_index(name):
    """'0085_mdb_02mar_000_0_S_def_00.fed' -> 'mdb_02mar_000_0_S_def_00'"""
    base = os.path.splitext(os.path.basename(name))[0]
    m = re.match(r"^\d{4}_(.*)$", base)
    return m.group(1) if m else base


def _key(name):
    """Comparison key: no numeric index and the S/G field (5th) ignored -
    the .fed may be 'S' and the .tex 'G' for the same costume
    (e.g. mdb_02mar_000_0_S_def_00.fed <-> mdb_02mar_000_0_G_def_00.tex)."""
    parts = strip_index(name).split("_")
    if len(parts) >= 6:
        parts[4] = "?"
    return "_".join(parts)


def rank_tex_names(fed_name, tex_names):
    """Candidate .tex names for a .fed, best first:
       1. same name (ignoring index and S/G);
       2. color variants of the same costume (e.g. mib_00.fed -> mib_01..13.tex);
       3. extras with a suffix (_1, _2 - alternative head textures)."""
    parts = strip_index(fed_name).split("_")
    if len(parts) > 7:  # e.g. mdb_02mar_000_0_S_def_00_mod.fed -> ignore the suffix
        fed_name = "_".join(parts[:7])
    key = _key(fed_name)
    kparts = key.split("_")
    costume_prefix = "_".join(kparts[:-1]) if len(kparts) >= 2 else key
    exact, extra, variant = [], [], []
    for n in sorted(tex_names):
        if not n.lower().endswith(".tex"):
            continue
        k = _key(n)
        if k == key:
            exact.append(n)
        elif k.startswith(key + "_"):
            extra.append(n)
        elif k.startswith(costume_prefix + "_"):
            variant.append(n)
    return exact + variant + extra


def find_tex_candidates(fed_path, tex_dir):
    """Same as rank_tex_names, looking inside a folder (full paths)."""
    if not tex_dir or not os.path.isdir(tex_dir):
        return []
    return [os.path.join(tex_dir, n) for n in rank_tex_names(fed_path, os.listdir(tex_dir))]

"""
Toolkit back-end (used by the GUI and the CLI): reads the game directly from
its .cpk archives, keeps one PROJECT folder per edited model and builds the
final mod .cpk files.

Game folder = the decrypted RecoLove folder (e.g. PCSG00782) containing
media/cpk/CharaModel.cpk + CharaTex.cpk and media/afs/*.als (the .als files
list the real name of every file inside the .cpk, one per line, line N = ID N).

Workspace layout:
    <workspace>/settings.json
    <workspace>/projects/<model name>/
        project.json                 which game files this project edits
        original/<id>_<name>.fed     untouched copies from the game
        original/<id>_<name>.tex
        <id>_<name>.fed              WORKING copies (your edits accumulate here)
        <id>_<name>.tex
        textures/tex_NN.png          textures for Photoshop & co. (NN = slot)
    <workspace>/output/CharaModel.cpk, CharaTex.cpk   the mod (all edited projects)
"""
import json
import os
import re
import shutil

try:
    from . import cpkfile, fedmodel, texfile
except ImportError:
    import cpkfile
    import fedmodel
    import texfile

MODEL_CPK = "CharaModel"
TEX_CPK = "CharaTex"


class GameError(Exception):
    pass


# ---------------------------------------------------------------------
# game source
# ---------------------------------------------------------------------

def resolve_game_dir(path):
    """Accepts the game folder, its 'media' folder or the 'cpk' folder and
    returns (cpk_dir, afs_dir)."""
    if not path:
        raise GameError("No game folder selected.")
    cands = [path, os.path.join(path, "media"), os.path.dirname(path)]
    for c in cands:
        cpk_dir = os.path.join(c, "cpk")
        afs_dir = os.path.join(c, "afs")
        if os.path.isfile(os.path.join(cpk_dir, MODEL_CPK + ".cpk")):
            return cpk_dir, afs_dir
    if os.path.isfile(os.path.join(path, MODEL_CPK + ".cpk")):
        return path, os.path.join(os.path.dirname(path), "afs")
    raise GameError("CharaModel.cpk not found in '%s'.\nSelect the decrypted RecoLove game folder "
                    "(the one that contains the 'media' folder)." % path)


def find_game_dir(search_roots):
    """Looks for a game folder near the given folders (first run auto-detect)."""
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            names = [root] + [os.path.join(root, n) for n in os.listdir(root)]
        except OSError:
            continue
        for c in names:
            if os.path.isfile(os.path.join(c, "media", "cpk", MODEL_CPK + ".cpk")):
                return c
    return ""


_CHAR_RE = re.compile(r"^\d+_(chibi_)?(.+)$")

# game folder name (00_chara/NN_<name>) -> full character name
CHARACTER_NAMES = {
    "sagara": "Miu Sagara",
    "mariana": "Mariana Priscila",
    "sorimachi": "Riko Sorimachi",
    "misaki": "Nagisa Misaki",
    "naruse": "Mahiro Naruse",
    "himeragi": "Rinze Himeragi",
    "aida": "Miyuri Aida",
    "uchima": "Yuuko Uchima",
    "mana": "Mana Iori",
    "yuina": "Yuina Iori",
    "isuzu": "Kokomi Isuzu",
    "abumi": "Kazushi Iori",
    "dohmoto": "Seya Doumoto",
    "nakai": "Kazuma Nakai",
    "nishi": "Takahito Hishi",
}


class Entry(object):
    """One file inside a .cpk."""

    def __init__(self, archive, file_id, als_path):
        self.archive = archive          # "CharaModel" / "CharaTex"
        self.id = file_id
        self.als_path = als_path
        self.basename = os.path.basename(als_path)
        self.filename = "%04d_%s" % (file_id, self.basename)   # same naming as the old tools
        parts = als_path.replace("\\", "/").split("/")
        self.character = self.part = self.costume = ""
        if "00_chara" in parts:
            i = parts.index("00_chara")
            if i + 1 < len(parts):
                m = _CHAR_RE.match(parts[i + 1])
                if m:
                    key = m.group(2)
                    name = CHARACTER_NAMES.get(key, key.replace("_", " ").title())
                    self.character = name + (" (chibi)" if m.group(1) else "")
            if i + 2 < len(parts):
                self.part = {"01_h_model": "Head", "02_b_model": "Body", "00_model": "Model"}.get(parts[i + 2], parts[i + 2])
            if i + 3 < len(parts) - 1:
                c = parts[i + 3].split("_")
                self.costume = ("%s %s" % (c[0], " ".join(c[3:]))).strip() + (" (v%s)" % c[1] if len(c) > 1 and c[1] != "0" else "")
        if "_col_" in self.basename:
            self.part = "Collision"
        self.name = os.path.splitext(self.basename)[0]

    def __repr__(self):
        return "<%s %04d %s>" % (self.archive, self.id, self.basename)


class Game(object):
    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.cpk_dir, self.afs_dir = resolve_game_dir(game_dir)
        self._cpk = {}
        self.models = self._entries(MODEL_CPK, ".fed")
        self.textures = self._entries(TEX_CPK, ".tex")
        self._tex_by_name = {e.filename: e for e in self.textures}

    def cpk_path(self, archive):
        return os.path.join(self.cpk_dir, archive + ".cpk")

    def cpk(self, archive):
        if archive not in self._cpk:
            self._cpk[archive] = cpkfile.Cpk(self.cpk_path(archive))
        return self._cpk[archive]

    def _entries(self, archive, ext):
        als = os.path.join(self.afs_dir, archive + ".als")
        if not os.path.isfile(als):
            raise GameError("%s not found - the game folder looks incomplete." % als)
        out = []
        for i, line in enumerate(cpkfile.read_als(als)):
            if line.lower().endswith(ext):
                out.append(Entry(archive, i, line))
        return out

    def read(self, entry):
        c = self.cpk(entry.archive)
        return c.read(c.find(entry.id))

    def textures_for(self, model_entry):
        """Candidate .tex entries for a model, best first."""
        names = texfile.rank_tex_names(model_entry.filename, list(self._tex_by_name))
        return [self._tex_by_name[n] for n in names]


# ---------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------

class Project(object):
    def __init__(self, folder):
        self.folder = folder
        with open(os.path.join(folder, "project.json"), "r") as f:
            self.info = json.load(f)
        self.name = self.info["name"]

    # files
    @property
    def fed_path(self):
        return os.path.join(self.folder, self.info["fed"]["filename"])

    @property
    def tex_path(self):
        t = self.info.get("tex")
        return os.path.join(self.folder, t["filename"]) if t else None

    def original(self, key):
        d = self.info.get(key)
        return os.path.join(self.folder, "original", d["filename"]) if d else None

    @property
    def textures_dir(self):
        return os.path.join(self.folder, "textures")

    @property
    def has_tex(self):
        return bool(self.info.get("tex"))

    # state
    def changed(self, key):
        d = self.info.get(key)
        if not d:
            return False
        a = os.path.join(self.folder, d["filename"])
        b = self.original(key)
        return os.path.getsize(a) != os.path.getsize(b) or _read(a) != _read(b)

    def is_modified(self):
        return self.changed("fed") or self.changed("tex")

    def load_model(self):
        return fedmodel.Model.load(self.fed_path)

    def load_tex(self):
        return texfile.TexFile.load(self.tex_path) if self.has_tex else None

    # actions
    def revert(self):
        for key in ("fed", "tex"):
            if self.info.get(key):
                shutil.copyfile(self.original(key), os.path.join(self.folder, self.info[key]["filename"]))
        if os.path.isdir(self.textures_dir):
            shutil.rmtree(self.textures_dir, ignore_errors=True)

    def replace_model(self, path):
        """Uses a .fed exported by Blender (validated before copying)."""
        fedmodel.Model.load(path)
        if os.path.abspath(path) != os.path.abspath(self.fed_path):
            shutil.copyfile(path, self.fed_path)

    def export_textures(self):
        if not self.has_tex:
            raise GameError("This model has no texture file.")
        return texfile.unpack_pngs(self.tex_path, self.textures_dir)

    def import_textures(self):
        if not os.path.isfile(os.path.join(self.textures_dir, texfile.MANIFEST)):
            raise GameError("Export the textures (PNG) first, edit them, then import.")
        tmp = self.tex_path + ".tmp"
        changed = texfile.repack_pngs(self.tex_path, self.textures_dir, tmp)
        os.replace(tmp, self.tex_path)
        if changed:
            texfile.unpack_pngs(self.tex_path, self.textures_dir)  # manifest follows the new state
        return changed


def _read(p):
    with open(p, "rb") as f:
        return f.read()


class Workspace(object):
    def __init__(self, root):
        self.root = root
        os.makedirs(self.projects_dir, exist_ok=True)
        self.settings_path = os.path.join(root, "settings.json")
        self.settings = {}
        if os.path.isfile(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8-sig") as f:
                    self.settings = json.load(f)
            except ValueError:
                self.settings = {}

    @property
    def projects_dir(self):
        return os.path.join(self.root, "projects")

    @property
    def output_dir(self):
        return os.path.join(self.root, "output")

    def save_settings(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def project_folder(self, entry):
        return os.path.join(self.projects_dir, entry.name)

    def has_project(self, entry):
        return os.path.isfile(os.path.join(self.project_folder(entry), "project.json"))

    def open_project(self, game, entry, tex_entry=None):
        """Opens (or creates, extracting the game files) the project of a model."""
        folder = self.project_folder(entry)
        if self.has_project(entry):
            p = Project(folder)
            for key in ("fed", "tex"):  # self-heal deleted working copies
                d = p.info.get(key)
                if d and not os.path.isfile(os.path.join(folder, d["filename"])):
                    shutil.copyfile(p.original(key), os.path.join(folder, d["filename"]))
            return p
        os.makedirs(os.path.join(folder, "original"), exist_ok=True)
        info = {"name": entry.name, "character": entry.character, "part": entry.part, "costume": entry.costume,
                "fed": {"archive": entry.archive, "id": entry.id, "filename": entry.filename, "path": entry.als_path}}
        files = [("fed", entry)]
        if tex_entry is None:
            cands = game.textures_for(entry)
            tex_entry = cands[0] if cands else None
        if tex_entry is not None:
            info["tex"] = {"archive": tex_entry.archive, "id": tex_entry.id, "filename": tex_entry.filename,
                           "path": tex_entry.als_path}
            files.append(("tex", tex_entry))
        for key, e in files:
            data = game.read(e)
            for path in (os.path.join(folder, "original", e.filename), os.path.join(folder, e.filename)):
                with open(path, "wb") as f:
                    f.write(data)
        with open(os.path.join(folder, "project.json"), "w") as f:
            json.dump(info, f, indent=2)
        return Project(folder)

    def projects(self):
        out = []
        if os.path.isdir(self.projects_dir):
            for n in sorted(os.listdir(self.projects_dir)):
                if os.path.isfile(os.path.join(self.projects_dir, n, "project.json")):
                    out.append(Project(os.path.join(self.projects_dir, n)))
        return out

    def modified_files(self):
        """{archive: {id: (path, project name)}} of every changed file."""
        out = {MODEL_CPK: {}, TEX_CPK: {}}
        for p in self.projects():
            for key in ("fed", "tex"):
                d = p.info.get(key)
                if d and p.changed(key):
                    out[d["archive"]][d["id"]] = (os.path.join(p.folder, d["filename"]), p.name)
        return out

    def build_mod(self, game, out_dir=None, log=print):
        """Writes CharaModel.cpk / CharaTex.cpk with EVERY modified file of
        every project. Returns the list of written .cpk paths."""
        out_dir = out_dir or self.output_dir
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for archive, files in self.modified_files().items():
            target = os.path.join(out_dir, archive + ".cpk")
            if not files:
                if os.path.isfile(target):
                    os.remove(target)  # stale file from an older build
                continue
            log("%s.cpk: %d modified file(s): %s" % (archive, len(files), ", ".join(n for _, n in files.values())))
            game.cpk(archive).replace({fid: _read(path) for fid, (path, _n) in files.items()}, target)
            written.append(target)
        if not written:
            raise GameError("Nothing to build - no model or texture was modified yet.")
        with open(os.path.join(out_dir, "HOW TO INSTALL.txt"), "w", encoding="utf-8") as f:
            f.write(INSTALL_TEXT)
        return written


INSTALL_TEXT = """How to install this mod on your PS Vita
=======================================

You need the rePatch reDux0 plugin installed on the Vita (HENkaku/Enso).

1. Copy the .cpk file(s) from this folder to:
      ux0:rePatch/PCSG00782/media/cpk/
   (create the folders if they do not exist; use your game's TITLE ID if it
   is a different version - it is the name of the game folder in ux0:app/)
2. Start the game. Files in rePatch replace the original ones.

To uninstall, delete the .cpk files from ux0:rePatch/PCSG00782/media/cpk/.
"""

"""
RecoLove (PS Vita) - .fed/.tex importer/exporter for Blender 4.2+.

This package is assembled by `recolove_tools/build_addon.py`, which copies
the validated core (fpac.py, fedmodel.py, gxt.py, texfile.py) into it. Do
not edit the copies inside the .zip - edit the originals in recolove_tools/
and run the build again.

Conventions (see fedmodel.py):
  * game: Y up  -> Blender: Z up   (x, y, z) -> (x, -z, y)
  * UV: v_blender = -v_raw
  * game triangles are clockwise relative to the normals -> flipped on
    import and flipped back on export
  * bones are named bone_NNN (NNN = index in FPACTRAN); vertex groups use
    the same names
"""
import hashlib
import os
import struct

import bpy
import numpy as np
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

try:
    from . import fedmodel as fm
    from . import texfile as tf
except ImportError:  # running straight from the source tree (development)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import fedmodel as fm
    import texfile as tf

bl_info = {
    "name": "RecoLove FED/TEX (PS Vita)",
    "author": "RecoLove Modding Toolkit",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export, 3D View > Sidebar (N) > RecoLove",
    "description": "Import/export RecoLove .fed models (skeleton, weights, morphs) and .tex textures",
    "category": "Import-Export",
}

# game (Y-up) -> Blender (Z-up)
AXIS = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
AXIS_INV = AXIS.inverted()

PROP_FED = "recolove_fed_path"
PROP_SLOT = "recolove_slot"
PROP_SIG = "recolove_sig"
PROP_HIDE = "recolove_hide"            # True = hide this mesh in the game
PROP_HIDE_ORIG = "recolove_hide_orig"
PROP_MAT_ID = "recolove_material_id"
PROP_TEX = "recolove_tex_path"
PROP_TEX_SLOT = "recolove_tex_slot"
PROP_SAVE_FED = "recolove_save_fed"    # set when opened by the toolkit: "Save to project" target
PROP_SAVE_TEX = "recolove_save_tex"
UID_ATTR = "recolove_uid"
NRM_ATTR = "recolove_nrm"    # exact original normal per corner (Blender compresses custom normals)
BNRM_ATTR = "recolove_bnrm"  # normal Blender computed at import time (detects user edits)

HELP_LINES = [
    "RecoLove add-on - quick guide",
    "",
    "EDITING",
    "- Each mesh object = one mesh 'slot' of the game file (name ends in _sNN).",
    "- Move, sculpt, add or delete geometry freely (topology can change).",
    "- Weights: vertex groups named bone_NNN. New vertices without any weight",
    "  get the weights of the nearest original vertex automatically.",
    "- Max 8 bone influences per vertex. Apply your modifiers (except Armature)",
    "  before saving - modifiers are NOT applied on export.",
    "- Shape keys named morph_slotNN are the game morphs (hand poses, faces).",
    "- 'Hide in game' removes a mesh from the game (e.g. clothes).",
    "- To use a mesh from another model/game: import it, parent it to the",
    "  armature, give it vertex groups (bone_NNN) and use 'Use for slot'.",
    "",
    "SAVING",
    "- 'Save to project' writes straight into the toolkit project (when Blender",
    "  was opened by the toolkit). Otherwise use 'Export .fed as...'.",
    "- Only meshes you changed are rewritten; the rest stays byte-identical.",
    "- Textures: paint or replace images, then 'Save textures'. DXT5 is lossy,",
    "  only edited images are re-encoded. Sizes must be powers of 2.",
]


def g2b(p):
    return (p[0], -p[2], p[1])


def b2g(p):
    return (p[0], p[2], -p[1])


def _log(op, level, msg):
    print("[RecoLove] %s" % msg)
    if op is not None:
        op.report({level}, msg)


# ---------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------

def guess_tex_dirs(fed_path):
    """Folders where the .tex of a .fed may be."""
    d = os.path.dirname(os.path.abspath(fed_path))
    parent = os.path.dirname(d)
    out = [d]
    for base in (d, parent, os.path.dirname(parent)):
        try:
            for name in sorted(os.listdir(base)):
                full = os.path.join(base, name)
                if os.path.isdir(full) and "tex" in name.lower() and full not in out:
                    out.append(full)
        except OSError:
            pass
    return out


def find_tex_for(fed_path, tex_dir=""):
    dirs = [tex_dir] if tex_dir else []
    dirs += guess_tex_dirs(fed_path)
    for d in dirs:
        c = tf.find_tex_candidates(fed_path, d)
        if c:
            return c[0]
    return None


def image_from_slot(tex, tex_path, slot):
    name = "%s_t%02d" % (tf.strip_index(tex_path), slot)
    img = bpy.data.images.get(name)
    if img is not None and img.get(PROP_TEX) == tex_path:
        return img
    w, h, rgba = tex.decode(slot)
    img = bpy.data.images.new(name, w, h, alpha=True)
    px = (rgba[::-1].astype(np.float32) / 255.0).ravel()  # Blender: row 0 = bottom
    img.pixels.foreach_set(px)
    img.pack()
    img[PROP_TEX] = tex_path
    img[PROP_TEX_SLOT] = slot
    return img


def image_to_rgba(img):
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    rgba = (np.clip(px.reshape(h, w, 4), 0, 1) * 255.0 + 0.5).astype(np.uint8)
    return w, h, np.ascontiguousarray(rgba[::-1])


def build_material(model, mat_index, tex, tex_path, model_name):
    name = "%s_mat%02d" % (model_name, mat_index)
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat[PROP_MAT_ID] = mat_index
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (100, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Roughness"].default_value = 0.8

    fmat = model.materials[mat_index] if mat_index < len(model.materials) else None
    vcol = nt.nodes.new("ShaderNodeVertexColor")
    vcol.layer_name = "Col"
    vcol.location = (-500, -250)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.location = (-150, 50)
    mix_in = {s.identifier: s for s in mix.inputs}
    mix_out = {s.identifier: s for s in mix.outputs}
    mix_in["Factor_Float"].default_value = 1.0
    nt.links.new(mix_out["Result_Color"], bsdf.inputs["Base Color"])
    nt.links.new(vcol.outputs["Color"], mix_in["B_Color"])

    slot = fmat.texture_slot(tex) if (fmat is not None and tex is not None) else None
    if slot is not None and tex is not None and tex.has(slot):
        img = image_from_slot(tex, tex_path, slot)
        texn = nt.nodes.new("ShaderNodeTexImage")
        texn.image = img
        texn.location = (-500, 100)
        nt.links.new(texn.outputs["Color"], mix_in["A_Color"])
        nt.links.new(texn.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat[PROP_TEX] = tex_path
        mat[PROP_TEX_SLOT] = slot
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "DITHERED"
        elif hasattr(mat, "blend_method"):
            mat.blend_method = "HASHED"
    else:
        c = fmat.color if fmat is not None else (255, 255, 255, 255)
        mix_in["A_Color"].default_value = (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)
    return mat


# ---------------------------------------------------------------------
# geometry signature (to know whether the user touched a mesh)
# ---------------------------------------------------------------------

def mesh_signature(obj):
    me = obj.data
    h = hashlib.sha1()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    h.update(np.round(co, 5).tobytes())
    lv = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)
    h.update(lv.tobytes())
    mi = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    h.update(mi.tobytes())
    if me.uv_layers.active:
        uv = np.empty(len(me.loops) * 2, dtype=np.float32)
        me.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 5).tobytes())
    for vg in obj.vertex_groups:
        h.update(vg.name.encode())
    for v in me.vertices:
        for g in v.groups:
            h.update(struct.pack("<If", g.group, round(g.weight, 4)))
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            a = np.empty(len(me.vertices) * 3, dtype=np.float32)
            kb.data.foreach_get("co", a)
            h.update(kb.name.encode())
            h.update(np.round(a, 5).tobytes())
    ca = me.color_attributes.get("Col")
    if ca is not None:
        c = np.empty(len(ca.data) * 4, dtype=np.float32)
        ca.data.foreach_get("color", c)
        h.update(np.round(c, 3).tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------------
# IMPORT
# ---------------------------------------------------------------------

def build_armature(model, name, collection):
    arm = bpy.data.armatures.new(name + "_skel")
    arm.display_type = "STICK"
    obj = bpy.data.objects.new(name + "_skel", arm)
    collection.objects.link(obj)
    obj.show_in_front = True
    world = model.world_matrices()
    mats = [AXIS @ Matrix(w) for w in world]
    children = {}
    for b in model.bones:
        if 0 <= b.parent < len(model.bones):
            children.setdefault(b.parent, []).append(b.index)

    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = []
    for b in model.bones:
        eb = arm.edit_bones.new(b.name)
        head = mats[b.index].to_translation()
        eb.head = head
        # tail: towards the children (visual only). Children that are very far
        # away (mesh/morph nodes sitting at the origin, e.g. the hand node) are ignored.
        seg = None
        if 0 <= b.parent < len(model.bones):
            seg = head - mats[b.parent].to_translation()
        limit = max(3.0 * seg.length, 0.5) if seg is not None and seg.length > 1e-3 else 1e9
        kids = [mats[c].to_translation() for c in children.get(b.index, [])]
        kids = [k for k in kids if 1e-3 < (k - head).length <= limit]
        tail = None
        if kids:
            avg = sum(kids, Vector()) / len(kids)
            if (avg - head).length > 1e-3:
                tail = avg
        if tail is None and seg is not None and seg.length > 1e-3:
            tail = head + seg.normalized() * max(seg.length * 0.4, 0.05)
        if tail is None:
            tail = head + mats[b.index].to_3x3() @ Vector((0, 0.08, 0))
        eb.tail = tail
        try:
            eb.align_roll(mats[b.index].to_3x3() @ Vector((0, 0, 1)))
        except Exception:
            pass
        ebs.append(eb)
    for b in model.bones:
        if 0 <= b.parent < len(model.bones) and b.parent != b.index:
            ebs[b.index].parent = ebs[b.parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = prev_active
    return obj


def _color_key(ca):
    return "color_srgb" if (len(ca.data) and hasattr(ca.data[0], "color_srgb")) else "color"


def _mark_sharp_where_normals_split(me, faces, loop_nrm, eps=1e-4):
    """Blender only lets a vertex have different normals on neighbour faces
    if the edge between them is 'sharp'. Marks those edges so the game
    normals are kept exactly."""
    seen = {}
    sharp = set()
    for fi, vids in enumerate(faces):
        for k in range(3):
            a, b = vids[k], vids[(k + 1) % 3]
            na, nb = loop_nrm[3 * fi + k], loop_nrm[3 * fi + (k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if a > b:
                na, nb = nb, na
            prev = seen.get(key)
            if prev is None:
                seen[key] = (na, nb)
            elif key not in sharp:
                if max(abs(x - y) for x, y in zip(prev[0] + prev[1], na + nb)) > eps:
                    sharp.add(key)
    if not sharp:
        return
    ev = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    flags = [((int(a), int(b)) if a < b else (int(b), int(a))) in sharp for a, b in ev]
    attr = me.attributes.get("sharp_edge") or me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    attr.data.foreach_set("value", flags)


def import_slot(model, slot, name, collection, arm_obj, materials, fed_path, with_morphs):
    # Blender vertices = unique positions (welded mesh). Exception: double-sided
    # faces (same 3 vertices, opposite order - common on skirts/clothes) get
    # their own vertices, otherwise Blender would drop the repeated face.
    bind = Matrix(model.slot_matrix(slot))       # slot vertices -> model space
    bind3 = bind.to_3x3()
    ident = fm.mat_is_identity(model.slot_matrix(slot))
    to_model = (lambda p: p) if ident else (lambda p: tuple(bind @ Vector(p)))
    nrm_model = (lambda n: n) if ident else (lambda n: tuple((bind3 @ Vector(n)).normalized()))
    src = []           # per Blender vertex -> unique position index
    first = {}         # unique position -> Blender vertex
    faces, loop_nrm, loop_raw, loop_uv, face_mat = [], [], [], [], []
    seen_faces = set()
    mat_slots = []
    for sm in slot.submeshes:
        if sm.material_id not in mat_slots:
            mat_slots.append(sm.material_id)
        mslot = mat_slots.index(sm.material_id)
        for (a, b, c) in sm.triangles:
            tri = (c, b, a)  # flip the order (game = clockwise)
            uids = [sm.pos_ids[v] for v in tri]
            if len(set(uids)) < 3:
                continue  # degenerate triangle
            vids = []
            for u in uids:
                if u not in first:
                    first[u] = len(src)
                    src.append(u)
                vids.append(first[u])
            key = frozenset(vids)
            if key in seen_faces:
                vids = []
                for u in uids:
                    vids.append(len(src))
                    src.append(u)
                key = frozenset(vids)
            seen_faces.add(key)
            faces.append(vids)
            face_mat.append(mslot)
            for v in tri:
                loop_nrm.append(g2b(nrm_model(sm.normals[v])) if sm.normals else None)
                loop_raw.append(sm.normals[v] if sm.normals else None)
                t = sm.uvs[v] if sm.uvs else (0.0, 0.0)
                loop_uv.append((t[0], -t[1]))

    me = bpy.data.meshes.new(name)
    me.from_pydata([g2b(to_model(slot.uniq_pos[u])) for u in src], [], faces)
    if len(me.polygons) != len(faces):
        raise RuntimeError("%s: Blender dropped %d faces" % (name, len(faces) - len(me.polygons)))

    for m in mat_slots:
        me.materials.append(materials.get(m))
    npoly = len(me.polygons)
    me.polygons.foreach_set("material_index", face_mat)

    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", [x for t in loop_uv for x in t])

    # vertex color
    ca = me.color_attributes.new("Col", "BYTE_COLOR", "POINT")
    flat = []
    for u in src:
        c = slot.uniq_col[u] if u < len(slot.uniq_col) else (255, 255, 255, 255)
        flat += [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, c[3] / 255.0]
    if flat:
        ca.data.foreach_set(_color_key(ca), flat)

    # original id (to reuse morph data and exact values on export)
    uid = me.attributes.new(UID_ATTR, "INT", "POINT")
    if src:
        uid.data.foreach_set("value", src)

    me.polygons.foreach_set("use_smooth", [True] * npoly)
    if loop_nrm and all(n is not None for n in loop_nrm):
        _mark_sharp_where_normals_split(me, faces, loop_nrm)
        me.normals_split_custom_set([Vector(n).normalized() for n in loop_nrm])
        na = me.attributes.new(NRM_ATTR, "FLOAT_VECTOR", "CORNER")
        na.data.foreach_set("vector", [x for n in loop_raw for x in n])  # raw, slot space
        me.update()
        bn = me.attributes.new(BNRM_ATTR, "FLOAT_VECTOR", "CORNER")
        bn.data.foreach_set("vector", _corner_normals(me).ravel())
    me.update()

    obj = bpy.data.objects.new(name, me)
    collection.objects.link(obj)
    obj[PROP_FED] = fed_path
    obj[PROP_SLOT] = slot.index
    hidden = slot.is_hidden
    obj[PROP_HIDE] = hidden
    obj[PROP_HIDE_ORIG] = hidden

    # weights
    for i, u in enumerate(src):
        for bone, w in slot.vertex_weights(u):
            if w <= 0:
                continue
            gname = fm.bone_name(bone)
            vg = obj.vertex_groups.get(gname) or obj.vertex_groups.new(name=gname)
            vg.add([i], w, "ADD")

    obj.parent = arm_obj
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj

    # morphs -> shape keys
    if with_morphs and slot.morph_targets:
        obj.shape_key_add(name="Basis", from_mix=False)
        for t in slot.morph_targets:
            if t >= len(model.meshes):
                continue
            ms = model.meshes[t]
            kb = obj.shape_key_add(name="morph_slot%02d" % t, from_mix=False)
            kb.value = 0.0  # Blender 5 creates shape keys with value 1.0
            co = []
            for u in src:
                d = ms.uniq_pos[u] if u < len(ms.uniq_pos) else (0.0, 0.0, 0.0)
                p = slot.uniq_pos[u]
                co += list(g2b(to_model((p[0] + d[0], p[1] + d[1], p[2] + d[2]))))
            if co:
                kb.data.foreach_set("co", co)
    obj[PROP_SIG] = mesh_signature(obj)
    if hidden:
        obj.hide_set(True)
    return obj


def import_fed(op, context, path, tex_dir="", with_textures=True, with_morphs=True,
               save_fed="", save_tex="", tex_path=""):
    model = fm.Model.load(path)
    name = tf.strip_index(path)
    coll = bpy.data.collections.new(name)
    context.scene.collection.children.link(coll)

    tex = None
    if with_textures:
        tex_path = tex_path or find_tex_for(path, tex_dir)
        if tex_path:
            tex = tf.TexFile.load(tex_path)
            _log(op, "INFO", "textures: %s" % os.path.basename(tex_path))
        else:
            _log(op, "WARNING", "no .tex found for %s (use 'Load .tex')" % name)

    arm = build_armature(model, name, coll)
    arm[PROP_FED] = path
    if tex_path:
        arm[PROP_TEX] = tex_path
    if save_fed:
        arm[PROP_SAVE_FED] = save_fed
    if save_tex:
        arm[PROP_SAVE_TEX] = save_tex

    used_mats = sorted({m for s in model.geometry_slots() for m in s.material_ids})
    materials = {m: build_material(model, m, tex, tex_path, name) for m in used_mats}

    count = 0
    for slot in model.geometry_slots():
        import_slot(model, slot, "%s_s%02d" % (name, slot.index), coll, arm, materials, path, with_morphs)
        count += 1
    _log(op, "INFO", "%s: %d meshes, %d bones imported" % (name, count, len(model.bones)))
    return arm


class IMPORT_OT_recolove_fed(bpy.types.Operator, ImportHelper):
    """Import a RecoLove model (.fed) with skeleton, weights, morphs and textures"""
    bl_idname = "import_scene.recolove_fed"
    bl_label = "Import RecoLove .fed"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fed"
    filter_glob: StringProperty(default="*.fed", options={"HIDDEN"})
    tex_dir: StringProperty(
        name="Textures folder",
        description="Where to look for the model's .tex. Empty = search automatically in the .fed "
                    "folder and in nearby folders with 'tex' in their name",
        default="")
    with_textures: BoolProperty(name="Textures", default=True)
    with_morphs: BoolProperty(name="Morphs as shape keys", default=True)
    tex_path: StringProperty(default="", options={"HIDDEN"})
    save_fed: StringProperty(default="", options={"HIDDEN"})
    save_tex: StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        try:
            import_fed(self, context, self.filepath, self.tex_dir, self.with_textures, self.with_morphs,
                       self.save_fed, self.save_tex, self.tex_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "Import failed: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------------------------------------------------------------------
# EXPORT
# ---------------------------------------------------------------------

def _corner_normals(me):
    if hasattr(me, "corner_normals"):
        arr = np.empty(len(me.loops) * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", arr)
        return arr.reshape(-1, 3)
    me.calc_normals_split()
    arr = np.empty(len(me.loops) * 3, dtype=np.float32)
    me.loops.foreach_get("normal", arr)
    return arr.reshape(-1, 3)


def _material_id_of(me, poly_mat_index, fallback):
    if 0 <= poly_mat_index < len(me.materials):
        m = me.materials[poly_mat_index]
        if m is not None and PROP_MAT_ID in m:
            return int(m[PROP_MAT_ID])
        if m is not None and "_mat" in m.name:
            try:
                return int(m.name.rsplit("_mat", 1)[1][:2])
            except ValueError:
                pass
    return fallback


def _to_game_matrix(obj):
    """Object -> game space matrix (relative to the armature, if any)."""
    mw = obj.matrix_world
    if obj.parent is not None:
        mw = obj.parent.matrix_world.inverted() @ mw
    return AXIS_INV @ mw


def _vertex_normals(me):
    if hasattr(me, "vertex_normals"):
        arr = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertex_normals.foreach_get("vector", arr)
        return arr.reshape(-1, 3)
    return np.array([tuple(v.normal) for v in me.vertices], dtype=np.float32)


def export_slot(op, obj, model, slot, report):
    """Rebuilds the slot from the Blender object."""
    orig_skinned = slot.is_skinned
    me = obj.data
    to_game = _to_game_matrix(obj)
    nrm_mat = to_game.to_3x3().inverted().transposed()

    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    uniq_pos = [tuple(to_game @ Vector(c)) for c in co]  # model space

    # colors
    ca = me.color_attributes.get("Col") or (me.color_attributes[0] if len(me.color_attributes) else None)
    uniq_col = None
    if ca is not None:
        n = len(ca.data)
        buf = np.empty(n * 4, dtype=np.float32)
        ca.data.foreach_get(_color_key(ca), buf)
        buf = buf.reshape(-1, 4)
        if ca.domain == "POINT":
            vc = buf
        else:
            vc = np.zeros((nv, 4), dtype=np.float32)
            cnt = np.zeros(nv, dtype=np.float32)
            lv = np.empty(len(me.loops), dtype=np.int32)
            me.loops.foreach_get("vertex_index", lv)
            np.add.at(vc, lv, buf)
            np.add.at(cnt, lv, 1)
            vc /= np.maximum(cnt, 1)[:, None]
        uniq_col = [tuple(int(round(x * 255)) for x in np.clip(c, 0, 1)) for c in vc]

    # weights
    weights = None
    group_bone = {}
    for vg in obj.vertex_groups:
        bi = fm.bone_index_from_name(vg.name)
        if bi is not None and bi < len(model.bones):
            group_bone[vg.index] = bi
    per_vert = []
    missing = []
    for v in me.vertices:
        ws = {}
        for g in v.groups:
            b = group_bone.get(g.group)
            if b is not None and g.weight > 1e-4:
                ws[b] = ws.get(b, 0.0) + g.weight
        per_vert.append(ws)
        if not ws:
            missing.append(v.index)

    rigid_bone = slot.bone
    all_rigid = all((not ws) or (len(ws) == 1 and rigid_bone in ws) for ws in per_vert)
    if slot.interleaved:
        if not all_rigid:
            report.append("slot %d: this kind of mesh (interleaved vertices) cannot have per-vertex weights "
                          "in the game - exported as rigid (follows bone %s)" % (slot.index, rigid_bone))
    elif orig_skinned or not all_rigid:
        if missing:
            # inherit the weights of the nearest ORIGINAL vertex
            old_bind = Matrix(model.slot_matrix(slot))
            kd = KDTree(len(slot.uniq_pos))
            for i, p in enumerate(slot.uniq_pos):
                kd.insert(old_bind @ Vector(p), i)
            kd.balance()
            for vi in missing:
                _, idx, _ = kd.find(Vector(uniq_pos[vi]))
                per_vert[vi] = dict(slot.vertex_weights(idx))
            report.append("slot %d: %d vertex(es) without weights got the weights of the nearest original vertex"
                          % (slot.index, len(missing)))
        weights = []
        for ws in per_vert:
            items = sorted(ws.items(), key=lambda t: -t[1])[:8]
            tot = sum(w for _, w in items) or 1.0
            weights.append([(b, w / tot) for b, w in items])

    # Final vertex space: a mesh that stays RIGID goes back to its node's local
    # space (see fedmodel.Model.slot_matrix); with weights, model space.
    old_bind = model.slot_matrix(slot)
    new_bind = old_bind if (weights is None) == (not orig_skinned) else (
        fm.mat_identity() if weights is not None else model.world_matrices()[slot.bone])
    unbind = None if fm.mat_is_identity(new_bind) else fm.mat_invert_affine(new_bind)
    same_space = new_bind is old_bind
    if unbind is not None:
        uniq_pos = [fm.mat_transform_point(unbind, p) for p in uniq_pos]

    # A vertex that did not move since import goes back to its EXACT original
    # value (avoids Blender float32 / node matrix rounding).
    uid = me.attributes.get(UID_ATTR)
    if uid is not None and same_space:
        uids = np.empty(nv, dtype=np.int32)
        uid.data.foreach_get("value", uids)
        ob = Matrix(old_bind)
        for i, u in enumerate(uids):
            if 0 <= u < len(slot.uniq_pos):
                raw = slot.uniq_pos[u]
                if (Vector(g2b(tuple(ob @ Vector(raw)))) - Vector(co[i])).length < 1e-5:
                    uniq_pos[i] = raw

    # triangles
    me.calc_loop_triangles()
    lnrm = _corner_normals(me)
    exact = None  # raw original normal (slot space) per corner
    na = me.attributes.get(NRM_ATTR)
    if same_space and na is not None and na.domain == "CORNER" and len(na.data) == len(me.loops):
        exact = np.empty(len(me.loops) * 3, dtype=np.float32)
        na.data.foreach_get("vector", exact)
        exact = exact.reshape(-1, 3)
    bnrm = None
    ba = me.attributes.get(BNRM_ATTR)
    if exact is not None and ba is not None and ba.domain == "CORNER" and len(ba.data) == len(me.loops):
        bnrm = np.empty(len(me.loops) * 3, dtype=np.float32)
        ba.data.foreach_get("vector", bnrm)
        bnrm = bnrm.reshape(-1, 3)
        # corner normal equal to the one at import time => the user did not touch it
        unchanged = np.linalg.norm(bnrm - lnrm, axis=1) < 1e-4
    ob3 = Matrix(old_bind).to_3x3()
    local_to_blender3 = (_to_game_matrix(obj).to_3x3().inverted()) @ ob3
    uvl = me.uv_layers.active
    uvs = None
    if uvl is not None:
        uvs = np.empty(len(me.loops) * 2, dtype=np.float32)
        uvl.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
    lv = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)

    orig_flags = {sm.material_id: sm.flags for sm in slot.submeshes}
    default_mid = slot.submeshes[0].material_id if slot.submeshes else 0
    default_flags = slot.submeshes[0].flags if slot.submeshes else (fm.FLAG_NORMAL | fm.FLAG_COLOR | fm.FLAG_UV)
    by_mat = {}
    order = []
    for lt in me.loop_triangles:
        mid = _material_id_of(me, lt.material_index, default_mid)
        if mid not in by_mat:
            by_mat[mid] = []
            order.append(mid)
        corners = []
        for li in reversed(lt.loops):  # flip back the order
            vi = int(lv[li])
            n = None
            if exact is not None:
                # normal not changed (only Blender's compression) -> exact original value
                e = exact[li]
                # area ~0: Blender cannot store a custom normal there
                if (bnrm is not None and unchanged[li]) or lt.area < 1e-10 or \
                        ((local_to_blender3 @ Vector(e)) - Vector(lnrm[li])).length < 2e-3:
                    n = tuple(float(x) for x in e)
            if n is None:
                n = nrm_mat @ Vector(lnrm[li])
                n = n.normalized() if n.length > 1e-8 else Vector((0.0, 1.0, 0.0))
                if unbind is not None:
                    n = Vector(fm.mat_transform_vector(unbind, n)).normalized()
                n = tuple(n)
            uv = (float(uvs[li][0]), -float(uvs[li][1])) if uvs is not None else None
            corners.append((vi, n, uv))
        by_mat[mid].append(tuple(corners))

    if not order:
        raise RuntimeError("object '%s' has no faces - use 'Hide in game' to remove a mesh" % obj.name)
    specs = [dict(material_id=m, flags=orig_flags.get(m, default_flags), triangles=by_mat[m]) for m in order]
    old_uniq = list(slot.uniq_pos)
    slot.set_geometry(uniq_pos, uniq_col, weights, specs)
    return old_uniq


def export_morphs(obj, model, slot, report):
    """Rewrites the morph slots of a base slot from the shape keys."""
    if not slot.morph_targets:
        return
    me = obj.data
    keys = me.shape_keys.key_blocks if me.shape_keys else None
    nv = len(me.vertices)
    to_game3 = _to_game_matrix(obj).to_3x3()
    bind = model.slot_matrix(slot)  # already with the new weights (set_geometry ran before)
    if not fm.mat_is_identity(bind):
        to_game3 = Matrix(fm.mat_invert_affine(bind)).to_3x3() @ to_game3
    basis = np.empty(nv * 3, dtype=np.float32)
    me.vertices.foreach_get("co", basis)
    basis = basis.reshape(-1, 3)
    uid = me.attributes.get(UID_ATTR)
    uids = None
    if uid is not None:
        uids = np.empty(nv, dtype=np.int32)
        uid.data.foreach_get("value", uids)
    bnrm = _vertex_normals(me)

    for t in slot.morph_targets:
        if t >= len(model.meshes):
            continue
        ms = model.meshes[t]
        kb = keys.get("morph_slot%02d" % t) if keys else None
        dpos, dnrm = [], []
        if kb is None:
            dpos = [(0.0, 0.0, 0.0)] * nv
            dnrm = [(0.0, 0.0, 0.0)] * nv
            report.append("slot %d: shape key morph_slot%02d is missing - that morph was zeroed" % (slot.index, t))
        else:
            kco = np.empty(nv * 3, dtype=np.float32)
            kb.data.foreach_get("co", kco)
            kco = kco.reshape(-1, 3)
            try:
                kn = np.array(kb.normals_vertex_get(), dtype=np.float32).reshape(-1, 3)
            except Exception:
                kn = bnrm
            for i in range(nv):
                d = tuple(to_game3 @ Vector(kco[i] - basis[i]))
                dpos.append(d)
                u = int(uids[i]) if uids is not None else -1
                if 0 <= u < len(ms.uniq_pos) and all(abs(a - b) < 1e-5 for a, b in zip(d, ms.uniq_pos[u])) \
                        and u < len(ms.uniq_nrm):
                    dnrm.append(ms.uniq_nrm[u])  # same as the original: reuse the normal delta
                else:
                    dnrm.append(tuple(to_game3 @ Vector(kn[i] - bnrm[i])))
        cols = slot.uniq_col if len(slot.uniq_col) == nv else None
        ms.set_morph_deltas(dpos, dnrm, cols)


def collect_objects(context, fed_path, only_selected):
    objs = context.selected_objects if only_selected else context.scene.objects
    return [o for o in objs if o.type == "MESH" and PROP_SLOT in o and o.get(PROP_FED) == fed_path]


def model_armature(context):
    """Armature of the RecoLove model being edited (active/selected first)."""
    for o in [context.active_object] + list(context.selected_objects):
        if o is None:
            continue
        for cand in (o, o.parent):
            if cand is not None and cand.type == "ARMATURE" and PROP_FED in cand:
                return cand
    for o in context.scene.objects:
        if o.type == "ARMATURE" and PROP_FED in o:
            return o
    return None


def find_source_fed(context):
    for o in [context.active_object] + list(context.selected_objects):
        if o is None:
            continue
        for cand in (o, o.parent):
            if cand is not None and PROP_FED in cand:
                return cand[PROP_FED]
    for o in context.scene.objects:
        if PROP_FED in o:
            return o[PROP_FED]
    return None


def export_fed(op, context, out_path, source_fed=None, only_selected=False, only_modified=True):
    source_fed = source_fed or find_source_fed(context)
    if not source_fed or not os.path.isfile(source_fed):
        raise RuntimeError("original .fed not found (%s). Import the model first." % source_fed)
    model = fm.Model.load(source_fed)
    objs = collect_objects(context, source_fed, only_selected)
    report = []
    written = []
    hidden = []
    seen = set()
    for obj in sorted(objs, key=lambda o: o[PROP_SLOT]):
        si = int(obj[PROP_SLOT])
        if si in seen:
            report.append("slot %d: more than one object uses it - only '%s' was used" % (si, obj.name))
            continue
        if si >= len(model.meshes) or model.meshes[si].kind != fm.MeshSlot.KIND_GEOMETRY:
            report.append("object %s points to an invalid slot %d - ignored" % (obj.name, si))
            continue
        seen.add(si)
        slot = model.meshes[si]
        if obj.get(PROP_HIDE, False):
            if not slot.is_hidden:
                model.hide_slot(si)
                hidden.append(si)
            continue
        if only_modified and obj.get(PROP_SIG) == mesh_signature(obj):
            continue
        export_slot(op, obj, model, slot, report)
        export_morphs(obj, model, slot, report)
        written.append(si)

    data = model.to_bytes()
    fm.Model(data)  # sanity check: the result must open again
    with open(out_path, "wb") as f:
        f.write(data)
    if os.path.abspath(out_path) == os.path.abspath(source_fed):
        # saved over the source: rewritten slots now use the Blender vertex
        # order as their unique-position order
        for obj in objs:
            if obj.get(PROP_SLOT) in written:
                uid = obj.data.attributes.get(UID_ATTR)
                if uid is not None:
                    uid.data.foreach_set("value", list(range(len(obj.data.vertices))))
    for line in report:
        _log(op, "WARNING", line)
    _log(op, "INFO", "%s saved: %d mesh(es) rewritten %s, %d hidden %s" % (
        os.path.basename(out_path), len(written), written, len(hidden), hidden))
    return written, report


def _run_export(op, context, path, only_selected=False, only_modified=True):
    try:
        export_fed(op, context, path, None, only_selected, only_modified)
    except Exception as e:
        import traceback
        traceback.print_exc()
        op.report({"ERROR"}, "Export failed: %s" % e)
        return {"CANCELLED"}
    # the saved file becomes the new reference for "only modified" checks
    fed = find_source_fed(context)
    if fed and os.path.abspath(fed) == os.path.abspath(path):
        for o in context.scene.objects:
            if o.get(PROP_FED) == fed and o.type == "MESH":
                o[PROP_SIG] = mesh_signature(o)
                o[PROP_HIDE_ORIG] = o.get(PROP_HIDE, False)
    return {"FINISHED"}


class EXPORT_OT_recolove_fed(bpy.types.Operator, ExportHelper):
    """Export the edited meshes back to a .fed (free topology)"""
    bl_idname = "export_scene.recolove_fed"
    bl_label = "Export RecoLove .fed"
    filename_ext = ".fed"
    filter_glob: StringProperty(default="*.fed", options={"HIDDEN"})
    only_selected: BoolProperty(name="Only selected objects", default=False)
    only_modified: BoolProperty(
        name="Only modified meshes", default=True,
        description="Unmodified meshes stay byte-identical to the original")

    def invoke(self, context, event):
        src = find_source_fed(context)
        if src:
            base = os.path.splitext(os.path.basename(src))[0]
            self.filepath = os.path.join(os.path.dirname(src), base + "_mod.fed")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        return _run_export(self, context, self.filepath, self.only_selected, self.only_modified)


class OBJECT_OT_recolove_save_project(bpy.types.Operator):
    """Save the model straight into the toolkit project (no file dialog)"""
    bl_idname = "object.recolove_save_project"
    bl_label = "Save to project"

    @classmethod
    def poll(cls, context):
        arm = model_armature(context)
        return arm is not None and bool(arm.get(PROP_SAVE_FED))

    def execute(self, context):
        arm = model_armature(context)
        res = _run_export(self, context, arm[PROP_SAVE_FED])
        if res == {"FINISHED"} and arm.get(PROP_SAVE_TEX):
            try:
                changed = export_tex(self, context, arm[PROP_SAVE_TEX], True)
                if changed:
                    for img in bpy.data.images:
                        if img.get(PROP_TEX) and img.is_dirty:
                            img.pack()  # clears the dirty flag
            except RuntimeError:
                pass
            self.report({"INFO"}, "Saved to the project. Go back to the RecoLove Toolkit to preview / build the mod.")
        return res


# ---------------------------------------------------------------------
# textures: load later / export .tex
# ---------------------------------------------------------------------

class OBJECT_OT_recolove_load_textures(bpy.types.Operator, ImportHelper):
    """Pick a .tex file and apply its textures to the model materials"""
    bl_idname = "object.recolove_load_textures"
    bl_label = "Load RecoLove textures (.tex)"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".tex"
    filter_glob: StringProperty(default="*.tex", options={"HIDDEN"})

    def execute(self, context):
        fed = find_source_fed(context)
        if not fed:
            self.report({"ERROR"}, "Select an object imported from a .fed first")
            return {"CANCELLED"}
        model = fm.Model.load(fed)
        tex = tf.TexFile.load(self.filepath)
        name = tf.strip_index(fed)
        n = 0
        for m in list(bpy.data.materials):
            if PROP_MAT_ID in m and m.name.startswith(name + "_mat"):
                build_material(model, int(m[PROP_MAT_ID]), tex, self.filepath, name)
                n += 1
        for o in context.scene.objects:
            if o.get(PROP_FED) == fed and o.type == "ARMATURE":
                o[PROP_TEX] = self.filepath
        self.report({"INFO"}, "%d materials updated with %s" % (n, os.path.basename(self.filepath)))
        return {"FINISHED"}


def export_tex(op, context, out_path, only_modified=True):
    """Writes the (edited) images back into the slots of the source .tex."""
    groups = {}
    for img in bpy.data.images:
        if PROP_TEX in img and PROP_TEX_SLOT in img:
            groups.setdefault(img[PROP_TEX], []).append(img)
    if not groups:
        raise RuntimeError("no RecoLove textures loaded")
    if len(groups) > 1:
        arm = model_armature(context)
        want = arm.get(PROP_TEX) if arm is not None else None
        if want in groups:
            groups = {want: groups[want]}
        else:
            raise RuntimeError("textures from more than one .tex are loaded - select the model first")
    src, imgs = next(iter(groups.items()))
    tex = tf.TexFile.load(src)
    changed = []
    for img in imgs:
        if only_modified and not img.is_dirty and img.packed_file is not None and not img.get("recolove_force"):
            continue  # packed at import time and not edited
        w, h, rgba = image_to_rgba(img)
        tex.replace_rgba(int(img[PROP_TEX_SLOT]), rgba, w, h)
        changed.append(int(img[PROP_TEX_SLOT]))
    tex.save(out_path)
    _log(op, "INFO", "%s: %d texture(s) re-encoded %s" % (os.path.basename(out_path), len(changed), changed))
    return changed


class EXPORT_OT_recolove_tex(bpy.types.Operator, ExportHelper):
    """Save the textures edited in Blender back into a .tex"""
    bl_idname = "export_scene.recolove_tex"
    bl_label = "Export RecoLove .tex"
    filename_ext = ".tex"
    filter_glob: StringProperty(default="*.tex", options={"HIDDEN"})
    only_modified: BoolProperty(
        name="Only edited textures", default=True,
        description="Turn off to re-encode every texture (DXT5 is lossy, each re-encode degrades a bit)")

    def invoke(self, context, event):
        for img in bpy.data.images:
            if PROP_TEX in img:
                base = os.path.splitext(os.path.basename(img[PROP_TEX]))[0]
                self.filepath = os.path.join(os.path.dirname(img[PROP_TEX]), base + "_mod.tex")
                break
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            export_tex(self, context, self.filepath, self.only_modified)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "Texture export failed: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------------------------------------------------------------------
# tools: hide in game / use any object for a slot / list slots / help
# ---------------------------------------------------------------------

class OBJECT_OT_recolove_toggle_hide(bpy.types.Operator):
    """Hide/show this mesh in the GAME (e.g. remove clothes). Applied when you save"""
    bl_idname = "object.recolove_toggle_hide"
    bl_label = "Hide in game"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" and PROP_SLOT in o for o in context.selected_objects)

    def execute(self, context):
        objs = [o for o in context.selected_objects if o.type == "MESH" and PROP_SLOT in o]
        new = not all(o.get(PROP_HIDE, False) for o in objs)
        for o in objs:
            if not new and o.get(PROP_HIDE_ORIG, False):
                self.report({"WARNING"}, "%s was already hidden in the original file - it cannot be restored"
                            % o.name)
                continue
            o[PROP_HIDE] = new
        self.report({"INFO"}, "%d mesh(es) will be %s in the game when you save" %
                    (len(objs), "HIDDEN" if new else "visible"))
        return {"FINISHED"}


class OBJECT_OT_recolove_mark_replace(bpy.types.Operator):
    """Use the active object (from any source: OBJ/FBX/another game) in place
    of an existing mesh slot when saving"""
    bl_idname = "object.recolove_mark_replace"
    bl_label = "Use for slot"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(name="Slot", default=0, min=0, description="Slot number (shown as _sNN in the object names)")
    material_id: IntProperty(name="Material", default=0, min=0,
                             description="Game material (texture/shader) used when the object has no RecoLove material")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        arm = model_armature(context)
        fed = arm[PROP_FED] if arm is not None else find_source_fed(context)
        if not fed:
            self.report({"ERROR"}, "Import the target .fed first (the object must know which file to replace)")
            return {"CANCELLED"}
        for o in context.scene.objects:  # the old object of that slot stops being exported
            if o is not obj and o.get(PROP_FED) == fed and o.get(PROP_SLOT) == self.slot:
                del o[PROP_SLOT]
                o.hide_set(True)
        obj[PROP_FED] = fed
        obj[PROP_SLOT] = self.slot
        obj[PROP_SIG] = ""
        obj[PROP_HIDE] = False
        for m in obj.data.materials:
            if m is not None and PROP_MAT_ID not in m:
                m[PROP_MAT_ID] = self.material_id
        if not obj.data.materials:
            mat = bpy.data.materials.new("%s_mat%02d" % (tf.strip_index(fed), self.material_id))
            mat[PROP_MAT_ID] = self.material_id
            obj.data.materials.append(mat)
        if arm is not None and obj.parent is None:
            mw = obj.matrix_world.copy()
            obj.parent = arm
            obj.matrix_world = mw
            if not any(m.type == "ARMATURE" for m in obj.modifiers):
                obj.modifiers.new("Armature", "ARMATURE").object = arm
        self.report({"INFO"}, "%s will replace slot %d when you save" % (obj.name, self.slot))
        return {"FINISHED"}


class IMPORT_OT_recolove_list_slots(bpy.types.Operator, ImportHelper):
    """List the mesh slots of a .fed in the system console, without importing"""
    bl_idname = "import_scene.recolove_list_slots"
    bl_label = "List .fed slots"
    filename_ext = ".fed"
    filter_glob: StringProperty(default="*.fed", options={"HIDDEN"})

    def execute(self, context):
        print(fm.Model.load(self.filepath).summary())
        self.report({"INFO"}, "Printed to the system console (Window > Toggle System Console)")
        return {"FINISHED"}


class WM_OT_recolove_help(bpy.types.Operator):
    """Show the RecoLove add-on guide"""
    bl_idname = "wm.recolove_help"
    bl_label = "RecoLove help"

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=560)

    def draw(self, context):
        col = self.layout.column(align=True)
        for line in HELP_LINES:
            col.label(text=line)


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

class VIEW3D_PT_recolove(bpy.types.Panel):
    bl_label = "RecoLove"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RecoLove"

    def draw(self, context):
        layout = self.layout
        arm = model_armature(context)

        box = layout.box()
        box.label(text="Model", icon="ARMATURE_DATA")
        if arm is None:
            box.operator(IMPORT_OT_recolove_fed.bl_idname, text="Import .fed", icon="IMPORT")
        else:
            box.label(text=tf.strip_index(arm[PROP_FED]))
            if arm.get(PROP_SAVE_FED):
                row = box.row()
                row.scale_y = 1.6
                row.operator(OBJECT_OT_recolove_save_project.bl_idname, text="Save to project", icon="FILE_TICK")
            box.operator(EXPORT_OT_recolove_fed.bl_idname, text="Export .fed as...", icon="EXPORT")
            box.operator(IMPORT_OT_recolove_fed.bl_idname, text="Import another .fed", icon="IMPORT")

        obj = context.active_object
        box = layout.box()
        box.label(text="Selected mesh", icon="MESH_DATA")
        if obj is not None and obj.type == "MESH" and PROP_SLOT in obj:
            col = box.column(align=True)
            col.label(text="%s  (slot %d)" % (obj.name, obj[PROP_SLOT]))
            hidden = obj.get(PROP_HIDE, False)
            col.label(text="In game: %s" % ("HIDDEN" if hidden else "visible"),
                      icon="HIDE_ON" if hidden else "HIDE_OFF")
            box.operator(OBJECT_OT_recolove_toggle_hide.bl_idname,
                         text="Show in game" if hidden else "Hide in game",
                         icon="HIDE_OFF" if hidden else "HIDE_ON")
        else:
            box.label(text="Select a mesh of the model")
        box.operator(OBJECT_OT_recolove_mark_replace.bl_idname, text="Use selected object for slot...",
                     icon="MOD_DATA_TRANSFER")

        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        if arm is not None and arm.get(PROP_SAVE_TEX):
            box.label(text="Saved together with 'Save to project'")
        else:
            box.operator(EXPORT_OT_recolove_tex.bl_idname, text="Save textures (.tex) as...", icon="IMAGE_DATA")
        box.operator(OBJECT_OT_recolove_load_textures.bl_idname, text="Load another .tex...", icon="FILE_IMAGE")

        layout.operator(WM_OT_recolove_help.bl_idname, text="Help", icon="HELP")


def menu_import(self, context):
    self.layout.operator(IMPORT_OT_recolove_fed.bl_idname, text="RecoLove Model (.fed)")


def menu_export(self, context):
    self.layout.operator(EXPORT_OT_recolove_fed.bl_idname, text="RecoLove Model (.fed)")
    self.layout.operator(EXPORT_OT_recolove_tex.bl_idname, text="RecoLove Textures (.tex)")


CLASSES = (
    IMPORT_OT_recolove_fed,
    EXPORT_OT_recolove_fed,
    OBJECT_OT_recolove_save_project,
    OBJECT_OT_recolove_load_textures,
    EXPORT_OT_recolove_tex,
    OBJECT_OT_recolove_toggle_hide,
    OBJECT_OT_recolove_mark_replace,
    IMPORT_OT_recolove_list_slots,
    WM_OT_recolove_help,
    VIEW3D_PT_recolove,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

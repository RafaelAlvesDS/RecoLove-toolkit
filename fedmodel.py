"""
RecoLove .fed model: FULL decoding and re-encoding.

Everything here was validated against all 356 .fed files of the game.
Tree overview (see fpac.py):

  FPAC (root)
   |- FPACMAT   materials: 16 bytes { u16 shader, u16 flag, u8 rgba[4],
   |            u32 0, u16 texA, u16 texB } or 8 bytes { u16 0, u16 0,
   |            rgba } (untextured). Texture = slot texA of the .tex; if that
   |            slot is empty, slot texB.
   |- FPACTRAN  nodes/bones: 52 bytes { i16 0, i16 parent, f32 t[3],
   |            f32 r[3], f32 s[3], f32 r2[3] } (XYZ Euler in radians,
   |            R = Rz*Ry*Rx). Skinned vertices are stored in MODEL space in
   |            this rest pose.
   |- FPACGEOM  N x FPACMESH (one mesh "slot" each)
   |- FPACDRMS  per slot: { u16 slot, u16 node } - node (FPACTRAN) the slot
   |            belongs to. Meshes without weights (empty block[6]) follow
   |            that node rigidly (e.g. hands).
   |- FPACMOR   { u16 baseSlot, u16 n, u32 morphSlots[n] } - morph targets
   |- FPACNTOD  (unknown, preserved)

FPACMESH (9 blocks):
  [0] 28 bytes: 6 floats (meaning unknown, preserved), u8 meshCount
      (0 = MORPH slot), u8 0, u8 flag, u8 4
  [1] meshCount x "display" submesh (what the GPU draws):
        32-byte header { u16 numIdx, u16 materialId, u32 attrFlags,
                         u64 0, u32 numVert, 12 bytes 0 }
        attrFlags: 0x04 normals, 0x08 color, 0x10 UV (position always)
        pos f32[3]*nv | nrm f32[3]*nv | color u8[4]*nv | uv f32[2]*nv |
        idx u16*numIdx (triangle list) - each array aligned to 16 bytes
  [2] UNIQUE positions f32[3]   (morph slot: position delta per unique pos)
  [3] unique normals   f32[3]   (morph slot: normal delta)
  [4] color per unique position u8[4]
  [5] unique UVs f32[2]
  [6] weights per unique position: { u32 n, {u32 bone, f32 weight} x n }
  [7] per submesh, per display vertex: u16 posId[nv], u16 nrmId[nv]
      (only if 0x04), u16 colId[nv] (only if 0x08), u8 uvId[nv] (truncated
      to 8 bits!) + u8 0[nv] (only if 0x10). Often allocated bigger than
      used (rest = garbage/zeros).
      NO block[7] => block[1] uses INTERLEAVED vertices (pos,nrm,col,uv per
      vertex) inside a region of the SAME size the separate arrays would
      take (rest zeroed). Their unique arrays are just the same set of
      positions (never used by morphs).
  [8] empty

UV: raw v has its origin at the TOP (glTF v = raw + 1, Blender v = -raw).
Axes: Y up. Triangles are clockwise relative to the normals.
"""
import math
import struct

try:
    from . import fpac
except ImportError:
    import fpac

FLAG_NORMAL = 0x04
FLAG_COLOR = 0x08
FLAG_UV = 0x10

MAX_INDICES_PER_SUBMESH = 0xFFFF - 2  # numIdx is u16


def _a16(n):
    return (n + 15) & ~15


def _pad(b):
    return bytes(b) + b"\x00" * (_a16(len(b)) - len(b))


# ---------------------------------------------------------------------
# minimal 4x4 math (no numpy/mathutils - runs on any Python)
# ---------------------------------------------------------------------

def mat_identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def euler_xyz(rx, ry, rz):
    """4x4 rotation R = Rz * Ry * Rx (validated: the only order that makes
    the left/right arms of the 'mdl' models mirror each other)."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx, 0.0],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx, 0.0],
        [-sy, cy * sx, cy * cx, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def mat_transform_point(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def mat_is_identity(m, eps=1e-5):
    return all(abs(m[i][j] - (1.0 if i == j else 0.0)) <= eps for i in range(4) for j in range(4))


def mat_transform_vector(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def mat_invert_affine(m):
    a = [[m[i][j] for j in range(3)] for i in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) -
           a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
           a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        det = 1e-12
    inv = [[0.0] * 3 for _ in range(3)]
    inv[0][0] = (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det
    inv[0][1] = (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det
    inv[0][2] = (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det
    inv[1][0] = (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det
    inv[1][1] = (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det
    inv[1][2] = (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det
    inv[2][0] = (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det
    inv[2][1] = (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det
    inv[2][2] = (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det
    t = [m[0][3], m[1][3], m[2][3]]
    out = mat_identity()
    for i in range(3):
        for j in range(3):
            out[i][j] = inv[i][j]
        out[i][3] = -(inv[i][0] * t[0] + inv[i][1] * t[1] + inv[i][2] * t[2])
    return out


# ---------------------------------------------------------------------
# FPACTRAN
# ---------------------------------------------------------------------

class Bone(object):
    def __init__(self, index, raw):
        self.index = index
        self.raw = raw
        self.unk0, self.parent = struct.unpack_from("<hh", raw, 0)
        f = struct.unpack_from("<12f", raw, 4)
        self.translation = f[0:3]
        self.rotation = f[3:6]
        self.scale = f[6:9]
        self.rotation2 = f[9:12]

    @property
    def name(self):
        return bone_name(self.index)

    def local_matrix(self):
        t = self.translation
        m = mat_identity()
        m[0][3], m[1][3], m[2][3] = t
        m = mat_mul(m, euler_xyz(*self.rotation))
        m = mat_mul(m, euler_xyz(*self.rotation2))
        s = mat_identity()
        s[0][0], s[1][1], s[2][2] = self.scale
        return mat_mul(m, s)


def bone_name(index):
    return "bone_%03d" % index


def bone_index_from_name(name):
    """'bone_012' / 'bone_012.L' / 'bone_012 hand' -> 12 (None if no match)."""
    if not name.startswith("bone_"):
        return None
    digits = ""
    for ch in name[5:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


# ---------------------------------------------------------------------
# FPACMAT
# ---------------------------------------------------------------------

class Material(object):
    def __init__(self, index, raw):
        self.index = index
        self.raw = raw
        self.shader, self.flag = struct.unpack_from("<HH", raw, 0)
        self.color = tuple(raw[4:8])
        if len(raw) >= 16:
            self.tex_a, self.tex_b = struct.unpack_from("<HH", raw, 12)
            self.textured = True
        else:
            self.tex_a = self.tex_b = None
            self.textured = False

    def texture_slot(self, tex):
        """.tex slot used by this material (tex = texfile.TexFile or anything
        with .has(i)). None if untextured."""
        if not self.textured:
            return None
        if tex is None:
            return self.tex_a
        if tex.has(self.tex_a):
            return self.tex_a
        if tex.has(self.tex_b):
            return self.tex_b
        return None


# ---------------------------------------------------------------------
# FPACMESH
# ---------------------------------------------------------------------

def _stride(flags):
    return 12 + (12 if flags & FLAG_NORMAL else 0) + (4 if flags & FLAG_COLOR else 0) + (8 if flags & FLAG_UV else 0)


def _vertex_region_size(flags, nv):
    """Size of a submesh vertex region - the SAME for both layouts: when
    interleaved the data takes the start and the rest is zero."""
    n = _a16(12 * nv)
    if flags & FLAG_NORMAL:
        n += _a16(12 * nv)
    if flags & FLAG_COLOR:
        n += _a16(4 * nv)
    if flags & FLAG_UV:
        n += _a16(8 * nv)
    return n


def _read_interleaved(b1, p, nv, sm):
    stride = _stride(sm.flags)
    for i in range(nv):
        q = p + stride * i
        sm.positions.append(struct.unpack_from("<3f", b1, q))
        q += 12
        if sm.flags & FLAG_NORMAL:
            sm.normals.append(struct.unpack_from("<3f", b1, q))
            q += 12
        if sm.flags & FLAG_COLOR:
            sm.colors.append(tuple(b1[q:q + 4]))
            q += 4
        if sm.flags & FLAG_UV:
            sm.uvs.append(struct.unpack_from("<2f", b1, q))
    return p + _vertex_region_size(sm.flags, nv)


def _write_interleaved(sm):
    nv = len(sm.positions)
    out = bytearray()
    for i in range(nv):
        out += struct.pack("<3f", *sm.positions[i])
        if sm.flags & FLAG_NORMAL:
            out += struct.pack("<3f", *sm.normals[i])
        if sm.flags & FLAG_COLOR:
            out += bytes(sm.colors[i])
        if sm.flags & FLAG_UV:
            out += struct.pack("<2f", *sm.uvs[i])
    out += b"\x00" * (_vertex_region_size(sm.flags, nv) - len(out))
    return bytes(out)


class SubMesh(object):
    """'Display' submesh (block[1]) + maps into the unique arrays."""

    def __init__(self):
        self.material_id = 0
        self.flags = FLAG_NORMAL | FLAG_COLOR | FLAG_UV
        self.positions = []
        self.normals = []   # empty without FLAG_NORMAL
        self.colors = []    # (r,g,b,a) 0-255
        self.uvs = []       # raw (u, v) from the file
        self.indices = []   # flat u16 list (triangles)
        self.pos_ids = []   # per display vertex -> index into uniq_pos
        self.nrm_ids = []
        self.col_ids = []
        self.uv_ids = []

    @property
    def triangles(self):
        ix = self.indices
        return [(ix[i], ix[i + 1], ix[i + 2]) for i in range(0, len(ix) - 2, 3)]


class MeshSlot(object):
    """One FPACMESH (child of FPACGEOM)."""

    KIND_EMPTY = "empty"
    KIND_GEOMETRY = "geometry"
    KIND_MORPH = "morph"

    def __init__(self, index, node):
        self.index = index
        self.node = node               # original fpac.Node (only rewritten if dirty)
        self.dirty = False
        self.block0 = bytes(node.children[0]) if node.children else b""
        self.submeshes = []
        self.uniq_pos = []
        self.uniq_nrm = []
        self.uniq_col = []
        self.uniq_uv = []
        self.weights = None            # list (per uniq_pos) of [(bone, weight)] or None (rigid)
        self.has_maps = False          # block[7] present
        self.interleaved = False       # interleaved vertices (meshes without block[7])
        self.maps_derived = False      # no valid block[7] -> ids rebuilt by value
        self.bone = None               # node from FPACDRMS
        self.morph_base = None         # base slot (if this is a morph)
        self.morph_targets = []        # morph slots (if this is a base)
        self._decode()

    # -------- reading
    def _blk(self, k):
        c = self.node.children
        return bytes(c[k]) if k < len(c) and c[k] else b""

    @property
    def mesh_count(self):
        return self.block0[24] if len(self.block0) >= 28 else 0

    @property
    def kind(self):
        if self.mesh_count > 0 and self._blk(1):
            return self.KIND_GEOMETRY
        if self.mesh_count == 0 and self._blk(2):
            return self.KIND_MORPH
        return self.KIND_EMPTY

    def _decode(self):
        b2, b3, b4, b5 = self._blk(2), self._blk(3), self._blk(4), self._blk(5)
        self.uniq_pos = [struct.unpack_from("<3f", b2, 12 * i) for i in range(len(b2) // 12)]
        self.uniq_nrm = [struct.unpack_from("<3f", b3, 12 * i) for i in range(len(b3) // 12)]
        self.uniq_col = [tuple(b4[4 * i:4 * i + 4]) for i in range(len(b4) // 4)]
        self.uniq_uv = [struct.unpack_from("<2f", b5, 8 * i) for i in range(len(b5) // 8)]
        if self.kind != self.KIND_GEOMETRY:
            return

        b1 = self._blk(1)
        # No block[7] => INTERLEAVED vertices (validated: 1034 of the 3555
        # meshes in the game; with block[7] => separate arrays, 2521 meshes).
        self.interleaved = not self._blk(7)
        p = 0
        for _ in range(self.mesh_count):
            sm = SubMesh()
            nidx, sm.material_id, sm.flags, _z1, nv = struct.unpack_from("<HHIQI", b1, p)
            p += 32
            if self.interleaved:
                p = _read_interleaved(b1, p, nv, sm)
            else:
                sm.positions = [struct.unpack_from("<3f", b1, p + 12 * i) for i in range(nv)]
                p += _a16(12 * nv)
                if sm.flags & FLAG_NORMAL:
                    sm.normals = [struct.unpack_from("<3f", b1, p + 12 * i) for i in range(nv)]
                    p += _a16(12 * nv)
                if sm.flags & FLAG_COLOR:
                    sm.colors = [tuple(b1[p + 4 * i:p + 4 * i + 4]) for i in range(nv)]
                    p += _a16(4 * nv)
                if sm.flags & FLAG_UV:
                    sm.uvs = [struct.unpack_from("<2f", b1, p + 8 * i) for i in range(nv)]
                    p += _a16(8 * nv)
            sm.indices = list(struct.unpack_from("<%dH" % nidx, b1, p))
            p += _a16(2 * nidx)
            self.submeshes.append(sm)

        b6 = self._blk(6)
        if b6:
            self.weights = []
            q = 0
            for _ in range(len(self.uniq_pos)):
                n = struct.unpack_from("<I", b6, q)[0]
                q += 4
                ws = []
                for _j in range(n):
                    bone, w = struct.unpack_from("<If", b6, q)
                    q += 8
                    ws.append((bone, w))
                self.weights.append(ws)

        b7 = self._blk(7)
        self.has_maps = bool(b7)
        ok = False
        if b7:
            try:
                q = 0
                for sm in self.submeshes:
                    nv = len(sm.positions)
                    sm.pos_ids = list(struct.unpack_from("<%dH" % nv, b7, q)); q += 2 * nv
                    sm.nrm_ids = [0] * nv
                    if sm.flags & FLAG_NORMAL:
                        sm.nrm_ids = list(struct.unpack_from("<%dH" % nv, b7, q)); q += 2 * nv
                    sm.col_ids = list(sm.pos_ids)
                    if sm.flags & FLAG_COLOR:
                        sm.col_ids = list(struct.unpack_from("<%dH" % nv, b7, q)); q += 2 * nv
                    sm.uv_ids = [0] * nv
                    if sm.flags & FLAG_UV:
                        sm.uv_ids = list(b7[q:q + nv]); q += 2 * nv
                ok = all(self.uniq_pos[sm.pos_ids[i]] == sm.positions[i]
                         for sm in self.submeshes for i in range(len(sm.positions)))
            except (struct.error, IndexError):
                ok = False
        if not ok:
            self._derive_ids()

    def _derive_ids(self):
        """No valid block[7] (rigid 'mdl' meshes, collision): rebuild the ids
        by value equality."""
        self.maps_derived = True
        pos_ix = {}
        for i, p in enumerate(self.uniq_pos):
            pos_ix.setdefault(p, i)
        nrm_ix = {}
        for i, n in enumerate(self.uniq_nrm):
            nrm_ix.setdefault(n, i)
        uv_ix = {}
        for i, t in enumerate(self.uniq_uv):
            uv_ix.setdefault(t, i)
        for sm in self.submeshes:
            sm.pos_ids = []
            for p in sm.positions:
                if p not in pos_ix:
                    pos_ix[p] = len(self.uniq_pos)
                    self.uniq_pos.append(p)
                sm.pos_ids.append(pos_ix[p])
            sm.nrm_ids = [nrm_ix.get(n, 0) for n in sm.normals] if sm.normals else [0] * len(sm.positions)
            sm.col_ids = list(sm.pos_ids)
            sm.uv_ids = [uv_ix.get(t, 0) & 0xFF for t in sm.uvs] if sm.uvs else [0] * len(sm.positions)
        # color per unique position, completed with the display color when missing
        cols = list(self.uniq_col[:len(self.uniq_pos)])
        cols += [(255, 255, 255, 255)] * (len(self.uniq_pos) - len(cols))
        for sm in self.submeshes:
            for i, ui in enumerate(sm.pos_ids):
                if sm.colors and ui >= len(self.uniq_col):
                    cols[ui] = sm.colors[i]
        self.uniq_col = cols

    # -------- queries
    def vertex_weights(self, uniq_index):
        """[(bone, weight)] of a unique position (rigid -> [(node, 1.0)])."""
        if self.weights is not None and uniq_index < len(self.weights):
            return self.weights[uniq_index]
        return [(self.bone if self.bone is not None else 0, 1.0)]

    @property
    def is_skinned(self):
        return self.weights is not None

    @property
    def material_ids(self):
        return [sm.material_id for sm in self.submeshes]

    @property
    def is_hidden(self):
        """True if every triangle is degenerate (slot hidden by zeroing)."""
        return all(len(set(t)) < 3 for sm in self.submeshes for t in sm.triangles)

    # -------- editing
    def set_geometry(self, uniq_pos, uniq_col, weights, submeshes):
        """Replaces the whole geometry of the slot (FREE topology).

        uniq_pos : [(x,y,z)]  - one per "real" vertex (e.g. a Blender vertex)
        uniq_col : [(r,g,b,a)] per uniq_pos, or None (white)
        weights  : [[(bone, weight)]] per uniq_pos, or None (rigid mesh that
                   follows the FPACDRMS node)
        submeshes: list of dict {material_id, flags, triangles}
                   triangles = [(c0, c1, c2)], each corner c = (uniq_index,
                   normal (x,y,z) or None, uv (u, raw_v) or None).
                   Submeshes with more than 21844 triangles are split
                   automatically (the header numIdx is u16).
        """
        if len(uniq_pos) > 0xFFFF:
            raise ValueError("mesh has %d unique vertices - the format limit is 65535" % len(uniq_pos))
        if uniq_col is None:
            uniq_col = [(255, 255, 255, 255)] * len(uniq_pos)
        f32 = lambda t: tuple(struct.unpack("<%df" % len(t), struct.pack("<%df" % len(t), *t)))

        self.uniq_pos = [f32(p) for p in uniq_pos]
        self.uniq_col = [tuple(int(x) for x in c) for c in uniq_col]
        self.weights = [list(w) for w in weights] if weights is not None else None
        self.uniq_nrm = []
        self.uniq_uv = []
        nrm_ix = {}
        uv_ix = {}
        self.submeshes = []

        tri_limit = MAX_INDICES_PER_SUBMESH // 3
        for spec in submeshes:
            tris = spec["triangles"]
            flags = spec.get("flags", FLAG_NORMAL | FLAG_COLOR | FLAG_UV)
            for start in range(0, max(len(tris), 1), tri_limit):
                chunk = tris[start:start + tri_limit]
                if not chunk:
                    continue
                sm = SubMesh()
                sm.material_id = spec["material_id"]
                sm.flags = flags
                vmap = {}
                for tri in chunk:
                    for (ui, nrm, uv) in tri:
                        nrm = f32(nrm) if (nrm is not None and flags & FLAG_NORMAL) else None
                        uv = f32(uv) if (uv is not None and flags & FLAG_UV) else None
                        key = (ui, nrm, uv)
                        vi = vmap.get(key)
                        if vi is None:
                            vi = len(sm.positions)
                            if vi > 0xFFFF:
                                raise ValueError("submesh has too many vertices (>65535)")
                            vmap[key] = vi
                            sm.positions.append(self.uniq_pos[ui])
                            sm.pos_ids.append(ui)
                            sm.col_ids.append(ui)
                            if flags & FLAG_COLOR:
                                sm.colors.append(self.uniq_col[ui])
                            if flags & FLAG_NORMAL:
                                n = nrm if nrm is not None else (0.0, 1.0, 0.0)
                                if n not in nrm_ix:
                                    nrm_ix[n] = len(self.uniq_nrm)
                                    self.uniq_nrm.append(n)
                                sm.normals.append(n)
                                sm.nrm_ids.append(nrm_ix[n])
                            else:
                                sm.nrm_ids.append(0)
                            if flags & FLAG_UV:
                                t = uv if uv is not None else (0.0, 0.0)
                                if t not in uv_ix:
                                    uv_ix[t] = len(self.uniq_uv)
                                    self.uniq_uv.append(t)
                                sm.uvs.append(t)
                                sm.uv_ids.append(uv_ix[t] & 0xFF)
                            else:
                                sm.uv_ids.append(0)
                        sm.indices.append(vi)
                self.submeshes.append(sm)
        if not self.submeshes:
            raise ValueError("mesh has no triangles")
        self.has_maps = not self.interleaved
        self.maps_derived = False
        self.dirty = True

    def set_morph_deltas(self, delta_pos, delta_nrm=None, colors=None):
        """For MORPH slots: deltas per unique position of the base slot."""
        self.uniq_pos = [tuple(d) for d in delta_pos]
        self.uniq_nrm = [tuple(d) for d in delta_nrm] if delta_nrm is not None else []
        if colors is not None:
            self.uniq_col = [tuple(c) for c in colors]
        self.dirty = True

    # -------- writing
    def encode_node(self):
        if not self.dirty:
            return self.node
        kids = [b""] * 9
        b0 = bytearray(self.block0 if len(self.block0) >= 28 else b"\x00" * 24 + b"\x00\x00\x00\x04")
        if self.kind == self.KIND_MORPH or (not self.submeshes and self.mesh_count == 0):
            b0[24] = 0
            kids[0] = bytes(b0)
            kids[2] = b"".join(struct.pack("<3f", *p) for p in self.uniq_pos)
            if self.uniq_nrm and any(any(abs(x) > 0 for x in n) for n in self.uniq_nrm):
                kids[3] = b"".join(struct.pack("<3f", *n) for n in self.uniq_nrm)
            kids[4] = b"".join(bytes(c) for c in self.uniq_col)
            return fpac.Node(self.node.tag, self.node.offset_type, self.node.unk, kids)

        if len(self.submeshes) > 255:
            raise ValueError("too many submeshes (%d > 255)" % len(self.submeshes))
        b0[24] = len(self.submeshes)
        kids[0] = bytes(b0)

        b1 = bytearray()
        any_nrm = any_col = any_uv = False
        for sm in self.submeshes:
            nv = len(sm.positions)
            b1 += struct.pack("<HHIQI12x", len(sm.indices), sm.material_id, sm.flags, 0, nv)
            any_nrm |= bool(sm.flags & FLAG_NORMAL)
            any_col |= bool(sm.flags & FLAG_COLOR)
            any_uv |= bool(sm.flags & FLAG_UV)
            if self.interleaved:
                b1 += _write_interleaved(sm)
            else:
                b1 += _pad(b"".join(struct.pack("<3f", *p) for p in sm.positions))
                if sm.flags & FLAG_NORMAL:
                    b1 += _pad(b"".join(struct.pack("<3f", *n) for n in sm.normals))
                if sm.flags & FLAG_COLOR:
                    b1 += _pad(b"".join(bytes(c) for c in sm.colors))
                if sm.flags & FLAG_UV:
                    b1 += _pad(b"".join(struct.pack("<2f", *t) for t in sm.uvs))
            b1 += _pad(struct.pack("<%dH" % len(sm.indices), *sm.indices))
        kids[1] = bytes(b1)
        kids[2] = b"".join(struct.pack("<3f", *p) for p in self.uniq_pos)
        if any_nrm:
            kids[3] = b"".join(struct.pack("<3f", *n) for n in self.uniq_nrm)
        if any_col or self.uniq_col:
            kids[4] = b"".join(bytes(c) for c in self.uniq_col)
        if any_uv:
            kids[5] = b"".join(struct.pack("<2f", *t) for t in self.uniq_uv)
        if self.weights is not None:
            b6 = bytearray()
            for ws in self.weights:
                b6 += struct.pack("<I", len(ws))
                for bone, w in ws:
                    b6 += struct.pack("<If", bone, w)
            kids[6] = bytes(b6)
        if self.has_maps:
            b7 = bytearray()
            for sm in self.submeshes:
                nv = len(sm.positions)
                b7 += struct.pack("<%dH" % nv, *sm.pos_ids)
                if sm.flags & FLAG_NORMAL:
                    b7 += struct.pack("<%dH" % nv, *sm.nrm_ids)
                if sm.flags & FLAG_COLOR:
                    b7 += struct.pack("<%dH" % nv, *sm.col_ids)
                if sm.flags & FLAG_UV:
                    b7 += bytes(x & 0xFF for x in sm.uv_ids)
                    b7 += b"\x00" * nv
            kids[7] = bytes(b7)
        return fpac.Node(self.node.tag, self.node.offset_type, self.node.unk, kids)


# ---------------------------------------------------------------------
# Whole model
# ---------------------------------------------------------------------

class Model(object):
    def __init__(self, data, name=""):
        self.name = name
        self.root = fpac.parse(data)
        if self.root.name != "FPAC":
            raise ValueError("not a .fed file (root is %s)" % self.root.name)

        mat = self.root.find("FPACMAT")
        self.materials = [Material(i, bytes(c)) for i, c in enumerate(mat.children)] if mat else []

        tran = self.root.find("FPACTRAN")
        self.bones = [Bone(i, bytes(c)) for i, c in enumerate(tran.children)] if tran else []

        geom = self.root.find("FPACGEOM")
        self.meshes = [MeshSlot(i, c) for i, c in enumerate(geom.children)] if geom else []

        drms = self.root.find("FPACDRMS")
        if drms:
            for c in drms.children:
                if len(c) >= 4:
                    slot, bone = struct.unpack_from("<HH", c, 0)
                    if slot < len(self.meshes):
                        self.meshes[slot].bone = bone

        self.morph_sets = {}
        mor = self.root.find("FPACMOR")
        if mor:
            for c in mor.children:
                if len(c) >= 4:
                    base, n = struct.unpack_from("<HH", c, 0)
                    targets = list(struct.unpack_from("<%dI" % n, c, 4))
                    self.morph_sets[base] = targets
                    if base < len(self.meshes):
                        self.meshes[base].morph_targets = targets
                    for t in targets:
                        if t < len(self.meshes):
                            self.meshes[t].morph_base = base

    @classmethod
    def load(cls, path):
        import os
        with open(path, "rb") as f:
            return cls(f.read(), os.path.basename(path))

    # -------- skeleton
    def world_matrices(self):
        """World matrix (rest pose) of every FPACTRAN node."""
        out = [None] * len(self.bones)

        def calc(i, depth=0):
            if out[i] is not None:
                return out[i]
            b = self.bones[i]
            local = b.local_matrix()
            if 0 <= b.parent < len(self.bones) and b.parent != i and depth < 512:
                out[i] = mat_mul(calc(b.parent, depth + 1), local)
            else:
                out[i] = local
            return out[i]

        for i in range(len(self.bones)):
            calc(i)
        return out

    def slot_matrix(self, slot, world=None):
        """'slot vertices -> model space' matrix in the rest pose.

        Meshes WITH weights are already in model space (identity). RIGID
        meshes are in the local space of their node (FPACDRMS): real position
        = world[node] * v. E.g. the body hands have a node with world ~
        identity; the face/hair of the heads (mdh) and the 'mdl' expressions
        do not."""
        if slot.is_skinned or slot.bone is None or not (0 <= slot.bone < len(self.bones)):
            return mat_identity()
        return (world or self.world_matrices())[slot.bone]

    def set_submesh_material(self, slot_index, sub_index, new_id):
        """Changes the materialId of a submesh by editing ONLY the 2 bytes of
        its header (the rest of the file is identical). Returns the old id."""
        slot = self.meshes[slot_index]
        if slot.kind != MeshSlot.KIND_GEOMETRY:
            raise ValueError("slot %d has no geometry" % slot_index)
        b1 = bytearray(slot.node.children[1])
        p = 0
        for i, sm in enumerate(slot.submeshes):
            if i == sub_index:
                old = struct.unpack_from("<H", b1, p + 2)[0]
                struct.pack_into("<H", b1, p + 2, new_id)
                slot.node.children[1] = bytes(b1)
                sm.material_id = new_id
                return old
            p += 32 + _vertex_region_size(sm.flags, len(sm.positions)) + _a16(2 * len(sm.indices))
        raise ValueError("submesh %d does not exist (slot %d has %d)" % (sub_index, slot_index, len(slot.submeshes)))

    def add_material(self, template_index, tex_slot):
        """Appends a new material: copy of material `template_index` pointing
        at texture slot `tex_slot` (texA = texB). Returns the new index.
        EXPERIMENTAL - the game's own files never grow their material list."""
        mat = self.root.find("FPACMAT")
        raw = bytearray(self.materials[template_index].raw)
        if len(raw) < 16:
            raw = bytearray(struct.pack("<HH4BIHH", 0, 1, 255, 255, 255, 255, 0, 0, 0))
        struct.pack_into("<HH", raw, 12, tex_slot, tex_slot)
        mat.children.append(bytes(raw))
        self.materials.append(Material(len(self.materials), bytes(raw)))
        return len(self.materials) - 1

    def hide_slot(self, slot_index):
        """Hides a slot by zeroing its triangle indices (all triangles become
        degenerate) - the method from the original forum thread ('fill data
        with zero'). The file size does not change. Tested on the Vita, also
        for the hand slots (morph bases). Do NOT hide meshes by collapsing
        their vertices to a single point: that crashed the game."""
        slot = self.meshes[slot_index]
        if slot.kind != MeshSlot.KIND_GEOMETRY:
            raise ValueError("slot %d has no geometry" % slot_index)
        if slot.dirty:
            for sm in slot.submeshes:
                sm.indices = [0] * len(sm.indices)
            return
        b1 = bytearray(slot.node.children[1])
        p = 0
        for sm in slot.submeshes:
            p += 32 + _vertex_region_size(sm.flags, len(sm.positions))
            n = len(sm.indices)
            b1[p:p + 2 * n] = b"\x00" * (2 * n)
            sm.indices = [0] * n
            p += _a16(2 * n)
        slot.node.children[1] = bytes(b1)

    def geometry_slots(self):
        return [m for m in self.meshes if m.kind == MeshSlot.KIND_GEOMETRY]

    # -------- writing
    def to_bytes(self):
        geom = self.root.find("FPACGEOM")
        if geom is not None:
            for m in self.meshes:
                if m.dirty:
                    geom.children[m.index] = m.encode_node()
        return fpac.serialize(self.root)

    def save(self, path):
        data = self.to_bytes()
        with open(path, "wb") as f:
            f.write(data)
        return len(data)

    def summary(self):
        lines = ["%s: %d bones/nodes, %d materials, %d mesh slots" %
                 (self.name, len(self.bones), len(self.materials), len(self.meshes))]
        for m in self.meshes:
            if m.kind == MeshSlot.KIND_GEOMETRY:
                nv = sum(len(s.positions) for s in m.submeshes)
                nt = sum(len(s.indices) // 3 for s in m.submeshes)
                lines.append("  [%2d] mesh   mats=%-10s verts=%-5d tris=%-5d unique=%-5d %s node=%s%s" % (
                    m.index, ",".join(str(x) for x in m.material_ids), nv, nt, len(m.uniq_pos),
                    "SKINNED" if m.is_skinned else "rigid", m.bone, " (hidden)" if m.is_hidden else ""))
            elif m.kind == MeshSlot.KIND_MORPH:
                lines.append("  [%2d] morph of [%s]" % (m.index, m.morph_base))
        return "\n".join(lines)

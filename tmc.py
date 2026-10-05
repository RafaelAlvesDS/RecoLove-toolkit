"""
Reader for Dead or Alive 5 Last Round (PC) .TMC/.TMCL models - used to
import DOA bodies (e.g. Honoka) into RecoLove.

Based on the Noesis plugin doa5pc.py (geometry/textures) plus what it was
missing, decoded here: bone weights and the skeleton.

  TMC (container) -> blocks: 0 MdlGeo, 1 TTDM, 2 VtxLay, 3 IdxLay, 4 MtrCol,
  5 MdlInfo, 6 HieLay, 7 LHeader, 8 NodeLay, 9 GlblMtx, 10 BnOfsMtx, ...
  Every block header: char[8], u32 flg, hsize, size, c1, c2, c3,
  o1, o2, o3, flg2; offset table u32[c1] at base+o1.

  * Vertex layout per 'decl': pairs (offset<<16, usage<<16 | D3D9 type):
    0x2 pos f32x3, 0x30002 normal f32x3, 0x1000D weights u8x4 (sum 255),
    0x20005 indices u8x4, 0x60003 tangent, 0x5000B/0x5000A UV half.
  * Bone indices go through a per-object PALETTE stored in the NodeObj of
    NodeLay named like the object (e.g. 'WGT_body'): {u32 obj, u32 n,
    u32 ?, u32 ?, mat4, u32 nodes[n]} at nodeobj.base + offs[0].
  * HieLay: per node, local mat4 (row-major, translation in row 3) + i32 parent.
  * Bind pose = inverse(BnOfsMtx) (GlblMtx is NOT the bind pose).
  * Indices are u16 triangle strips.
  * Textures: DDS inside the TMCL (TTDL), mapped through TTDH.
"""
import struct

import numpy as np

try:
    from . import gxt
except ImportError:
    import gxt


class _H(object):
    def __init__(self, d, base):
        self.base = base
        self.name = d[base:base + 8].rstrip(b"\0").decode("ascii", "replace")
        (self.flg1, self.hsize, self.size, self.c1, self.c2, self.c3,
         self.o1, self.o2, self.o3, self.flg2) = struct.unpack_from("<10I", d, base + 8)
        self.offs = list(struct.unpack_from("<%dI" % self.c1, d, base + self.o1)) if self.c1 else []


class TmcObject(object):
    def __init__(self):
        self.name = ""
        self.positions = []   # (x,y,z)
        self.normals = []
        self.uvs = []         # (u, v) DirectX convention (origin at the top)
        self.weights = []     # [(indice_do_no, peso)]
        self.triangles = []   # (a,b,c) sem ordem garantida (vem de strip)
        self.textures = []    # [(slot, kind, global_texture_id)]


class Tmc(object):
    def __init__(self, tmc_path, tmcl_path=None):
        with open(tmc_path, "rb") as f:
            self.d = d = f.read()
        if tmcl_path is None:
            tmcl_path = tmc_path[:-3] + ("TMCL" if tmc_path[-3:].isupper() else "tmcl")
        try:
            with open(tmcl_path, "rb") as f:
                self.l = f.read()
        except OSError:
            self.l = b""
        top = _H(d, 0)
        self.blocks = [(_H(d, o) if o else None) for o in top.offs]
        self._read_nodes()
        self._read_objects()

    # ---------------------------------------------------------------- nos
    def _read_nodes(self):
        d, B = self.d, self.blocks
        nl = B[8]
        self.node_names = []
        self.node_objs = {}
        for o in nl.offs:
            h = _H(d, nl.base + o)
            self.node_names.append(d[h.base + 0x40:h.base + 0x80].split(b"\0")[0].decode("ascii", "replace"))
            self.node_objs[self.node_names[-1]] = h
        n = len(self.node_names)
        hl = B[6]
        self.parents = []
        for o in hl.offs:
            self.parents.append(struct.unpack_from("<i", d, hl.base + o + 0x40)[0])
        bo = B[10]
        self.bind = []  # bind world matrix (row-major, v' = v @ M)
        for o in bo.offs:
            m = np.frombuffer(d, "<f4", 16, bo.base + o).reshape(4, 4).astype(np.float64)
            self.bind.append(np.linalg.inv(m))
        assert len(self.parents) == n == len(self.bind)

    def bind_position(self, i):
        return self.bind[i][3, :3]

    def node_index(self, name):
        return self.node_names.index(name)

    # ------------------------------------------------------------- meshes
    def _palette(self, objname):
        h = self.node_objs.get(objname)
        if h is None or not h.offs:
            return None
        b = h.base + h.offs[0]
        _obj, n = struct.unpack_from("<2I", self.d, b)
        return list(struct.unpack_from("<%dI" % n, self.d, b + 16 + 64))

    def _read_objects(self):
        d, B = self.d, self.blocks
        geo, vtx, idx = B[0], B[2], B[3]
        self.objects = []
        for oo in geo.offs:
            if not oo:
                continue
            oh = _H(d, geo.base + oo)
            dh = _H(d, geo.base + oo + oh.o3)
            ob = TmcObject()
            ob.name = d[oh.base + 0x50:oh.base + 0x90].split(b"\0")[0].decode("ascii", "replace")
            pal = self._palette(ob.name)
            decls = []
            for do in dh.offs:
                b = dh.base + do
                ib, ic, vc = struct.unpack_from("<3I", d, b + 0xC)
                vb, vsize, nl = struct.unpack_from("<3I", d, b + 0x30)
                lay = {}
                for i in range(nl):
                    off, typ = struct.unpack_from("<2I", d, b + 0x40 + 8 * i)
                    lay[typ] = off >> 16
                decls.append((ib, ic, vc, vb, vsize, lay))
            mats = []
            for mo in oh.offs:
                if not mo:
                    continue
                b = oh.base + mo
                v = struct.unpack_from("<2I4xI40xI4xI3I2f2I4I4I", d, b)
                ntex, decl = v[2], v[3]
                istart, icount, vstart, vcount = v[-4:]
                texs = [struct.unpack_from("<3I", d, b + 0xD0 + k * 0x70) for k in range(ntex)]
                mats.append((decl, istart, icount, texs))
            for k, (ib, ic, vc, vb, vsize, lay) in enumerate(decls):
                base = vtx.base + vtx.offs[vb]
                raw = np.frombuffer(d, np.uint8, vsize * vc, base).reshape(vc, vsize)
                first = len(ob.positions)
                pos = raw[:, lay[2]:lay[2] + 12].copy().view("<f4").reshape(-1, 3)
                ob.positions += [tuple(map(float, p)) for p in pos]
                if 0x30002 in lay:
                    nrm = raw[:, lay[0x30002]:lay[0x30002] + 12].copy().view("<f4").reshape(-1, 3)
                    ob.normals += [tuple(map(float, n)) for n in nrm]
                uvk = 0x5000B if 0x5000B in lay else (0x5000A if 0x5000A in lay else None)
                if uvk is not None:
                    uv = raw[:, lay[uvk]:lay[uvk] + 4].copy().view("<f2").reshape(-1, 2).astype(np.float64)
                    ob.uvs += [tuple(map(float, t)) for t in uv]
                if 0x1000D in lay and 0x20005 in lay:
                    w = raw[:, lay[0x1000D]:lay[0x1000D] + 4]
                    bi = raw[:, lay[0x20005]:lay[0x20005] + 4]
                    for i in range(vc):
                        ob.weights.append([((pal[bi[i, j]] if pal else int(bi[i, j])), w[i, j] / 255.0)
                                           for j in range(4) if w[i, j]])
                for (decl, istart, icount, texs) in mats:
                    if decl != k:
                        continue
                    ob.textures = texs
                    strip = struct.unpack_from("<%dH" % icount, d, idx.base + idx.offs[ib] + istart * 2)
                    for i in range(len(strip) - 2):
                        a, b_, c = strip[i], strip[i + 1], strip[i + 2]
                        if a == b_ or b_ == c or a == c:
                            continue
                        tri = (a, b_, c) if i % 2 == 0 else (b_, a, c)
                        ob.triangles.append(tuple(first + x for x in tri))
            self.objects.append(ob)

    def object(self, name):
        for o in self.objects:
            if o.name == name:
                return o
        raise KeyError(name)

    # ------------------------------------------------------------ textures
    def texture(self, tex_id):
        """Global texture tex_id (as used by materials) -> numpy [h,w,4] RGBA."""
        d, ttdm = self.d, self.blocks[1]
        ttdh = _H(d, ttdm.base + ttdm.hsize)
        fl, tid = struct.unpack_from("<2I", d, ttdh.base + ttdh.offs[tex_id])
        if fl:
            ttdl = _H(d, ttdm.base + ttdm.o3)
            size = struct.unpack_from("<I", d, ttdl.base + ttdl.o2 + 4 * tid)[0]
            dds = self.l[0x80 + ttdl.offs[tid]:0x80 + ttdl.offs[tid] + size]
        else:
            size = struct.unpack_from("<I", d, ttdm.base + ttdm.o2 + 4 * tid)[0]
            dds = d[ttdm.base + ttdm.offs[tid]:ttdm.base + ttdm.offs[tid] + size]
        return decode_dds(dds)


def decode_dds(dds):
    h, w = struct.unpack_from("<2I", dds, 12)
    fourcc = dds[84:88]
    bw, bh = (w + 3) // 4, (h + 3) // 4
    if fourcc == b"DXT5":
        blocks = np.frombuffer(dds, np.uint8, bw * bh * 16, 128).reshape(-1, 16)
    elif fourcc == b"DXT1":
        c = np.frombuffer(dds, np.uint8, bw * bh * 8, 128).reshape(-1, 8)
        blocks = np.zeros((c.shape[0], 16), np.uint8)
        blocks[:, 0] = 255   # alpha 255 (opaque DXT1)
        blocks[:, 1] = 255
        blocks[:, 8:] = c
    else:
        raise NotImplementedError("DDS %r is not supported" % fourcc)
    px = gxt.decode_dxt5_blocks(blocks)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w])

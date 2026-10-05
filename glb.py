"""
Exports a whole .fed model to .glb (binary glTF 2.0, single file): skeleton
(FPACTRAN) as joints, a skin with the real weights, morphs as morph targets
and the CORRECT textures (resolved through FPACMAT, see fedmodel.py)
embedded as PNG.

Good for online/Windows 3D viewers and it also opens in Blender's built-in
glTF importer - but to edit and send back to the game use the Blender
add-on, which keeps everything the format needs.

Conventions: glTF is Y-up like the game (no axis swap); glTF UVs have their
origin at the top: v_gltf = v_raw + 1.0; triangles are flipped (the game
stores them clockwise).
"""
import json
import struct
import zlib

import numpy as np

try:
    from . import fedmodel as fm
except ImportError:
    import fedmodel as fm

FLOAT, UBYTE, USHORT, UINT = 5126, 5121, 5123, 5125
ARRAY, ELEMENT = 34962, 34963


def png_bytes(rgba):
    """numpy uint8 [h,w,4] -> PNG bytes in memory."""
    h, w = rgba.shape[:2]
    raw = np.zeros((h, 1 + w * 4), dtype=np.uint8)
    raw[:, 1:] = rgba.reshape(h, w * 4)
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b""))


class _Writer(object):
    def __init__(self):
        self.bin = bytearray()
        self.views = []
        self.accessors = []

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, arr, ctype, atype, target=None, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = {"bufferView": self.view(arr.tobytes(), target), "componentType": ctype,
             "count": int(arr.shape[0]), "type": atype}
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"] = [float(x) for x in arr.min(axis=0)]
            a["max"] = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(a)
        return len(self.accessors) - 1


def _col_major(m):
    return [float(m[r][c]) for c in range(4) for r in range(4)]


def export_model_glb(model, out_path, tex=None, name="recolove"):
    w = _Writer()
    world = model.world_matrices()
    nb = len(model.bones)

    # ---- nodes: bones first (indices 0..nb-1)
    nodes = []
    for b in model.bones:
        nodes.append({"name": b.name, "matrix": _col_major(b.local_matrix())})
    for b in model.bones:
        if 0 <= b.parent < nb and b.parent != b.index:
            nodes[b.parent].setdefault("children", []).append(b.index)
    roots = [b.index for b in model.bones if not (0 <= b.parent < nb) or b.parent == b.index]
    ibm = np.array([_col_major(fm.mat_invert_affine(m)) for m in world], dtype=np.float32)
    skin = {"joints": list(range(nb)), "inverseBindMatrices": w.accessor(ibm, FLOAT, "MAT4")}
    if roots:
        skin["skeleton"] = roots[0]

    # ---- materials / textures
    materials, textures, images = [], [], []
    mat_index = {}
    img_index = {}
    for m in model.materials:
        mat = {"name": "mat%02d" % m.index, "doubleSided": False,
               "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.8}}
        slot = m.texture_slot(tex) if tex is not None else None
        if slot is not None and tex is not None and tex.has(slot):
            if slot not in img_index:
                _w, _h, rgba = tex.decode(slot)
                images.append({"mimeType": "image/png", "bufferView": w.view(png_bytes(rgba)),
                               "name": "tex%02d" % slot})
                textures.append({"source": len(images) - 1})
                img_index[slot] = (len(textures) - 1, bool((rgba[:, :, 3] < 250).any()))
            ti, has_alpha = img_index[slot]
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": ti}
            if has_alpha:
                mat["alphaMode"] = "MASK"
                mat["alphaCutoff"] = 0.5
        else:
            c = m.color
            mat["pbrMetallicRoughness"]["baseColorFactor"] = [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0]
        materials.append(mat)
        mat_index[m.index] = len(materials) - 1

    # ---- meshes
    meshes = []
    mesh_nodes = []
    for slot in model.geometry_slots():
        bind = model.slot_matrix(slot, world)
        rot = np.array([r[:3] for r in bind[:3]], dtype=np.float64)
        trans = np.array([bind[i][3] for i in range(3)], dtype=np.float64)
        prims = []
        target_names = ["morph_slot%02d" % t for t in slot.morph_targets if t < len(model.meshes)]
        for sm in slot.submeshes:
            nv = len(sm.positions)
            pos = np.array(sm.positions, dtype=np.float64) @ rot.T + trans
            attrs = {"POSITION": w.accessor(pos.astype(np.float32), FLOAT, "VEC3", ARRAY, minmax=True)}
            if sm.normals:
                n = np.array(sm.normals, dtype=np.float64) @ rot.T
                n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
                attrs["NORMAL"] = w.accessor(n.astype(np.float32), FLOAT, "VEC3", ARRAY)
            if sm.uvs:
                uv = np.array(sm.uvs, dtype=np.float32)
                uv[:, 1] += 1.0
                attrs["TEXCOORD_0"] = w.accessor(uv, FLOAT, "VEC2", ARRAY)
            if sm.colors:
                attrs["COLOR_0"] = w.accessor(np.array(sm.colors, dtype=np.uint8), UBYTE, "VEC4", ARRAY,
                                              normalized=True)
            # weights: up to 8 influences (2 sets of 4)
            infl = [sorted(slot.vertex_weights(u), key=lambda t: -t[1])[:8] for u in sm.pos_ids]
            sets = 2 if any(len(x) > 4 for x in infl) else 1
            J = np.zeros((nv, 4 * sets), dtype=np.uint16)
            W = np.zeros((nv, 4 * sets), dtype=np.float32)
            for i, ws in enumerate(infl):
                tot = sum(x for _, x in ws) or 1.0
                for k, (b, x) in enumerate(ws):
                    J[i, k] = b if b < nb else 0
                    W[i, k] = x / tot
            for s in range(sets):
                attrs["JOINTS_%d" % s] = w.accessor(J[:, 4 * s:4 * s + 4], USHORT, "VEC4", ARRAY)
                attrs["WEIGHTS_%d" % s] = w.accessor(W[:, 4 * s:4 * s + 4], FLOAT, "VEC4", ARRAY)
            tris = np.array(sm.triangles, dtype=np.uint32).reshape(-1, 3)[:, ::-1]
            prim = {"attributes": attrs, "indices": w.accessor(tris.reshape(-1), UINT, "SCALAR", ELEMENT),
                    "material": mat_index.get(sm.material_id, 0)}
            if target_names:
                targets = []
                for t in slot.morph_targets:
                    if t >= len(model.meshes):
                        continue
                    ms = model.meshes[t]
                    d = np.array([ms.uniq_pos[u] if u < len(ms.uniq_pos) else (0, 0, 0) for u in sm.pos_ids],
                                 dtype=np.float64) @ rot.T
                    tg = {"POSITION": w.accessor(d.astype(np.float32), FLOAT, "VEC3", ARRAY, minmax=True)}
                    if sm.normals and ms.uniq_nrm:
                        dn = np.array([ms.uniq_nrm[u] if u < len(ms.uniq_nrm) else (0, 0, 0) for u in sm.pos_ids],
                                      dtype=np.float64) @ rot.T
                        tg["NORMAL"] = w.accessor(dn.astype(np.float32), FLOAT, "VEC3", ARRAY)
                    targets.append(tg)
                prim["targets"] = targets
            prims.append(prim)
        mesh = {"name": "slot%02d" % slot.index, "primitives": prims}
        if target_names:
            mesh["extras"] = {"targetNames": target_names}
            mesh["weights"] = [0.0] * len(target_names)
        meshes.append(mesh)
        nodes.append({"name": "slot%02d" % slot.index, "mesh": len(meshes) - 1, "skin": 0})
        mesh_nodes.append(len(nodes) - 1)

    doc = {
        "asset": {"version": "2.0", "generator": "RecoLove Modding Toolkit"},
        "scene": 0,
        "scenes": [{"name": name, "nodes": roots + mesh_nodes}],
        "nodes": nodes,
        "meshes": meshes,
        "skins": [skin],
        "materials": materials,
        "accessors": w.accessors,
        "bufferViews": w.views,
        "buffers": [{"byteLength": len(w.bin)}],
    }
    if textures:
        doc["textures"] = textures
        doc["images"] = images
        doc["samplers"] = [{"magFilter": 9729, "minFilter": 9729}]
        for t in textures:
            t["sampler"] = 0

    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binp = bytes(w.bin) + b"\x00" * ((4 - len(w.bin) % 4) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binp))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(binp), 0x004E4942) + binp
    with open(out_path, "wb") as f:
        f.write(out)
    return len(out)

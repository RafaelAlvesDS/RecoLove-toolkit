"""
Orthographic preview renderer (numpy): z-buffer, per-pixel texture sampling
(interpolated UVs), vertex color and simple normal shading. Uses
fedmodel/texfile, so the textures are the CORRECT ones (FPACMAT) and rigid
meshes are placed by their node matrix.

Usage:
    python quickrender.py <fed> <out.png> [tex] [view=xy|-xy|zy|xz]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fedmodel as fm
import texfile as tf

# view -> (screen x axis, sign, screen y axis, depth axis, depth sign)
_AXES = {
    "xy": (0, 1.0, 1, 2, 1.0),     # front
    "-xy": (0, -1.0, 1, 2, -1.0),  # back
    "zy": (2, 1.0, 1, 0, -1.0),    # side
    "xz": (0, 1.0, 2, 1, 1.0),     # top
}


def _collect(model, tex, only_slots=None):
    world = model.world_matrices()
    images = {}
    items = []  # (pos Nx3, nrm Nx3|None, uv Nx2|None, col Nx4|None, tris Mx3, img|None)
    for slot in model.geometry_slots():
        if only_slots is not None and slot.index not in only_slots:
            continue
        bind = np.array(model.slot_matrix(slot, world), dtype=np.float64)
        for sm in slot.submeshes:
            if not sm.indices:
                continue
            pos = np.array(sm.positions, dtype=np.float64) @ bind[:3, :3].T + bind[:3, 3]
            nrm = None
            if sm.normals:
                nrm = np.array(sm.normals, dtype=np.float64) @ bind[:3, :3].T
            img = None
            mat = model.materials[sm.material_id] if sm.material_id < len(model.materials) else None
            s = mat.texture_slot(tex) if (mat is not None and tex is not None) else None
            if s is not None and tex.has(s):
                if s not in images:
                    images[s] = tex.decode(s)[2]
                img = images[s]
            items.append((pos, nrm,
                          np.array(sm.uvs, dtype=np.float64) if sm.uvs else None,
                          np.array(sm.colors, dtype=np.float64) / 255.0 if sm.colors else None,
                          np.array(sm.indices, dtype=np.int64).reshape(-1, 3), img))
    return items


def render_model(model, tex=None, axis="xy", width=460, bg=(40, 40, 40), only_slots=None, max_height=None):
    """-> (W, H, numpy uint8 [H, W, 4])"""
    ax, sx, ay, az, sz = _AXES[axis]
    items = _collect(model, tex, only_slots)
    if not items:
        raise ValueError("model has no geometry")
    allp = np.concatenate([it[0] for it in items])
    X = allp[:, ax] * sx
    Y = allp[:, ay]
    minx, maxx, miny, maxy = X.min(), X.max(), Y.min(), Y.max()
    pad = 12
    scale = (width - 2 * pad) / max(maxx - minx, 1e-6)
    H = int((maxy - miny) * scale) + 2 * pad
    W = width
    limit = min(3 * width, max_height) if max_height else 3 * width
    if H > limit:  # tall model: fit by height instead
        H = limit
        scale = (H - 2 * pad) / max(maxy - miny, 1e-6)
        W = min(width, int((maxx - minx) * scale) + 2 * pad)
    color = np.zeros((H, W, 3), dtype=np.float64)
    color[:] = np.array(bg) / 255.0
    zbuf = np.full((H, W), -np.inf)
    light = np.array([0.3, 0.5, 0.8])
    light /= np.linalg.norm(light)

    for pos, nrm, uv, col, tris, img in items:
        px = pad + (pos[:, ax] * sx - minx) * scale
        py = pad + (maxy - pos[:, ay]) * scale
        pz = pos[:, az] * sz
        # normal in screen space (for lighting)
        if nrm is not None:
            sn = np.stack([nrm[:, ax] * sx, nrm[:, ay], nrm[:, az] * sz], axis=1)
            shade = 0.45 + 0.55 * np.clip(sn @ light, 0, 1)
        else:
            shade = np.full(len(pos), 0.85)
        for (a, b, c) in tris:
            x0, x1, x2 = px[a], px[b], px[c]
            y0, y1, y2 = py[a], py[b], py[c]
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(den) < 1e-9:
                continue
            xa = max(int(min(x0, x1, x2)), 0)
            xb = min(int(max(x0, x1, x2)) + 1, W)
            ya = max(int(min(y0, y1, y2)), 0)
            yb = min(int(max(y0, y1, y2)) + 1, H)
            if xa >= xb or ya >= yb:
                continue
            gx, gy = np.meshgrid(np.arange(xa, xb) + 0.5, np.arange(ya, yb) + 0.5)
            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
            if not inside.any():
                continue
            z = w0 * pz[a] + w1 * pz[b] + w2 * pz[c]
            zb = zbuf[ya:yb, xa:xb]
            m = inside & (z > zb)
            if not m.any():
                continue
            if img is not None and uv is not None:
                u = w0 * uv[a, 0] + w1 * uv[b, 0] + w2 * uv[c, 0]
                v = 1.0 + (w0 * uv[a, 1] + w1 * uv[b, 1] + w2 * uv[c, 1])
                th, tw = img.shape[:2]
                tx = (np.floor(u * tw).astype(np.int64)) % tw
                ty = (np.floor(v * th).astype(np.int64)) % th
                texel = img[ty, tx]
                m = m & (texel[..., 3] >= 128)  # alpha test (hair/eyelashes)
                if not m.any():
                    continue
                rgb = texel[..., :3] / 255.0
            else:
                rgb = np.full(gx.shape + (3,), 0.8)
            if col is not None:
                vc = w0[..., None] * col[a, :3] + w1[..., None] * col[b, :3] + w2[..., None] * col[c, :3]
                rgb = rgb * vc
            sh = w0 * shade[a] + w1 * shade[b] + w2 * shade[c]
            rgb = rgb * sh[..., None]
            zb[m] = z[m]
            color[ya:yb, xa:xb][m] = rgb[m]

    out = np.empty((H, W, 4), dtype=np.uint8)
    out[..., :3] = np.clip(color * 255.0, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return W, H, out


def main():
    fed_path, out_path = sys.argv[1], sys.argv[2]
    tex_path = None
    axis = "xy"
    for extra in sys.argv[3:]:
        if extra.lower().endswith(".tex"):
            tex_path = extra
        else:
            axis = extra
    model = fm.Model.load(fed_path)
    if tex_path is None:
        for d in (os.path.dirname(os.path.abspath(fed_path)),
                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(fed_path))), "CharaTex_out")):
            c = tf.find_tex_candidates(fed_path, d)
            if c:
                tex_path = c[0]
                break
    tex = tf.TexFile.load(tex_path) if tex_path else None
    W, H, img = render_model(model, tex, axis, width=700)
    import glb
    with open(out_path, "wb") as f:
        f.write(glb.png_bytes(img))
    print("OK: %s (%dx%d, texture: %s)" % (out_path, W, H, os.path.basename(tex_path) if tex_path else "none"))


if __name__ == "__main__":
    main()

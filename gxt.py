"""
NATIVE decoder/encoder for RecoLove GXT textures (PS Vita) - no
GXTConvert.exe / texconv.exe / psp2gxt.exe needed.

Every one of the 3487 textures in the game (750 .tex files) uses exactly the
same format: 1 texture per GXT, type 0 (SWIZZLED), format 0x87000000
(UBC3 = DXT5), 1 mip level. Only that path is implemented (other formats
raise a clear error). Decoding is pixel-identical to GXTConvert.

GXT layout (0x40-byte header + data):
    0x00 "GXT\0", uint32 version (0x10000003), uint32 numTextures (1),
         uint32 dataOffset (0x40), uint32 dataSize, uint32 numP4, numP8, pad
    0x20 texture: uint32 offset, uint32 size, int32 paletteIndex (-1),
         uint32 flags, uint32 type (0=swizzled), uint32 format,
         uint16 width, uint16 height, uint16 mipmaps, uint16 pad

Vita swizzle: the 4x4 DXT blocks are in Morton (Z) order inside squares of
side min(bw, bh) - same algorithm as GXTConvert.

Requires numpy (Blender ships it).
"""
import struct
import numpy as np

GXT_MAGIC = b"GXT\x00"
FMT_UBC3 = 0x87000000
TYPE_SWIZZLED = 0x00000000
HEADER_SIZE = 0x40


class GxtInfo(object):
    def __init__(self, width, height, fmt, tex_type, mips, data_offset, data_size):
        self.width = width
        self.height = height
        self.fmt = fmt
        self.tex_type = tex_type
        self.mips = mips
        self.data_offset = data_offset
        self.data_size = data_size


def read_info(gxt):
    if gxt[:4] != GXT_MAGIC:
        raise ValueError("not a GXT")
    ntex = struct.unpack_from("<I", gxt, 8)[0]
    if ntex != 1:
        raise ValueError("GXT with %d textures is not supported" % ntex)
    off, size, _pal, _flags, ttype, fmt, w, h, mips = struct.unpack_from("<IIiIIIHHH", gxt, 0x20)
    return GxtInfo(w, h, fmt, ttype, mips, off, size)


# ---------------------------------------------------------------------
# swizzle (Morton order of the blocks)
# ---------------------------------------------------------------------

def _compact1by1(x):
    x = x & 0x55555555
    x = (x | (x >> 1)) & 0x33333333
    x = (x | (x >> 2)) & 0x0F0F0F0F
    x = (x | (x >> 4)) & 0x00FF00FF
    x = (x | (x >> 8)) & 0x0000FFFF
    return x


def _swizzle_order(bw, bh):
    """For each block i in FILE (swizzled) order, returns its linear index
    (y*bw + x) in the image."""
    i = np.arange(bw * bh, dtype=np.int64)
    mn = min(bw, bh)
    k = mn.bit_length() - 1
    mx = _compact1by1(i)
    my = _compact1by1(i >> 1)
    hi = (i >> (2 * k)) << (2 * k)
    if bh < bw:
        j = hi | ((my & (mn - 1)) << k) | (mx & (mn - 1))
        x = j // bh
        y = j % bh
    else:
        j = hi | ((mx & (mn - 1)) << k) | (my & (mn - 1))
        x = j % bw
        y = j // bw
    return y * bw + x


# ---------------------------------------------------------------------
# DXT5 decode
# ---------------------------------------------------------------------

def _565_to_rgb(c):
    r = ((c >> 11) & 31).astype(np.int32)
    g = ((c >> 5) & 63).astype(np.int32)
    b = (c & 31).astype(np.int32)
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1)


def decode_dxt5_blocks(blocks):
    """blocks: uint8 (N,16) -> uint8 (N,16,4) RGBA (16 pixels per block,
    row order inside the block)."""
    n = blocks.shape[0]
    a0 = blocks[:, 0].astype(np.int32)
    a1 = blocks[:, 1].astype(np.int32)
    abits = np.zeros(n, dtype=np.uint64)
    for k in range(6):
        abits |= blocks[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    aidx = np.stack([((abits >> np.uint64(3 * p)) & np.uint64(7)).astype(np.int32) for p in range(16)], axis=1)

    apal = np.zeros((n, 8), dtype=np.int32)
    apal[:, 0] = a0
    apal[:, 1] = a1
    gt = a0 > a1
    for k in range(1, 7):  # 8-level mode
        v8 = ((7 - k) * a0 + k * a1) // 7
        if k <= 4:
            v6 = ((5 - k) * a0 + k * a1) // 5
        else:
            v6 = np.zeros(n, dtype=np.int32) if k == 5 else np.full(n, 255, dtype=np.int32)
        apal[:, 1 + k] = np.where(gt, v8, v6)
    alpha = np.take_along_axis(apal, aidx, axis=1)

    c0 = blocks[:, 8].astype(np.uint16) | (blocks[:, 9].astype(np.uint16) << 8)
    c1 = blocks[:, 10].astype(np.uint16) | (blocks[:, 11].astype(np.uint16) << 8)
    rgb0 = _565_to_rgb(c0)
    rgb1 = _565_to_rgb(c1)
    # DXT5: color block is always in 4-color mode
    pal = np.stack([rgb0, rgb1, (2 * rgb0 + rgb1) // 3, (rgb0 + 2 * rgb1) // 3], axis=1)  # (n,4,3)
    cbits = (blocks[:, 12].astype(np.uint32) | (blocks[:, 13].astype(np.uint32) << 8) |
             (blocks[:, 14].astype(np.uint32) << 16) | (blocks[:, 15].astype(np.uint32) << 24))
    cidx = np.stack([((cbits >> (2 * p)) & 3).astype(np.int64) for p in range(16)], axis=1)
    rgb = np.take_along_axis(pal, cidx[:, :, None].repeat(3, axis=2), axis=1)

    out = np.empty((n, 16, 4), dtype=np.uint8)
    out[:, :, :3] = rgb
    out[:, :, 3] = alpha
    return out


def decode(gxt):
    """GXT (bytes) -> (width, height, numpy uint8 [h, w, 4] RGBA, row 0 = top)."""
    info = read_info(gxt)
    if info.fmt != FMT_UBC3:
        raise NotImplementedError("formato GXT 0x%08X not supported (DXT5/UBC3 only)" % info.fmt)
    if info.tex_type != TYPE_SWIZZLED:
        raise NotImplementedError("tipo GXT 0x%08X not supported (swizzled only)" % info.tex_type)
    w, h = info.width, info.height
    bw, bh = (w + 3) // 4, (h + 3) // 4
    raw = np.frombuffer(gxt, dtype=np.uint8, count=bw * bh * 16, offset=info.data_offset).reshape(-1, 16)
    linear = np.empty_like(raw)
    linear[_swizzle_order(bw, bh)] = raw
    px = decode_dxt5_blocks(linear)  # (bw*bh, 16, 4)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return w, h, np.ascontiguousarray(img[:h, :w])


# ---------------------------------------------------------------------
# DXT5 encode (range fit along the principal axis - quality similar to
# "fast" encoders like squish range fit; vectorized with numpy)
# ---------------------------------------------------------------------

def _rgb_to_565(rgb):
    r = np.clip(np.round(rgb[..., 0] * 31 / 255.0), 0, 31).astype(np.uint16)
    g = np.clip(np.round(rgb[..., 1] * 63 / 255.0), 0, 63).astype(np.uint16)
    b = np.clip(np.round(rgb[..., 2] * 31 / 255.0), 0, 31).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def encode_dxt5_blocks(px):
    """px: uint8 (N,16,4) -> uint8 (N,16) DXT5 blocks."""
    n = px.shape[0]
    out = np.zeros((n, 16), dtype=np.uint8)

    # ---- alpha: 8-level mode with a0=max, a1=min
    a = px[:, :, 3].astype(np.int32)
    amax = a.max(axis=1)
    amin = a.min(axis=1)
    flat = amax == amin
    amax_e = np.where(flat & (amax < 255), amax + 1, amax)
    amin_e = np.where(flat & (amax >= 255), amin - 1, amin)
    apal = np.stack([amax_e, amin_e] + [((7 - k) * amax_e + k * amin_e) // 7 for k in range(1, 7)], axis=1)
    aidx = np.abs(a[:, :, None] - apal[:, None, :]).argmin(axis=2).astype(np.uint64)
    out[:, 0] = amax_e
    out[:, 1] = amin_e
    abits = np.zeros(n, dtype=np.uint64)
    for p in range(16):
        abits |= aidx[:, p] << np.uint64(3 * p)
    for k in range(6):
        out[:, 2 + k] = ((abits >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)

    # ---- color: principal axis (power iteration) + projected extremes
    rgb = px[:, :, :3].astype(np.float32)
    mean = rgb.mean(axis=1, keepdims=True)
    d = rgb - mean
    cov = np.einsum("npi,npj->nij", d, d)
    axis = np.ones((n, 3), dtype=np.float32)
    for _ in range(8):
        axis = np.einsum("nij,nj->ni", cov, axis)
        norm = np.linalg.norm(axis, axis=1, keepdims=True)
        axis = np.where(norm > 1e-6, axis / np.maximum(norm, 1e-12), np.array([0.577, 0.577, 0.577], dtype=np.float32))
    proj = np.einsum("npi,ni->np", d, axis)
    lo = mean[:, 0, :] + axis * proj.min(axis=1, keepdims=True)
    hi = mean[:, 0, :] + axis * proj.max(axis=1, keepdims=True)
    c0 = _rgb_to_565(np.clip(hi, 0, 255))
    c1 = _rgb_to_565(np.clip(lo, 0, 255))
    # 4-color mode requires c0 > c1
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1
    rgb0 = _565_to_rgb(c0).astype(np.float32)
    rgb1 = _565_to_rgb(c1).astype(np.float32)
    pal = np.stack([rgb0, rgb1, (2 * rgb0 + rgb1) / 3.0, (rgb0 + 2 * rgb1) / 3.0], axis=1)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(axis=3)
    cidx = dist.argmin(axis=2).astype(np.uint32)
    cidx[same] = 0
    cbits = np.zeros(n, dtype=np.uint32)
    for p in range(16):
        cbits |= cidx[:, p] << np.uint32(2 * p)
    out[:, 8] = c0 & 0xFF
    out[:, 9] = c0 >> 8
    out[:, 10] = c1 & 0xFF
    out[:, 11] = c1 >> 8
    for k in range(4):
        out[:, 12 + k] = (cbits >> np.uint32(8 * k)) & 0xFF
    return out


def encode(rgba, width, height, header_template=None):
    """numpy uint8 [h, w, 4] (row 0 = top) -> complete GXT bytes.
    If `header_template` (original GXT) is given and the size matches,
    the original header is reused byte for byte."""
    if width % 4 or height % 4:
        raise ValueError("width/height must be multiples of 4 (got %dx%d)" % (width, height))
    for v in (width, height):
        if v & (v - 1):
            raise ValueError("width/height must be powers of 2 (got %dx%d)" % (width, height))
    img = np.ascontiguousarray(rgba, dtype=np.uint8).reshape(height, width, 4)
    bw, bh = width // 4, height // 4
    px = img.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bw * bh, 16, 4)
    linear = encode_dxt5_blocks(px)
    swz = linear[_swizzle_order(bw, bh)]
    payload = swz.tobytes()

    header = None
    if header_template is not None:
        try:
            info = read_info(header_template)
            if (info.width, info.height, info.fmt, info.tex_type) == (width, height, FMT_UBC3, TYPE_SWIZZLED):
                header = bytes(header_template[:HEADER_SIZE])
        except ValueError:
            header = None
    if header is None:
        header = struct.pack("<4sIIIIIII", GXT_MAGIC, 0x10000003, 1, HEADER_SIZE, len(payload), 0, 0, 0)
        header += struct.pack("<IIiIIIHHHH", HEADER_SIZE, len(payload), -1, 0, TYPE_SWIZZLED, FMT_UBC3,
                              width, height, 1, 0)
    return header + payload

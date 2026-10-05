"""
Minimal PNG decoder/encoder (RGBA8, no interlace) using only the standard
library (zlib). Used to read PNGs edited by the user back into .tex files.
"""
import struct
import zlib


def read_png_rgba(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    width = height = bitdepth = colortype = None
    idat = b""
    palette = None
    trns = None
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack_from(">IIBB", chunk, 0)
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"tRNS":
            trns = chunk
        pos += 8 + length + 4
    raw = zlib.decompress(idat)

    src_channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colortype]
    bpp = src_channels * (bitdepth // 8)
    stride = width * bpp

    out = bytearray(height * stride)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos+stride])
        pos += stride
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y*stride:(y+1)*stride] = line
        prev = line

    # normalize to RGBA8
    rgba = bytearray(width * height * 4)
    if colortype == 6:
        rgba[:] = out
    elif colortype == 2:
        for i in range(width * height):
            rgba[i*4:i*4+3] = out[i*3:i*3+3]
            rgba[i*4+3] = 255
    elif colortype == 0:
        for i in range(width * height):
            v = out[i]
            rgba[i*4:i*4+3] = bytes([v, v, v])
            rgba[i*4+3] = 255
    elif colortype == 3:
        for i in range(width * height):
            idx = out[i]
            r, g, b = palette[idx*3:idx*3+3]
            a = trns[idx] if trns and idx < len(trns) else 255
            rgba[i*4:i*4+4] = bytes([r, g, b, a])
    else:
        raise ValueError("unsupported PNG color type: %d" % colortype)

    return width, height, bytes(rgba)


def write_png_rgba(path, width, height, rgba):
    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data +
                struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0 (none)
        raw.extend(rgba[y*stride:(y+1)*stride])
    compressed = zlib.compress(bytes(raw), 6)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", compressed))
        f.write(chunk(b"IEND", b""))


def draw_line(rgba, width, height, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            i = (y0 * width + x0) * 4
            rgba[i:i+4] = color
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy

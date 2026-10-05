"""
Native reader/patcher for CRI .cpk archives (no CriPakTools needed).

Layout: "CPK " chunk with an @UTF table (CpkHeader) holding offsets of the
TOC/ITOC tables. @UTF tables are big-endian:
    "@UTF", u32 size, then at base=+8:
    u32 rows_off, u32 strings_off, u32 data_off, u32 name, u16 ncols,
    u16 row_len, u32 nrows; columns: u8 flags(storage|type), u32 name
    [+ inline value if storage is CONSTANT]. Offsets relative to base.
Storage: 0x10 zero, 0x30 constant, 0x50 per-row. Types: 0/1 u8/s8, 2/3
u16/s16, 4/5 u32/s32, 6/7 u64/s64, 8 f32, 0xA string, 0xB data.
"""
import os
import struct

_FMT = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f", 9: ">d", 0xA: ">I", 0xB: ">II"}


def utf_decrypt(buf):
    """Standard CRI @UTF table XOR obfuscation (self-inverse)."""
    out = bytearray(buf)
    m = 0x0000655F
    for i in range(len(out)):
        out[i] ^= m & 0xFF
        m = (m * 0x00004115) & 0xFFFFFFFF
    return bytes(out)


class UtfTable(object):
    def __init__(self, data, pos):
        """data: bytes of the whole file; pos: offset of '@UTF' (tables
        obfuscated with the CRI XOR are decrypted transparently, read-only)."""
        if data[pos:pos + 4] != b"@UTF":
            if utf_decrypt(data[pos:pos + 4]) != b"@UTF":
                raise ValueError("@UTF not found at 0x%x" % pos)
            size = struct.unpack_from(">I", utf_decrypt(data[pos:pos + 8]), 4)[0]
            plain = utf_decrypt(data[pos:pos + 8 + size])
            data = bytes(pos) + plain  # keeps absolute offsets valid
            self.encrypted = True
        else:
            self.encrypted = False
        self.pos = pos
        self.size = struct.unpack_from(">I", data, pos + 4)[0]
        base = pos + 8
        self.base = base
        rows_off, str_off, data_off, name_off, ncols, row_len, nrows = struct.unpack_from(">IIIIHHI", data, base)
        self.rows_off, self.str_off, self.data_off = rows_off, str_off, data_off
        self.row_len, self.nrows = row_len, nrows
        self.name = self._str(data, name_off)
        self.columns = []  # (name, storage, type, const_value, row_offset)
        p = base + 24
        roff = 0
        for _ in range(ncols):
            flags = data[p]
            nameo = struct.unpack_from(">I", data, p + 1)[0]
            p += 5
            storage, typ = flags & 0xF0, flags & 0x0F
            const = None
            size = struct.calcsize(_FMT[typ])
            if storage in (0x30, 0x70):
                const = self._value(data, p, typ)
                p += size
                col_off = None
            elif storage == 0x50:
                col_off = roff
                roff += size
            else:
                col_off = None
            self.columns.append((self._str(data, nameo), storage, typ, const, col_off))
        self.data = data

    def _str(self, data, off):
        s = self.base + self.str_off + off
        return data[s:data.index(b"\0", s)].decode("ascii", "replace")

    def _value(self, data, p, typ):
        v = struct.unpack_from(_FMT[typ], data, p)
        if typ == 0xA:
            return self._str(data, v[0])
        if typ == 0xB:
            s = self.base + self.data_off + v[0]
            return data[s:s + v[1]]
        return v[0]

    def get(self, row, name, default=None):
        for (n, storage, typ, const, off) in self.columns:
            if n != name:
                continue
            if storage == 0x50:
                return self._value(self.data, self.base + self.rows_off + row * self.row_len + off, typ)
            if storage in (0x30, 0x70):
                return const
            return 0
        return default

    def cell_offset(self, row, name):
        """Absolute file offset of a per-row cell (for in-place patching)."""
        for (n, storage, typ, const, off) in self.columns:
            if n == name and storage == 0x50:
                return self.base + self.rows_off + row * self.row_len + off, typ
        return None, None

    def has(self, name):
        return any(c[0] == name for c in self.columns)


class CpkEntry(object):
    def __init__(self, file_id, offset, size, extract_size, table=None, row=None):
        self.id = file_id              # ID (the "0085" style internal name)
        self.offset = offset           # absolute offset in the .cpk
        self.size = size               # stored size (compressed if CRILAYLA)
        self.extract_size = extract_size
        self.table = table             # sub-table (CpkItocL/H) holding the sizes
        self.row = row

    @property
    def name(self):
        return "%04d" % self.id


def _align(n, a):
    return (n + a - 1) // a * a if a > 1 else n


class Cpk(object):
    """ITOC-based CPK (what RecoLove uses: files addressed by ID, offsets
    implicit = sequential from ContentOffset, each aligned to Align)."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = data = f.read()
        if data[:4] != b"CPK ":
            raise ValueError("not a CPK file: %s" % path)
        self.header = h = UtfTable(data, 0x10)
        if h.get(0, "TocOffset", 0):
            raise NotImplementedError("name-based (TOC) CPKs are not supported - only ITOC")
        self.align = h.get(0, "Align", 1) or 1
        self.content_offset = h.get(0, "ContentOffset")
        itoc_off = h.get(0, "ItocOffset")
        if not itoc_off:
            raise NotImplementedError("CPK without ITOC")
        it = UtfTable(data, itoc_off + 0x10)
        rows = []
        for col in ("DataL", "DataH"):
            for (n, storage, typ, const, off) in it.columns:
                if n != col:
                    continue
                ptr, size = struct.unpack_from(">II", data, it.base + it.rows_off + off)
                if not size:
                    continue
                sub = UtfTable(data, it.base + it.data_off + ptr)
                for r in range(sub.nrows):
                    rows.append((sub.get(r, "ID"), sub.get(r, "FileSize"), sub.get(r, "ExtractSize"), sub, r))
        rows.sort(key=lambda x: x[0])
        self.entries = []
        cur = self.content_offset
        for (fid, fs, es, sub, r) in rows:
            self.entries.append(CpkEntry(fid, cur, fs, es, sub, r))
            cur = _align(cur + fs, self.align)

    def find(self, name_or_id):
        fid = int(os.path.basename(str(name_or_id)).split("_")[0].split(".")[0])
        for e in self.entries:
            if e.id == fid:
                return e
        raise KeyError(name_or_id)

    def read(self, entry):
        raw = self.data[entry.offset:entry.offset + entry.size]
        if raw[:8] == b"CRILAYLA":
            return crilayla_decompress(raw)
        return raw

    def replace(self, files, out_path):
        """files: {id_or_name: new_bytes}. Writes out_path with those files
        replaced, stored uncompressed.

        Mirrors CriPakTools - whose output is proven to load on the Vita -
        on purpose: only the FileSize/ExtractSize cells of the replaced files
        change, the header totals (ContentSize, EnabledDataSize,
        EnabledPackedSize) are left as they were, and when something was
        replaced the archive end is padded to the alignment. With no changes
        the output is byte-identical to the input."""
        new = {self.find(k).id: v for k, v in files.items()}
        if not new:
            with open(out_path, "wb") as f:
                f.write(self.data)
            return
        out = bytearray(self.data[:self.content_offset])
        for e in self.entries:
            blob = new.get(e.id)
            if blob is None:
                blob = self.data[e.offset:e.offset + e.size]
            else:
                for col in ("FileSize", "ExtractSize"):
                    off, typ = e.table.cell_offset(e.row, col)
                    if typ in (2, 3) and len(blob) > 0xFFFF:
                        raise ValueError("file %s too big for its 16-bit ITOC row" % e.name)
                    struct.pack_into(_FMT[typ], out, off, len(blob))
            out += blob
            out += b"\0" * (_align(len(out), self.align) - len(out))
        with open(out_path, "wb") as f:
            f.write(out)


def crilayla_decompress(src):
    """CRILAYLA decompression (standard CRI algorithm)."""
    uncompressed_size, header_offset = struct.unpack_from("<II", src, 8)
    result = bytearray(uncompressed_size + 0x100)
    result[0:0x100] = src[header_offset + 0x10:header_offset + 0x10 + 0x100]
    input_end = len(src) - 0x100 - 1
    input_offset = input_end
    output_end = 0x100 + uncompressed_size - 1
    bit_pool = 0
    bits_left = 0
    bytes_output = 0
    vle_lens = (2, 3, 5, 8)

    def get_bits(n):
        nonlocal bit_pool, bits_left, input_offset
        out_bits = 0
        produced = 0
        while produced < n:
            if bits_left == 0:
                bit_pool = src[input_offset]
                bits_left = 8
                input_offset -= 1
            take = min(bits_left, n - produced)
            out_bits <<= take
            out_bits |= (bit_pool >> (bits_left - take)) & ((1 << take) - 1)
            bits_left -= take
            produced += take
        return out_bits

    while bytes_output < uncompressed_size:
        if get_bits(1):
            backref_offset = output_end - bytes_output + get_bits(13) + 3
            backref_length = 3
            vle_level = 0
            while vle_level < len(vle_lens):
                this_level = get_bits(vle_lens[vle_level])
                backref_length += this_level
                if this_level != (1 << vle_lens[vle_level]) - 1:
                    break
                vle_level += 1
            if vle_level == len(vle_lens):
                while True:
                    this_level = get_bits(8)
                    backref_length += this_level
                    if this_level != 255:
                        break
            for _ in range(backref_length):
                result[output_end - bytes_output] = result[backref_offset]
                backref_offset -= 1
                bytes_output += 1
        else:
            result[output_end - bytes_output] = get_bits(8)
            bytes_output += 1
    return bytes(result[:0x100 + uncompressed_size])


def read_als(path):
    """.als = one real file name per line, in TOC order."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [l.strip() for l in f if l.strip()]

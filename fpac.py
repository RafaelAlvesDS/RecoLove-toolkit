"""
FPAC: the generic container RecoLove uses for .fed (models) and .tex
(textures). This module reads/writes the WHOLE tree generically with a
byte-identical round trip (validated on all 356 .fed and 750 .tex files).

Layout of every FPAC node:

    0x00 char   tag[8]        "FPAC\0\0\0\0", "FPACMESH", "FPACTEX\0", ...
    0x08 uint16 blockCount
    0x0A uint8  offsetType    2 = uint32 table ; 6 = uint16 table
    0x0B uint8  unk[5]        preserved as-is
    0x10        { off, size } x blockCount   (off relative to the node)
    ...         children, each aligned to 16 bytes

Rules observed (and reproduced when writing):
  * a child with size==0 has off==0 in the table (empty slot);
  * the `size` of a leaf child is the EXACT data size (not rounded), but
    the next child starts at the next multiple of 16;
  * the `size` of a container child includes its trailing padding.

A block is treated as a container if it starts with "FPAC" (no data block
in the game starts like that - floats, indices, GXT...).
"""
import struct


def _align16(n):
    return (n + 15) & ~15


class Node(object):
    """FPAC node. `children` is a list where each item is:
         - Node      (sub-container)
         - bytes     (leaf block; b"" = empty slot)"""

    def __init__(self, tag=b"FPAC\x00\x00\x00\x00", offset_type=2, unk=b"\x01\x00\x00\x00\x00", children=None):
        if isinstance(tag, str):
            tag = tag.encode("ascii")
        self.tag = tag.ljust(8, b"\x00")[:8]
        self.offset_type = offset_type
        self.unk = unk
        self.children = children if children is not None else []

    @property
    def name(self):
        return self.tag.rstrip(b"\x00").decode("ascii", "replace")

    def find(self, name):
        """First child container with that name (e.g. 'FPACGEOM')."""
        for c in self.children:
            if isinstance(c, Node) and c.name == name:
                return c
        return None

    def __repr__(self):
        return "<Node %s children=%d>" % (self.name, len(self.children))


def is_container(data, off, size):
    return size >= 16 and data[off:off + 4] == b"FPAC"


def parse(data, base=0, size=None):
    """Recursively parses the FPAC node at data[base:base+size]."""
    if size is None:
        size = len(data) - base
    if not is_container(data, base, size):
        raise ValueError("not an FPAC container (offset %d)" % base)
    node = Node(tag=bytes(data[base:base + 8]),
                offset_type=data[base + 10],
                unk=bytes(data[base + 11:base + 16]))
    count = struct.unpack_from("<H", data, base + 8)[0]
    for i in range(count):
        if node.offset_type == 2:
            off, sz = struct.unpack_from("<II", data, base + 16 + i * 8)
        elif node.offset_type == 6:
            off, sz = struct.unpack_from("<HH", data, base + 16 + i * 4)
        else:
            raise ValueError("unknown offsetType: %d" % node.offset_type)
        if sz == 0:
            node.children.append(b"")
        elif is_container(data, base + off, sz):
            node.children.append(parse(data, base + off, sz))
        else:
            node.children.append(bytes(data[base + off:base + off + sz]))
    return node


def serialize(node):
    """Re-serializes the tree. Identical to the original if nothing changed."""
    blobs = []
    for c in node.children:
        blobs.append(serialize(c) if isinstance(c, Node) else bytes(c))

    entry = 8 if node.offset_type == 2 else 4
    cursor = _align16(16 + entry * len(blobs))
    table = bytearray()
    body = bytearray()
    for b in blobs:
        if not b:
            off = 0
        else:
            off = cursor
            body += b
            pad = _align16(len(b)) - len(b)
            body += b"\x00" * pad
            cursor += len(b) + pad
        if node.offset_type == 2:
            table += struct.pack("<II", off, len(b))
        else:
            if off > 0xFFFF or len(b) > 0xFFFF:
                raise ValueError("block too big for a uint16 table in %s" % node.name)
            table += struct.pack("<HH", off, len(b))

    out = bytearray()
    out += node.tag
    out += struct.pack("<HB", len(blobs), node.offset_type)
    out += node.unk
    out += table
    out += b"\x00" * (_align16(len(out)) - len(out))
    out += body
    return bytes(out)


def load(path):
    with open(path, "rb") as f:
        return parse(f.read())


def dump(node, indent=0, out=None):
    """Readable tree (diagnostics)."""
    lines = out if out is not None else []
    lines.append("%s%s (%d children, offsetType=%d)" % ("  " * indent, node.name, len(node.children), node.offset_type))
    for i, c in enumerate(node.children):
        if isinstance(c, Node):
            dump(c, indent + 1, lines)
        elif c:
            lines.append("%s[%d] %d bytes" % ("  " * (indent + 1), i, len(c)))
    if out is None:
        return "\n".join(lines)

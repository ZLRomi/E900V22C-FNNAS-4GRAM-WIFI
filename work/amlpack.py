#!/usr/bin/env python3
"""
Amlogic 烧录包 (aml_upgrade_package v2) 打包器
配合 amlpkg.py 解析器使用，格式已实测校验通过。
"""
import struct
import zlib
import os
import sys

HDR_SIZE = 64
DESC_SIZE = 576          # 0x228 数据 + 24 补齐
MAGIC = 0x27B51956

TYPE_NORMAL = 0x000
TYPE_SPARSE = 0x0fe


def build(items, version=2, align=4):
    """
    items: list of dict:
        main, sub, type, verify, is_backup, backup_id, data(bytes) 或 path(str)
    """
    n = len(items)

    # 数据区起始（头 + 描述符表，按 align 对齐）
    data_start = HDR_SIZE + DESC_SIZE * n
    data_start = (data_start + align - 1) // align * align

    descs = b''
    blobs = []
    pos = data_start

    for i, it in enumerate(items):
        if 'data' in it:
            data = it['data']
        else:
            with open(it['path'], 'rb') as f:
                data = f.read()

        main = it['main'].encode()[:255]
        sub = it['sub'].encode()[:255]
        size = len(data)

        d = struct.pack('<IIQQQ',
                        i,
                        it.get('type', TYPE_NORMAL),
                        0,
                        pos,
                        size)
        d += main.ljust(256, b'\x00')
        d += sub.ljust(256, b'\x00')
        d += struct.pack('<IHH',
                         it.get('verify', 0),
                         it.get('is_backup', 0),
                         it.get('backup_id', 0))
        d += b'\x00' * 24
        assert len(d) == DESC_SIZE, f"描述符长度错误 {len(d)}"
        descs += d
        blobs.append(data)

        pos += size
        pos = (pos + align - 1) // align * align

    total = pos

    # 头（crc 先填 0）
    head = struct.pack('<III', 0, version, MAGIC)
    head += struct.pack('<Q', total)
    head += struct.pack('<I', align)
    head += struct.pack('<I', n)
    head += b'\x00' * 36
    assert len(head) == HDR_SIZE

    out = bytearray(head + descs)
    out += b'\x00' * (data_start - len(out))
    for b in blobs:
        out += b
        pad = (align - len(b) % align) % align
        out += b'\x00' * pad
    assert len(out) == total, f"长度不符 {len(out)} != {total}"

    # CRC = crc32(data[4:]) ^ 0xffffffff
    crc = zlib.crc32(bytes(out)[4:]) ^ 0xffffffff
    out[0:4] = struct.pack('<I', crc)
    return bytes(out)


def verify(path):
    """用解析器的规则自检生成的包"""
    raw = open(path, 'rb').read()
    crc_hdr, version, magic = struct.unpack('<III', raw[0:12])
    size = struct.unpack('<Q', raw[0x0C:0x14])[0]
    align = struct.unpack('<I', raw[0x14:0x18])[0]
    count = struct.unpack('<I', raw[0x18:0x1C])[0]
    crc_calc = zlib.crc32(raw[4:]) ^ 0xffffffff

    print(f"  文件      : {os.path.basename(path)}  ({len(raw):,} 字节)")
    print(f"  version   : {version}")
    print(f"  magic     : 0x{magic:08X}  {'OK' if magic == MAGIC else 'BAD'}")
    print(f"  size      : {size:,}  {'OK' if size == len(raw) else f'MISMATCH(实际 {len(raw)})'}")
    print(f"  align     : {align}")
    print(f"  items     : {count}")
    print(f"  CRC       : 0x{crc_hdr:08X} / 计算 0x{crc_calc:08X}  "
          f"{'OK' if crc_hdr == crc_calc else 'FAIL'}")

    ok = True
    for i in range(count):
        base = HDR_SIZE + i * DESC_SIZE
        d = raw[base:base + DESC_SIZE]
        _id, ftype, unk, off, sz = struct.unpack('<IIQQQ', d[0:0x20])
        main = d[0x20:0x120].split(b'\x00')[0].decode('utf-8', 'replace')
        sub = d[0x120:0x220].split(b'\x00')[0].decode('utf-8', 'replace')
        v, ib, bid = struct.unpack('<IHH', d[0x220:0x228])
        inside = off + sz <= len(raw)
        if not inside:
            ok = False
        print(f"    [{i:>2}] {main:<12} {sub:<14} off={off:>10,} size={sz:>12,} "
              f"verify={v} bkp={ib}"
              f"{'' if inside else '  <<< 越界!'}")
    print(f"  结构校验  : {'OK' if ok else 'FAIL'}")
    return ok and crc_hdr == crc_calc and magic == MAGIC and size == len(raw)

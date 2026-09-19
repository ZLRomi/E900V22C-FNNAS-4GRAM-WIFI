#!/usr/bin/env python3
"""
Amlogic 烧录包 (aml_upgrade_package) 解析 / 重打包工具
格式参考: superna9999/pyamlboot AML-IMAGE-FORMAT.md
"""
import struct
import sys
import os
from zlib import crc32

HDR_SIZE = 0x40
DESC_SIZE = 0x240          # v2 描述符实际占用（0x228 + 24 对齐）

TYPE_NORMAL = 0x000
TYPE_SPARSE = 0x0fe
TYPE_UBI    = 0x1fe
TYPE_UBIFS  = 0x2fe

TYPE_NAMES = {
    0x000: 'normal', 0x0fe: 'sparse', 0x1fe: 'ubi', 0x2fe: 'ubifs',
}


def _cstr(b):
    return b.split(b'\x00')[0].decode('utf-8', 'replace')


def read_header(f):
    f.seek(0)
    raw = f.read(HDR_SIZE)
    crc, version, magic = struct.unpack('<III', raw[0:12])
    size = struct.unpack('<Q', raw[0x0C:0x14])[0]
    align = struct.unpack('<I', raw[0x14:0x18])[0]
    count = struct.unpack('<I', raw[0x18:0x1C])[0]
    return dict(crc=crc, version=version, magic=magic, size=size,
                align=align, count=count, raw=raw)


def read_descriptor(f, idx):
    f.seek(HDR_SIZE + idx * DESC_SIZE)
    raw = f.read(DESC_SIZE)
    if len(raw) < DESC_SIZE:
        return None
    d = {}
    d['id'], d['filetype'] = struct.unpack('<II', raw[0:8])
    d['unk'] = struct.unpack('<Q', raw[0x08:0x10])[0]
    d['offset'] = struct.unpack('<Q', raw[0x10:0x18])[0]
    d['size'] = struct.unpack('<Q', raw[0x18:0x20])[0]
    d['main'] = _cstr(raw[0x20:0x120])
    d['sub'] = _cstr(raw[0x120:0x220])
    d['verify'], d['is_backup'], d['backup_id'] = struct.unpack('<IHH', raw[0x220:0x228])
    d['raw'] = raw
    return d


def check_crc(path):
    with open(path, 'rb') as f:
        data = f.read()
    want = struct.unpack('<I', data[0:4])[0]
    got = crc32(data[4:]) ^ 0xffffffff
    return want, got & 0xffffffff


def info(path):
    with open(path, 'rb') as f:
        h = read_header(f)
        print("=" * 100)
        print("文件:", os.path.basename(path), f"  ({os.path.getsize(path):,} 字节)")
        print("=" * 100)
        print(f"  crc      = 0x{h['crc']:08X}")
        print(f"  version  = {h['version']}")
        print(f"  magic    = 0x{h['magic']:08X}  {'OK' if h['magic'] == 0x27B51956 else 'BAD'}")
        print(f"  size     = {h['size']:,}")
        print(f"  align    = {h['align']}")
        print(f"  items    = {h['count']}")

        want, got = check_crc(path)
        print(f"  CRC 校验 = {'OK' if want == got else f'FAIL (header=0x{want:08X} calc=0x{got:08X})'}")

        print()
        print(f"  {'#':>3} {'id':>3} {'type':<8} {'main':<16} {'sub':<20} {'offset':>12} {'size':>14} {'verify':>7}  {'bkp':>4}")
        print("  " + "-" * 100)

        descs = []
        for i in range(h['count']):
            d = read_descriptor(f, i)
            if not d:
                print(f"  !! 描述符 {i} 读取失败")
                break
            descs.append(d)
            tn = TYPE_NAMES.get(d['filetype'], hex(d['filetype']))
            print(f"  {i:>3} {d['id']:>3} {tn:<8} {d['main']:<16} {d['sub']:<20} "
                  f"{d['offset']:>12,} {d['size']:>14,} {d['verify']:>7}  {d['is_backup']:>4}")
        return h, descs


def extract(path, outdir, only=None):
    h, descs = info(path)
    os.makedirs(outdir, exist_ok=True)
    print("\n--- 提取 ---")
    with open(path, 'rb') as f:
        for i, d in enumerate(descs):
            if d['size'] == 0:
                continue
            label = f"{i:02d}_{d['main']}_{d['sub']}".replace('/', '_').strip('_')
            if only and only not in label:
                continue
            fn = os.path.join(outdir, label + '.bin')
            f.seek(d['offset'])
            remain = d['size']
            with open(fn, 'wb') as o:
                while remain > 0:
                    chunk = f.read(min(1 << 20, remain))
                    if not chunk:
                        break
                    o.write(chunk)
                    remain -= len(chunk)
            print(f"  [{i:>2}] {label:<44} {d['size']:>13,} 字节 -> {os.path.basename(fn)}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'info'
    if cmd == 'info':
        for p in sys.argv[2:]:
            info(p)
    elif cmd == 'extract':
        extract(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        print(__doc__)

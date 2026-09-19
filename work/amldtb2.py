#!/usr/bin/env python3
"""
解析 Amlogic _aml_dtb 容器
结构: header magic(4)+version(4)+count(4) = 12B
      条目: name(48) + offset(4) + size(4) = 56B
"""
import struct
import gzip
import sys
import os
import re
import subprocess

FDT_MAGIC = b'\xd0\x0d\xfe\xed'


def load(path):
    raw = open(path, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return raw


def parse(path, outdir):
    d = load(path)
    if d[:4] != b'AML_':
        print("  非 AML_ 容器, magic =", d[:4])
        return
    ver, cnt = struct.unpack('<II', d[4:12])
    print(f"  AML_ 容器  version={ver}  count={cnt}  总大小={len(d)}")
    os.makedirs(outdir, exist_ok=True)

    for i in range(cnt):
        base = 12 + i * 56
        if base + 56 > len(d):
            break
        name = d[base:base+48].split(b'\x00')[0].decode('utf-8', 'replace').strip()
        off = struct.unpack('<I', d[base+48:base+52])[0]
        size = struct.unpack('<I', d[base+52:base+56])[0]
        print(f"\n  [{i}] name='{name}'  offset=0x{off:X}  size={size}")
        blob = d[off:off+size]
        if blob[:4] != FDT_MAGIC:
            print(f"       magic={blob[:4].hex(' ')}  (非标准 FDT)")
            continue
        fn = os.path.join(outdir, f'dtb_{i}.bin')
        open(fn, 'wb').write(blob)
        print(f"       -> {fn}")
        show(fn)


def show(fn):
    r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', '-o', '/tmp/_y.dts', fn],
                       capture_output=True)
    if r.returncode != 0:
        print("       反编译失败")
        return
    txt = open('/tmp/_y.dts', encoding='utf-8', errors='replace').read()
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith('model = '):
            print(f"       model      : {s.split('=',1)[1].strip().strip(';')}")
            break
    m = re.search(r'memory@[0-9a-fA-F]*\s*\{[^}]*?reg = <([^>]*)>', txt, re.S)
    if m:
        cells = m.group(1).split()
        print(f"       memory reg : {' '.join(cells)}")
        if len(cells) == 4:
            try:
                total = (int(cells[2], 16) << 32) | int(cells[3], 16)
                print(f"       => 内存     : {total/1024/1024/1024:.2f} GB")
            except Exception:
                pass
    # 关键外设
    for key in ['mmc@ffe07000', 'ethernet@ff3f0000']:
        if key in txt:
            print(f"       含 {key}")


if __name__ == '__main__':
    parse(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else '/tmp/aml_dtb2')

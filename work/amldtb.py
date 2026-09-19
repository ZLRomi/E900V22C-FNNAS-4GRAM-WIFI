#!/usr/bin/env python3
"""解析 Amlogic _aml_dtb 容器 (AML_ magic, 内含多个 dtb)"""
import struct
import sys
import gzip
import os

path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/aml_ext/02_PARTITION__aml_dtb.bin'
raw = open(path, 'rb').read()

if raw[:2] == b'\x1f\x8b':
    d = gzip.decompress(raw)
    print(f"gzip 解压: {len(raw)} -> {len(d)} 字节")
else:
    d = raw

print(f"magic = {d[0:4]!r}  ({d[0:4].hex(' ')})")
ver, cnt = struct.unpack('<II', d[4:12])
print(f"version = {ver}   count = {cnt}")

base = 12
for i in range(min(cnt, 8)):
    off, size = struct.unpack('<II', d[base + i*8: base + i*8 + 8])
    magic = d[off:off+4]
    tag = ''
    if magic == b'\xd0\x0d\xfe\xed':
        tag = ' <- FDT (d00dfeed)'
    print(f"  dtb[{i}]  offset=0x{off:<8X} size={size:<8} magic={magic.hex(' ')}{tag}")
    if magic == b'\xd0\x0d\xfe\xed':
        out = f'/tmp/aml_ext/aml_dtb_{i}.dtb'
        open(out, 'wb').write(d[off:off+size])
        print(f"           -> 已导出 {out}")

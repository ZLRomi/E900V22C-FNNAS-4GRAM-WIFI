#!/usr/bin/env python3
"""
从安卓线刷包的 _aml_dtb 分区里找出所有内嵌 dtb，
打印 model / compatible / memory 节点 —— 用来确认这台盒子正确的内存配置。
"""
import gzip
import struct
import os
import subprocess
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/aml_ext2/02_PARTITION__aml_dtb.bin'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/tmp/and_dtbs'

raw = open(SRC, 'rb').read()
print(f"输入: {SRC}  ({len(raw):,} 字节)")
if raw[:2] == b'\x1f\x8b':
    raw = gzip.decompress(raw)
    print(f"  -> gzip 解压后 {len(raw):,} 字节")

os.makedirs(OUT, exist_ok=True)

# 找出所有 FDT magic (d00dfeed)
MAGIC = b'\xd0\x0d\xfe\xed'
offs = []
i = 0
while True:
    j = raw.find(MAGIC, i)
    if j < 0:
        break
    offs.append(j)
    i = j + 4
print(f"找到 {len(offs)} 个 FDT 魔数位置: {[hex(o) for o in offs]}")

for k, off in enumerate(offs):
    totalsize = struct.unpack('>I', raw[off+4:off+8])[0]
    if totalsize <= 0 or off + totalsize > len(raw):
        print(f"  [{k}] off=0x{off:X} totalsize={totalsize} 越界，跳过")
        continue
    blob = raw[off:off+totalsize]
    p = os.path.join(OUT, f'dtb_{k:02d}.dtb')
    open(p, 'wb').write(blob)
    print(f"  [{k}] off=0x{off:X}  size={totalsize:,}  -> {p}")

print()
print("=" * 78)
print("逐个反编译查看 model / memory")
print("=" * 78)
for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {f}: dtc 失败 {r.stderr.strip()[:80]}")
        continue
    dts = r.stdout
    model = ''
    compat = ''
    for line in dts.splitlines():
        if 'model = ' in line and not model:
            model = line.strip()
        if 'compatible = ' in line and not compat:
            compat = line.strip()
    # memory 节点
    mem = []
    lines = dts.splitlines()
    for i, line in enumerate(lines):
        if 'memory@' in line:
            for j in range(i, min(i + 5, len(lines))):
                mem.append(lines[j].strip())
            break
    print(f"\n--- {f} ---")
    print(f"  {model}")
    print(f"  {compat}")
    for m in mem:
        print(f"  {m}")

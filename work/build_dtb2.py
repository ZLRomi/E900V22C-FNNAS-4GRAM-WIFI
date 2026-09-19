#!/usr/bin/env python3
"""
基于 FnNAS 原版 dtb 生成多个内存变体，用于排查重启循环。
依据：安卓 4G 包内的 dtb 声明 linux,usable-memory = <0x00 0xf5700000>
      （约 3.83GB，顶部约 170MB 被 BL31/TEE 保留）
"""
import re
import os
import shutil
import subprocess
import sys

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUTDIR = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
WORK = '/tmp/dtbwork'

os.makedirs(OUTDIR, exist_ok=True)
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK],
                   capture_output=True, text=True)
if r.returncode != 0:
    print("dtc 反编译失败:", r.stderr[:400])
    sys.exit(1)
dts = r.stdout

m = re.search(r'memory@0\s*\{.*?\n\t\};', dts, re.S)
if not m:
    print("找不到 memory@0 节点")
    sys.exit(1)
mem_node = m.group(0)
print("=" * 78)
print("FnNAS 原版 dtb 的 memory 节点")
print("=" * 78)
print(mem_node)
reg = re.search(r'reg = <([^>]*)>;', mem_node)
print(f"\n原 reg = <{reg.group(1)}>")

VARIANTS = [
    ('dtba-stock-2g.dtb',        '<0x00 0x00 0x00 0x80000000>', '原版 2GB (最保险，先测这个)'),
    ('dtbb-4g-usable.dtb',       '<0x00 0x00 0xf5700000>',      '4GB 可用3.83GB (对齐安卓4G dtb)'),
    ('dtbc-4g-full.dtb',         '<0x00 0x00 0x01 0x00>',       '4GB 完整 (之前用的，疑似崩溃源)'),
    ('dtbd-4g-reserve.dtb',      '<0x00 0x00 0x01 0x00>',       '4GB 完整 + 顶部保留内存节点'),
]

print()
print("=" * 78)
print("生成变体")
print("=" * 78)

for fname, newreg, desc in VARIANTS:
    newnode = re.sub(r'reg = <[^>]*>;', f'reg = {newreg};', mem_node)
    newdts = dts.replace(mem_node, newnode)

    if fname == 'dtbd-4g-reserve.dtb':
        # 在 memory 节点后插入一个保留区，覆盖 0x100000000-0x10A90000 之外的顶部
        # Amlogic 常见做法：reserved-memory 里声明 bl31/secure 等
        reserve = ('\treserved-memory {\n'
                   '\t\t#address-cells = <0x02>;\n'
                   '\t\t#size-cells = <0x02>;\n'
                   '\t\tranges;\n'
                   '\n'
                   '\t\tlinux,cma {\n'
                   '\t\t\tcompatible = "shared-dma-pool";\n'
                   '\t\t\treusable;\n'
                   '\t\t\tsize = <0x00 0x10000000>;\n'
                   '\t\t\talignment = <0x00 0x400000>;\n'
                   '\t\t\tlinux,cma-default;\n'
                   '\t\t};\n'
                   '\n'
                   '\t\tsecmon_reserved: secmon@10000000 {\n'
                   '\t\t\tno-map;\n'
                   '\t\t\treg = <0x00 0x10000000 0x00 0x2000000>;\n'
                   '\t\t};\n'
                   '\t};\n')
        if 'reserved-memory {' not in newdts:
            newdts = newdts.replace('\tmemory@0 {', reserve + '\n\tmemory@0 {')

    dtsf = os.path.join(WORK, fname.replace('.dtb', '.dts'))
    open(dtsf, 'w').write(newdts)
    out = os.path.join(OUTDIR, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', out, dtsf],
                        capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(out)
    size = os.path.getsize(out) if ok else 0
    print(f"  {'OK ' if ok else 'ERR'} {fname:<26} {size:>7,} B   {desc}")
    if not ok:
        print("       ", rr.stderr.strip().splitlines()[0] if rr.stderr else '')

print()
print("=" * 78)
print("逐个校验生成的 dtb")
print("=" * 78)
for f in sorted(os.listdir(OUTDIR)):
    if not f.endswith('.dtb'):
        continue
    p = os.path.join(OUTDIR, f)
    r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {f}: dtc 解析失败")
        continue
    mm = re.search(r'memory@0\s*\{.*?reg = <([^>]*)>;', r.stdout, re.S)
    magic = open(p, 'rb').read(4).hex(' ')
    print(f"  {f:<26} {os.path.getsize(p):>7,} B  magic={magic}  memory reg = <{mm.group(1) if mm else '?'}>")

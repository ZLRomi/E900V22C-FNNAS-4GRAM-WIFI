#!/usr/bin/env python3
"""
生成组合 dtb 变体：
  - 内存：2G(原版 0x80000000) / 4G(0xf5700000，对齐安卓4G包)
  - eMMC 速度：HS200(原版100MHz) / DDR52(52MHz) / HS50(50MHz) / 25MHz
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv3'

shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout

# 找到 eMMC 节点
m = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
assert m, "找不到 sd_emmc_c 节点"
EMMC = m.group(1)
print("=" * 78)
print("原始 eMMC 节点关键配置")
print("=" * 78)
for line in EMMC.splitlines():
    if re.search(r'bus-width|max-frequency|mmc-ddr|mmc-hs|cap-mmc|non-removable|disable-wp', line):
        print("   " + line.strip())

# 内存节点
mm = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
assert mm, "找不到 memory@0"
MEM = mm.group(1)
print()
print("原始 memory@0:")
print(MEM)


def make_emmc(speed):
    """返回修改后的 eMMC 节点"""
    n = EMMC
    # 去掉原有速度声明
    n = re.sub(r'\n\t\t\tmmc-hs200-1_8v;', '', n)
    n = re.sub(r'\n\t\t\tmmc-ddr-1_8v;', '', n)
    n = re.sub(r'\n\t\t\tmmc-hs400-1_8v;', '', n)
    # 去掉原 max-frequency
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)

    extra = ''
    if speed == 'hs200':
        extra = '\n\t\t\tmax-frequency = <0x5f5e100>;\n\t\t\tmmc-ddr-1_8v;\n\t\t\tmmc-hs200-1_8v;'
        freq = '100MHz HS200'
    elif speed == 'ddr52':
        extra = '\n\t\t\tmax-frequency = <0x3197500>;\n\t\t\tmmc-ddr-1_8v;'
        freq = '52MHz DDR52'
    elif speed == 'hs50':
        extra = '\n\t\t\tmax-frequency = <0x2faf080>;'
        freq = '50MHz HS'
    elif speed == 'hs25':
        extra = '\n\t\t\tmax-frequency = <0x17d7840>;'
        freq = '25MHz HS'
    else:
        raise ValueError(speed)

    # 插到 cap-mmc-highspeed 之后
    n = n.replace('\t\t\tcap-mmc-highspeed;', '\t\t\tcap-mmc-highspeed;' + extra)
    return n, freq


def make_mem(kind):
    if kind == '2g':
        return re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0x00 0x80000000>;', MEM), '2GB'
    elif kind == '4g':
        return re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0xf5700000>;', MEM), '4GB(可用3.83G)'
    raise ValueError(kind)


COMBOS = [
    ('fat-2g-emmc-hs200.dtb', '2g', 'hs200'),
    ('fat-2g-emmc-ddr52.dtb', '2g', 'ddr52'),
    ('fat-2g-emmc-hs50.dtb',  '2g', 'hs50'),
    ('fat-2g-emmc-hs25.dtb',  '2g', 'hs25'),
    ('fat-4g-emmc-hs200.dtb', '4g', 'hs200'),
    ('fat-4g-emmc-ddr52.dtb', '4g', 'ddr52'),
    ('fat-4g-emmc-hs50.dtb',  '4g', 'hs50'),
]

print()
print("=" * 78)
print("生成变体")
print("=" * 78)
results = []
for fname, mem, spd in COMBOS:
    new_dts = dts
    newmem, memdesc = make_mem(mem)
    newe, freq = make_emmc(spd)
    new_dts = new_dts.replace(MEM, newmem)
    new_dts = new_dts.replace(EMMC, newe)

    dtsf = os.path.join(W, fname.replace('.dtb', '.dts'))
    open(dtsf, 'w').write(new_dts)
    outp = os.path.join(OUT, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, dtsf],
                        capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(outp)
    sz = os.path.getsize(outp) if ok else 0
    print(f"  {'OK ' if ok else 'ERR'} {fname:<26} {memdesc:<16} {freq:<14} {sz:>7,} B")
    if not ok:
        print("      ", (rr.stderr or '').strip().splitlines()[:2])
    results.append((fname, outp, ok))

print()
print("=" * 78)
print("校验：反编译确认 memory 与 eMMC 速度")
print("=" * 78)
for fname, p, ok in results:
    if not ok:
        continue
    rr = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p], capture_output=True, text=True)
    t = rr.stdout
    mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>', t)
    em = re.search(r'sd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};', t, re.S)
    emtxt = em.group(0) if em else ''
    freq = re.search(r'max-frequency = <([^>]*)>', emtxt)
    ddr = 'mmc-ddr-1_8v' in emtxt
    hs2 = 'mmc-hs200-1_8v' in emtxt
    magic = open(p, 'rb').read(4).hex(' ')
    print(f"  {fname:<26} magic={magic} mem=<{mem.group(1) if mem else '?'}> "
          f"freq=<{freq.group(1) if freq else '-'}> ddr={ddr} hs200={hs2}")

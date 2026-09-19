#!/usr/bin/env python3
"""
修正版：memory reg 必须是 4 个 cell (addr_hi addr_lo size_hi size_lo)
  #address-cells = 2, #size-cells = 2
  2G 原版 : <0x00 0x00 0x00 0x80000000>   addr=0 size=0x0_80000000 (2GB)
  4G 可用 : <0x00 0x00 0x00 0xf5700000>   addr=0 size=0x0_f5700000 (3.83GB，对齐安卓4G包)
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv4'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout

m = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
assert m, "找不到 sd_emmc_c"
EMMC = m.group(1)

mm = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
assert mm, "找不到 memory@0"
MEM = mm.group(1)
print("原始 memory@0:")
print(MEM)

print()
print("原始 eMMC 关键行:")
for ln in EMMC.splitlines():
    if re.search(r'bus-width|max-frequency|mmc-ddr|mmc-hs|cap-mmc|non-removable|disable-wp|status = "ok', ln):
        print("  " + ln.strip())


def emmc_ddr52():
    n = EMMC
    n = re.sub(r'\n\t\t\tmmc-hs200-1_8v;', '', n)
    n = re.sub(r'\n\t\t\tmmc-hs400-1_8v;', '', n)
    n = re.sub(r'\n\t\t\tmmc-ddr-1_8v;', '', n)
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)
    n = n.replace('\t\t\tcap-mmc-highspeed;',
                  '\t\t\tcap-mmc-highspeed;\n\t\t\tmax-frequency = <0x3197500>;\n\t\t\tmmc-ddr-1_8v;')
    return n


# 4 个 cell 的写法：addr_hi addr_lo size_hi size_lo
MEM_VARIANTS = [
    ('2g',       'reg = <0x00 0x00 0x00 0x80000000>;', '2GB (原版对照)'),
    ('4g-383',   'reg = <0x00 0x00 0x00 0xf5700000>;', '3.83GB (对齐安卓4G包, 推荐)'),
    ('4g-375',   'reg = <0x00 0x00 0x00 0xf0000000>;', '3.75GB'),
    ('4g-350',   'reg = <0x00 0x00 0x00 0xe0000000>;', '3.50GB'),
    ('4g-300',   'reg = <0x00 0x00 0x00 0xc0000000>;', '3.00GB'),
]

EMMC52 = emmc_ddr52()

print()
print("=" * 82)
print("生成变体 (全部修正为 4-cell reg)")
print("=" * 82)

results = []
for tag, memnew, desc in MEM_VARIANTS:
    fname = f'v4-{tag}-emmc-ddr52.dtb'
    newmem = re.sub(r'reg = <[^>]*>;', memnew, MEM)
    newdts = dts.replace(MEM, newmem).replace(EMMC, EMMC52)
    dtsf = os.path.join(W, fname + '.dts')
    open(dtsf, 'w').write(newdts)
    outp = os.path.join(OUT, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, dtsf],
                        capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(outp)
    sz = os.path.getsize(outp) if ok else 0
    print(f"  {'OK ' if ok else 'ERR'} {fname:<28} {desc:<26} {sz:>7,} B")
    results.append((fname, outp, ok))

print()
print("=" * 82)
print("校验（重点确认 reg 是 4 个 cell）")
print("=" * 82)
for fname, p, ok in results:
    if not ok:
        continue
    rr = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p], capture_output=True, text=True)
    t = rr.stdout
    mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
    cells = mem.group(1).split() if mem else []
    em = re.search(r'sd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};', t, re.S)
    emtxt = em.group(0) if em else ''
    freq = re.search(r'max-frequency = <([^>]*)>', emtxt)
    ddr = 'mmc-ddr-1_8v' in emtxt
    hs2 = 'mmc-hs200-1_8v' in emtxt
    magic = open(p, 'rb').read(4).hex(' ')
    okcells = 'OK(4)' if len(cells) == 4 else f'BAD({len(cells)})'
    print(f"  {fname:<28} magic={magic} cells={okcells} reg=<{' '.join(cells)}> "
          f"emmc_freq=<{freq.group(1) if freq else '-'}> ddr={ddr} hs200={hs2}")

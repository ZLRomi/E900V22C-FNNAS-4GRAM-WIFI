#!/usr/bin/env python3
"""
v5: 两组测试
  A. 内存上限：DDR52 固定，内存从 3.83G -> 4.00G 逐档
  B. eMMC 速度上限：内存固定 3.83G，速度各档
所有 reg 均为 4 个 cell
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv5'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
dts = r.stdout

EMMC_RE = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
assert EMMC_RE
EMMC = EMMC_RE.group(1)
MEM_RE = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
assert MEM_RE
MEM = MEM_RE.group(1)

# ---------------- eMMC 各速度档 ----------------
def emmc_node(flags, freq_hex):
    """flags: list of 属性字符串(不含前导制表), 插在 cap-mmc-highspeed 之后"""
    n = EMMC
    for p in ['mmc-hs200-1_8v', 'mmc-hs400-1_8v', 'mmc-ddr-1_8v']:
        n = re.sub(r'\n\t\t\t' + re.escape(p) + r';', '', n)
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)
    add = '\n\t\t\tmax-frequency = <%s>;' % freq_hex
    for f in flags:
        add += '\n\t\t\t%s;' % f
    n = n.replace('\t\t\tcap-mmc-highspeed;', '\t\t\tcap-mmc-highspeed;' + add)
    return n

SPEEDS = {
    'ddr52':    (['mmc-ddr-1_8v'],                  '0x3197500', 'DDR52 @50MHz (当前可用)'),
    'hs52':     ([],                                '0x3197500', 'HS-SDR @50MHz'),
    'hs25':     ([],                                '0x17d7840', 'HS-SDR @25MHz'),
    'hs200-50': (['mmc-hs200-1_8v'],                '0x2faf080', 'HS200 @50MHz'),
    'hs200-75': (['mmc-hs200-1_8v'],                '0x47868c0', 'HS200 @75MHz'),
    'hs200-100':(['mmc-hs200-1_8v'],                '0x5f5e100', 'HS200 @100MHz (已知写入失败)'),
    'hs400':    (['mmc-hs400-1_8v', 'mmc-hs400-enhanced-strobe'], '0x5f5e100', 'HS400 @100MHz'),
}

# ---------------- 内存各档（4 cell） ----------------
MEMS = {
    '2g':    ('0x00 0x00 0x00 0x80000000', '2GB'),
    '383':   ('0x00 0x00 0x00 0xf5700000', '3.83GB (当前可用)'),
    '385':   ('0x00 0x00 0x00 0xf6c00000', '3.86GB'),
    '390':   ('0x00 0x00 0x00 0xf9000000', '3.90GB'),
    '394':   ('0x00 0x00 0x00 0xfc000000', '3.94GB'),
    '397':   ('0x00 0x00 0x00 0xfe000000', '3.97GB'),
    '400':   ('0x00 0x00 0x01 0x00',       '4.00GB 完整'),
}

def build(fname, mem_key, spd_key, tag):
    memreg, memdesc = MEMS[mem_key]
    flags, freq, spddesc = SPEEDS[spd_key]
    newmem = re.sub(r'reg = <[^>]*>;', 'reg = <%s>;' % memreg, MEM)
    newe = emmc_node(flags, freq)
    nd = dts.replace(MEM, newmem).replace(EMMC, newe)
    dtsf = os.path.join(W, fname + '.dts')
    open(dtsf, 'w').write(nd)
    outp = os.path.join(OUT, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, dtsf],
                        capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(outp)
    if not ok:
        print(f"  ERR {fname}: {(rr.stderr or '').strip().splitlines()[:1]}")
        return None
    # 校验
    rr2 = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', outp], capture_output=True, text=True)
    t = rr2.stdout
    m = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
    cells = m.group(1).split() if m else []
    sz = os.path.getsize(outp)
    status = 'OK' if len(cells) == 4 else f'BAD({len(cells)} cell)'
    print(f"  [{status}] {fname:<32} mem=<{' '.join(cells)}> {memdesc:<18} {spddesc:<28} {sz:,}B")
    return outp

print("=" * 130)
print(" A 组：内存上限测试（eMMC 固定 DDR52）")
print("=" * 130)
for k in ['383', '385', '390', '394', '397', '400']:
    build(f'v5-mem{k}.dtb', k, 'ddr52', 'mem')

print()
print("=" * 130)
print(" B 组：eMMC 速度上限测试（内存固定 3.83GB）")
print("=" * 130)
for k in ['ddr52', 'hs52', 'hs25', 'hs200-50', 'hs200-75', 'hs200-100', 'hs400']:
    build(f'v5-spd{k}.dtb', '383', k, 'spd')

print()
print("=" * 130)
print(" 已生成文件")
print("=" * 130)
for f in sorted(os.listdir(OUT)):
    if f.startswith('v5-'):
        print(f"  {f:<34} {os.path.getsize(os.path.join(OUT,f)):>7,} B")

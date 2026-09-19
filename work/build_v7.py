#!/usr/bin/env python3
"""
v7 最终版：内存固定 3.97GB (0xFE000000) + WiFi 修复 + 各 eMMC 速度档
WiFi 修复内容：
  1. pwm_ef (pwm@19000) 启用 + 补 pinctrl/clock-names（照抄 cm311）
  2. wifi32k.pwms 改用 label 引用 &pwm_ef
  3. sd_emmc_a 下补 wifi@1 子节点 + alias
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv7'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout

MEM_RE = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
MEM_OLD = MEM_RE.group(1)
MEM_NEW = re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0x00 0xfe000000>;', MEM_OLD)

EMMC_RE = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
EMMC_OLD = EMMC_RE.group(1)

PWM_OLD = re.search(r'(\t\t\tpwm_ef: pwm@19000 \{.*?\n\t\t\t\};)', dts, re.S).group(1)
PWM_NEW = '''\t\t\tpwm_ef: pwm@19000 {
\t\t\t\tcompatible = "amlogic,meson-g12-pwm-v2", "amlogic,meson8-pwm-v2";
\t\t\t\treg = <0x00 0x19000 0x00 0x20>;
\t\t\t\tclocks = <0x08>;
\t\t\t\t#pwm-cells = <0x03>;
\t\t\t\tstatus = "okay";
\t\t\t\tpinctrl-0 = <&pwm_e_pins>;
\t\t\t\tpinctrl-names = "default";
\t\t\t\tclock-names = "clkin0";
\t\t\t\tphandle = <0x71>;
\t\t\t};'''

W32_OLD = re.search(r'(\twifi32k: wifi32k \{.*?\n\t\};)', dts, re.S).group(1)
W32_NEW = '''\twifi32k: wifi32k {
\t\tcompatible = "pwm-clock";
\t\t#clock-cells = <0x00>;
\t\tclock-frequency = <0x8000>;
\t\tpwms = <&pwm_ef 0x00 0x7736 0x00>;
\t\tphandle = <0x72>;
\t};'''

SDIO_OLD = re.search(r'(\t\tsd_emmc_a: mmc@ffe03000 \{.*?\n\t\t\};)', dts, re.S).group(1)
SDIO_NEW = SDIO_OLD.replace('\t\t};', '''\n\t\t\twifi: wifi@1 {
\t\t\t\treg = <0x01>;
\t\t\t\tcompatible = "sprd,unisoc-wifi";
\t\t\t};
\t\t};''')

ALIAS_OLD = '\t\twifi32k = "/wifi32k";'
ALIAS_NEW = '\t\twifi32k = "/wifi32k";\n\t\twifi = "/soc/mmc@ffe03000/wifi@1";'

SPEEDS = [
    ('ddr52',    ['mmc-ddr-1_8v'],                                  '0x3197500', 'DDR52 @50MHz'),
    ('hs52',     [],                                                '0x3197500', 'HS-SDR @52MHz'),
    ('hs25',     [],                                                '0x17d7840', 'HS-SDR @25MHz'),
    ('hs200-50', ['mmc-hs200-1_8v'],                                '0x2faf080', 'HS200 @50MHz'),
    ('hs200-75', ['mmc-hs200-1_8v'],                                '0x47868c0', 'HS200 @75MHz'),
    ('hs200-100',['mmc-hs200-1_8v'],                                '0x5f5e100', 'HS200 @100MHz'),
    ('hs400',    ['mmc-hs400-1_8v','mmc-hs400-enhanced-strobe'],    '0x5f5e100', 'HS400 @100MHz(200MB/s)'),
]


def emmc_node(flags, freq):
    n = EMMC_OLD
    for p in ['mmc-hs200-1_8v', 'mmc-hs400-1_8v', 'mmc-ddr-1_8v']:
        n = re.sub(r'\n\t\t\t' + re.escape(p) + r';', '', n)
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)
    add = '\n\t\t\tmax-frequency = <%s>;' % freq
    for f in flags:
        add += '\n\t\t\t%s;' % f
    return n.replace('\t\t\tcap-mmc-highspeed;', '\t\t\tcap-mmc-highspeed;' + add)


base = dts.replace(MEM_OLD, MEM_NEW).replace(PWM_OLD, PWM_NEW) \
          .replace(W32_OLD, W32_NEW).replace(SDIO_OLD, SDIO_NEW) \
          .replace(ALIAS_OLD, ALIAS_NEW)

print("=" * 118)
print(" v7 生成（内存 3.97GB + WiFi 修复 + 各 eMMC 速度）")
print("=" * 118)
made = []
for tag, flags, freq, desc in SPEEDS:
    fname = f'v7-wifi-{tag}.dtb'
    d = base.replace(EMMC_OLD, emmc_node(flags, freq))
    p = os.path.join(W, fname + '.dts')
    open(p, 'w').write(d)
    outp = os.path.join(OUT, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, p], capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(outp)
    if not ok:
        print(f"  ERR {fname}: {(rr.stderr or '').strip().splitlines()[:2]}")
        continue
    made.append((fname, outp, desc))

print()
print("=" * 118)
print(" 校验")
print("=" * 118)
for fname, p, desc in made:
    rr = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p], capture_output=True, text=True)
    t = rr.stdout
    mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
    cells = mem.group(1).split() if mem else []
    pwm = re.search(r'pwm_ef: pwm@19000 \{(.*?)\n\t\t\t\};', t, re.S)
    pwmtxt = pwm.group(1) if pwm else ''
    em = re.search(r'sd_emmc_c: mmc@ffe07000 \{(.*?)\n\t\t\};', t, re.S)
    emtxt = em.group(0) if em else ''
    fq = re.search(r'max-frequency = <([^>]*)>', emtxt)
    print(f"  {fname:<28} cells={len(cells)} reg=<{' '.join(cells)}> "
          f"pwm={'okay' if 'status = \"okay\"' in pwmtxt else 'BAD'} "
          f"wifi@1={'Y' if 'wifi@1' in t else 'N'} "
          f"freq=<{fq.group(1) if fq else '?'}> ddr={'mmc-ddr-1_8v' in emtxt} "
          f"hs200={'mmc-hs200-1_8v' in emtxt} hs400={'mmc-hs400-1_8v' in emtxt}  ({desc})")

print()
print(f"  共生成 {len(made)} 个文件")

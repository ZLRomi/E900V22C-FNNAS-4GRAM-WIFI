#!/usr/bin/env python3
"""
v8 FINAL: 完整的 UWE5621DS WiFi 支持 dtb
基线: 3.97GB 内存 + eMMC DDR52（已验证可用）
新增（依 NullYing 仓库 README/§1/§18.7）:
  1. sd_emmc_a   + cap-sdio-irq
  2. pwrseq      + post-power-on-delay-ms=1000, power-off-delay-us=10000,
                 + clocks=<&wifi32k>, clock-names="ext_clock"
  3. pwm_ef      启用 + pinctrl-0 + clock-names（LPO 32K 时钟源）
  4. wifi@1      compatible = "uwcnmodem,we5621ds" + reg=<1>
  5. alias       wifi
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv8'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout

# ---------- 1. memory ----------
MEM_RE = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
assert MEM_RE
MEM_OLD = MEM_RE.group(1)
MEM_NEW = re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0x00 0xfe000000>;', MEM_OLD)

# ---------- 2. eMMC DDR52 ----------
EMMC_RE = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
assert EMMC_RE
EMMC_OLD = EMMC_RE.group(1)
def ddr52(n):
    for p in ['mmc-hs200-1_8v', 'mmc-hs400-1_8v', 'mmc-ddr-1_8v']:
        n = re.sub(r'\n\t\t\t' + re.escape(p) + r';', '', n)
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)
    return n.replace('\t\t\tcap-mmc-highspeed;',
                     '\t\t\tcap-mmc-highspeed;\n\t\t\tmax-frequency = <0x3197500>;\n\t\t\tmmc-ddr-1_8v;')
EMMC_NEW = ddr52(EMMC_OLD)

# ---------- 3. sd_emmc_a: 加 cap-sdio-irq ----------
SDIO_RE = re.search(r'(\t\tsd_emmc_a: mmc@ffe03000 \{.*?\n\t\t\};)', dts, re.S)
assert SDIO_RE
SDIO_OLD = SDIO_RE.group(1)
SDIO_NEW = SDIO_OLD
if 'cap-sdio-irq' not in SDIO_NEW:
    SDIO_NEW = SDIO_NEW.replace('\t\t\tcap-sd-highspeed;',
                                '\t\t\tcap-sd-highspeed;\n\t\t\tcap-sdio-irq;')
# 补 wifi@1 子节点（compatible 用 uwcnmodem,we5621ds）
if 'wifi@1' not in SDIO_NEW:
    SDIO_NEW = SDIO_NEW.replace('\t\t};', '''\n\t\t\twifi: wifi@1 {
\t\t\t\tcompatible = "uwcnmodem,we5621ds";
\t\t\t\treg = <0x01>;
\t\t\t};
\t\t};''')

# ---------- 4. pwm_ef 启用 ----------
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

# ---------- 5. wifi32k 规范化 ----------
W32_OLD = re.search(r'(\twifi32k: wifi32k \{.*?\n\t\};)', dts, re.S).group(1)
W32_NEW = '''\twifi32k: wifi32k {
\t\tcompatible = "pwm-clock";
\t\t#clock-cells = <0x00>;
\t\tclock-frequency = <0x8000>;
\t\tpwms = <&pwm_ef 0x00 0x7736 0x00>;
\t\tphandle = <0x72>;
\t};'''

# ---------- 6. pwrseq 节点（按 compatible 定位） ----------
PWR_RE = re.search(r'(\t\t?[\w\-]*pwrseq[\w\-]* \{.*?compatible = "mmc-pwrseq-simple".*?\n\t\t?\};)', dts, re.S)
if not PWR_RE:
    PWR_RE = re.search(r'(\t[\w\-]+ \{.*?compatible = "mmc-pwrseq-simple".*?\n\t\};)', dts, re.S)
assert PWR_RE, "找不到 mmc-pwrseq-simple 节点"
PWR_OLD = PWR_RE.group(1)
print("=" * 100)
print(" 原始 pwrseq 节点")
print("=" * 100)
print(PWR_OLD)

# 构造新节点：保留 reset-gpios，加上延时与 ext_clock
rg = re.search(r'reset-gpios = <([^>]+)>;', PWR_OLD)
reset = rg.group(1) if rg else None
print(f"\n  reset-gpios = <{reset}>")

indent = '\t' if PWR_OLD.startswith('\t\t') is False else '\t'
# 判断节点本身的缩进层级
first_line = PWR_OLD.splitlines()[0]
base_ind = first_line[:len(first_line) - len(first_line.lstrip('\t'))]
inner = base_ind + '\t'
close = base_ind

PWR_NEW = (f'{base_ind}sdio-pwrseq {{\n'
           f'{inner}compatible = "mmc-pwrseq-simple";\n')
if reset:
    PWR_NEW += f'{inner}reset-gpios = <{reset}>;\n'
PWR_NEW += (f'{inner}post-power-on-delay-ms = <0x3e8>;\n'
            f'{inner}power-off-delay-us = <0x2710>;\n'
            f'{inner}clocks = <&wifi32k>;\n'
            f'{inner}clock-names = "ext_clock";\n'
            f'{close}}};')

print()
print("=" * 100)
print(" 新 pwrseq 节点")
print("=" * 100)
print(PWR_NEW)

# ---------- alias ----------
ALIAS_OLD = '\t\twifi32k = "/wifi32k";'
ALIAS_NEW = '\t\twifi32k = "/wifi32k";\n\t\twifi = "/soc/mmc@ffe03000/wifi@1";'

# ---------- 组装 ----------
d = dts.replace(MEM_OLD, MEM_NEW)
d = d.replace(EMMC_OLD, EMMC_NEW)
d = d.replace(SDIO_OLD, SDIO_NEW)
d = d.replace(PWM_OLD, PWM_NEW)
d = d.replace(W32_OLD, W32_NEW)
d = d.replace(PWR_OLD, PWR_NEW)
if ALIAS_OLD in d:
    d = d.replace(ALIAS_OLD, ALIAS_NEW)

fname = 'v8-uwe5621ds-3.97g-ddr52.dtb'
p = os.path.join(W, fname + '.dts')
open(p, 'w').write(d)
outp = os.path.join(OUT, fname)
rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, p], capture_output=True, text=True)
ok = rr.returncode == 0 and os.path.exists(outp)
print()
print("=" * 100)
if not ok:
    print(" 编译失败:")
    print((rr.stderr or '')[:3000])
    raise SystemExit(1)
print(f" 编译成功: {fname}   {os.path.getsize(outp):,} B")

# ---------- 校验 ----------
rr2 = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', outp], capture_output=True, text=True)
t = rr2.stdout
print()
print("=" * 100)
print(" 校验")
print("=" * 100)
mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
print(f"  memory      = <{mem.group(1)}>  ({len(mem.group(1).split())} cells)")
pwm = re.search(r'pwm_ef: pwm@19000 \{(.*?)\n\t\t\t\};', t, re.S)
pt = pwm.group(1) if pwm else ''
print(f"  pwm_ef      : status={'okay' if 'okay' in pt else 'BAD'} pinctrl={'Y' if 'pinctrl-0' in pt else 'N'}")
w32 = re.search(r'wifi32k: wifi32k \{(.*?)\n\t\};', t, re.S)
print(f"  wifi32k     : pwms&pwm_ef={'Y' if '&pwm_ef' in (w32.group(1) if w32 else '') else 'N'}")
pwr = re.search(r'sdio-pwrseq \{(.*?)\n\t\};', t, re.S)
pwrt = pwr.group(1) if pwr else ''
print(f"  sdio-pwrseq : reset={'Y' if 'reset-gpios' in pwrt else 'N'} delay={'Y' if 'post-power-on-delay' in pwrt else 'N'} extclk={'Y' if 'ext_clock' in pwrt else 'N'}")
sdio = re.search(r'sd_emmc_a: mmc@ffe03000 \{(.*?)\n\t\t\};', t, re.S)
st = sdio.group(1) if sdio else ''
print(f"  sd_emmc_a   : cap-sdio-irq={'Y' if 'cap-sdio-irq' in st else 'N'} wifi@1={'Y' if 'wifi@1' in st else 'N'}")
em = re.search(r'sd_emmc_c: mmc@ffe07000 \{(.*?)\n\t\t\};', t, re.S)
et = em.group(1) if em else ''
fq = re.search(r'max-frequency = <([^>]*)>', et)
print(f"  eMMC        : freq=<{fq.group(1) if fq else '?'}> ddr={'Y' if 'mmc-ddr-1_8v' in et else 'N'} hs200={'Y' if 'hs200' in et else 'N'}")
w1 = re.search(r'wifi@1 \{(.*?)\};', t, re.S)
print(f"  wifi@1      : {(w1.group(1).strip() if w1 else 'N/A')}")

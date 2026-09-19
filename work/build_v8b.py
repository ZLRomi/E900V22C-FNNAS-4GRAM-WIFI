#!/usr/bin/env python3
"""
v8b: 修正版 —— 精确匹配 pwrseq 节点
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv8b'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout


def must_replace(d, old, new, tag):
    assert old in d, f"[{tag}] 未找到目标文本"
    return d.replace(old, new)


# ---- 1. memory 3.97GB ----
MEM_OLD = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S).group(1)
MEM_NEW = re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0x00 0xfe000000>;', MEM_OLD)

# ---- 2. eMMC DDR52 ----
EMMC_OLD = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S).group(1)
_e = EMMC_OLD
for p in ['mmc-hs200-1_8v', 'mmc-hs400-1_8v', 'mmc-ddr-1_8v']:
    _e = re.sub(r'\n\t\t\t' + re.escape(p) + r';', '', _e)
_e = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', _e)
EMMC_NEW = _e.replace('\t\t\tcap-mmc-highspeed;',
                      '\t\t\tcap-mmc-highspeed;\n\t\t\tmax-frequency = <0x3197500>;\n\t\t\tmmc-ddr-1_8v;')

# ---- 3. sd_emmc_a: cap-sdio-irq + wifi@1 ----
SDIO_OLD = re.search(r'(\t\tsd_emmc_a: mmc@ffe03000 \{.*?\n\t\t\};)', dts, re.S).group(1)
SDIO_NEW = SDIO_OLD.replace('\t\t\tcap-sd-highspeed;',
                            '\t\t\tcap-sd-highspeed;\n\t\t\tcap-sdio-irq;')
assert 'cap-sdio-irq' in SDIO_NEW, "cap-sdio-irq 插入失败"
SDIO_NEW = SDIO_NEW.replace('\t\t};', '''\n\t\t\twifi: wifi@1 {
\t\t\t\tcompatible = "uwcnmodem,we5621ds";
\t\t\t\treg = <0x01>;
\t\t\t};
\t\t};''')

# ---- 4. pwm_ef 启用 ----
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

# ---- 5. wifi32k 用 label 引用 ----
W32_OLD = re.search(r'(\twifi32k: wifi32k \{.*?\n\t\};)', dts, re.S).group(1)
W32_NEW = '''\twifi32k: wifi32k {
\t\tcompatible = "pwm-clock";
\t\t#clock-cells = <0x00>;
\t\tclock-frequency = <0x8000>;
\t\tpwms = <&pwm_ef 0x00 0x7736 0x00>;
\t\tphandle = <0x72>;
\t};'''

# ---- 6. sdio-pwrseq 精确匹配 ----
PWR_RE = re.search(r'(\tsdio_pwrseq: sdio-pwrseq \{.*?\n\t\};)', dts, re.S)
assert PWR_RE, "找不到 sdio_pwrseq 节点"
PWR_OLD = PWR_RE.group(1)
print("=" * 96)
print(" 原始 sdio-pwrseq 节点")
print("=" * 96)
print(PWR_OLD)
rg = re.search(r'reset-gpios = <([^>]+)>;', PWR_OLD)
assert rg, "找不到 reset-gpios"
RESET = rg.group(1)
assert RESET.startswith('0x32 0x47'), f"reset-gpios 意外: {RESET}"

PWR_NEW = (f'\tsdio_pwrseq: sdio-pwrseq {{\n'
           f'\t\tcompatible = "mmc-pwrseq-simple";\n'
           f'\t\treset-gpios = <{RESET}>;\n'
           f'\t\tpost-power-on-delay-ms = <0x3e8>;\n'
           f'\t\tpower-off-delay-us = <0x2710>;\n'
           f'\t\tclocks = <&wifi32k>;\n'
           f'\t\tclock-names = "ext_clock";\n'
           f'\t\tphandle = <0x2e>;\n'
           f'\t}};')
print()
print(" 新节点:")
print(PWR_NEW)

# ---- 7. mmc-pwrseq 用 label ----
PWRREF_OLD = 'mmc-pwrseq = <0x2e>;'
PWRREF_NEW = 'mmc-pwrseq = <&sdio_pwrseq>;'

# ---- 8. alias ----
AL_OLD = '\t\twifi32k = "/wifi32k";'
AL_NEW = '\t\twifi32k = "/wifi32k";\n\t\twifi = "/soc/mmc@ffe03000/wifi@1";'

# ---- 应用 ----
d = dts
d = must_replace(d, MEM_OLD, MEM_NEW, 'memory')
d = must_replace(d, EMMC_OLD, EMMC_NEW, 'emmc')
d = must_replace(d, SDIO_OLD, SDIO_NEW, 'sdio_a')
d = must_replace(d, PWM_OLD, PWM_NEW, 'pwm_ef')
d = must_replace(d, W32_OLD, W32_NEW, 'wifi32k')
d = must_replace(d, PWR_OLD, PWR_NEW, 'pwrseq')
if PWRREF_OLD in d:
    d = d.replace(PWRREF_OLD, PWRREF_NEW)
    print("\n  mmc-pwrseq 引用已改为 &sdio_pwrseq")
if AL_OLD in d:
    d = d.replace(AL_OLD, AL_NEW)
    print("  wifi alias 已添加")

fname = 'v8b-uwe5621ds-3.97g-ddr52.dtb'
p = os.path.join(W, fname + '.dts')
open(p, 'w').write(d)
outp = os.path.join(OUT, fname)
rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, p], capture_output=True, text=True)
if rr.returncode != 0:
    print("\n编译失败:\n" + (rr.stderr or '')[:2500])
    raise SystemExit(1)
print(f"\n编译成功: {fname}  {os.path.getsize(outp):,} B")

# ---- 校验 ----
t = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', outp], capture_output=True, text=True).stdout
print()
print("=" * 96)
print(" 逐项校验")
print("=" * 96)
mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
print(f"  memory       <{mem.group(1)}>  cells={len(mem.group(1).split())}")

pwm = re.search(r'pwm_ef: pwm@19000 \{(.*?)\n\t\t\t\};', t, re.S)
pt = pwm.group(1) if pwm else ''
print(f"  pwm_ef       status={'okay' if 'okay' in pt else 'BAD'}  pinctrl={'Y' if 'pinctrl-0' in pt else 'N'}  clock-names={'Y' if 'clock-names' in pt else 'N'}")

w32 = re.search(r'wifi32k: wifi32k \{(.*?)\n\t\};', t, re.S)
wt = w32.group(1) if w32 else ''
print(f"  wifi32k      pwms={'Y' if 'pwms' in wt else 'N'}  freq={'Y' if '0x8000' in wt else 'N'}")

pwr = re.search(r'sdio_pwrseq: sdio-pwrseq \{(.*?)\n\t\};', t, re.S)
pw = pwr.group(1) if pwr else ''
print(f"  sdio-pwrseq  reset={'Y' if 'reset-gpios' in pw else 'N'}  post-delay={'Y' if 'post-power-on-delay-ms' in pw else 'N'}  ext_clock={'Y' if 'ext_clock' in pw else 'N'}")
m = re.search(r'reset-gpios = <([^>]+)>', pw)
print(f"               reset-gpios=<{m.group(1) if m else '?'}>  (应为 0x32 0x47 0x01)")

sdio = re.search(r'sd_emmc_a: mmc@ffe03000 \{(.*?)\n\t\t\};', t, re.S)
st = sdio.group(1) if sdio else ''
print(f"  sd_emmc_a    cap-sdio-irq={'Y' if 'cap-sdio-irq' in st else 'N'}  wifi@1={'Y' if 'wifi@1' in st else 'N'}  pwrseq-ref={'Y' if 'mmc-pwrseq' in st else 'N'}")

em = re.search(r'sd_emmc_c: mmc@ffe07000 \{(.*?)\n\t\t\};', t, re.S)
et = em.group(1) if em else ''
fq = re.search(r'max-frequency = <([^>]*)>', et)
print(f"  eMMC         freq=<{fq.group(1) if fq else '?'}>  ddr={'Y' if 'mmc-ddr-1_8v' in et else 'N'}  hs200={'Y' if 'mmc-hs200-1_8v' in et else 'N'}")

w1 = re.search(r'wifi@1 \{(.*?)\n\t\t\t\};', t, re.S)
print(f"  wifi@1       {(w1.group(1).strip().replace(chr(10),' ') if w1 else 'N/A')}")

# 结构完整性检查：关键节点是否都还在
print()
print(" 结构完整性检查:")
for node in ['aliases', 'chosen', 'reserved-memory', 'secmon@5000000', 'linux,cma',
             'ethernet@ff3f0000', 'sd_emmc_b', 'sd_emmc_c', 'usb@ff500000']:
    print(f"    {node:<24} {'存在' if node in t else '★丢失★'}")
print(f"\n  dts 总行数: {len(t.splitlines())}")

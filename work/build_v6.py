#!/usr/bin/env python3
"""
v6: WiFi 修复
  基础 = 3.83GB 内存 + DDR52 eMMC（已验证可用）
  修复 = 启用 pwm_ef(pwm@19000) + 照抄 cm311 的时钟/引脚定义
  变体1: 仅修 PWM
  变体2: 修 PWM + 补 wifi@1 子节点 + alias
"""
import re
import os
import subprocess
import shutil

STOCK = '/tmp/fn_boot/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/dtb-variants'
W = '/tmp/bv6'
shutil.rmtree(W, ignore_errors=True)
os.makedirs(W)
os.makedirs(OUT, exist_ok=True)

r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', STOCK], capture_output=True, text=True)
assert r.returncode == 0
dts = r.stdout

# ---------------- 定位 phandle 0x08（pwm 的 clkin） ----------------
print("=" * 90)
print(" 1. 查找 phandle 0x08（pwm clkin 时钟源）")
print("=" * 90)
lines = dts.splitlines()
for i, ln in enumerate(lines):
    if re.match(r'\s*phandle = <0x8>;', ln):
        for j in range(i, max(0, i - 15), -1):
            if re.search(r'^\s*[\w\-]+:?\s+[\w\-@]+\s*\{', lines[j]) or re.search(r'^\s*[\w\-]+:\s+[\w\-@]+ \{', lines[j]):
                print(f"  line {j+1}: {lines[j].strip()}")
                for k in range(j, min(len(lines), j + 12)):
                    print("    " + lines[k])
                    if lines[k].strip() == '};':
                        break
                break
        break

# ---------------- 原始 pwm_ef 节点 ----------------
print()
print("=" * 90)
print(" 2. 原始 pwm_ef 节点")
print("=" * 90)
PWM_RE = re.search(r'(\t\t\tpwm_ef: pwm@19000 \{.*?\n\t\t\t\};)', dts, re.S)
assert PWM_RE, "找不到 pwm_ef 节点"
PWM_OLD = PWM_RE.group(1)
print(PWM_OLD)

# cm311 风格的节点（用 label 引用 pinctrl，避免 phandle 数字错位）
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

# ---------------- wifi32k 的 pwms 用 label ----------------
print()
print("=" * 90)
print(" 3. wifi32k 节点（pwms 改用 label 引用）")
print("=" * 90)
W32_RE = re.search(r'(\twifi32k: wifi32k \{.*?\n\t\};)', dts, re.S)
assert W32_RE, "找不到 wifi32k"
W32_OLD = W32_RE.group(1)
print(W32_OLD)
W32_NEW = '''\twifi32k: wifi32k {
\t\tcompatible = "pwm-clock";
\t\t#clock-cells = <0x00>;
\t\tclock-frequency = <0x8000>;
\t\tpwms = <&pwm_ef 0x00 0x7736 0x00>;
\t\tphandle = <0x72>;
\t};'''

# ---------------- 内存 / eMMC ----------------
MEM_RE = re.search(r'(\tmemory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <[^>]*>;\n\t\};)', dts, re.S)
MEM_OLD = MEM_RE.group(1)
MEM_NEW = re.sub(r'reg = <[^>]*>;', 'reg = <0x00 0x00 0x00 0xf5700000>;', MEM_OLD)

EMMC_RE = re.search(r'(\t\tsd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};)', dts, re.S)
EMMC_OLD = EMMC_RE.group(1)


def emmc_ddr52(node):
    n = node
    for p in ['mmc-hs200-1_8v', 'mmc-hs400-1_8v', 'mmc-ddr-1_8v']:
        n = re.sub(r'\n\t\t\t' + re.escape(p) + r';', '', n)
    n = re.sub(r'\n\t\t\tmax-frequency = <[^>]*>;', '', n)
    return n.replace('\t\t\tcap-mmc-highspeed;',
                     '\t\t\tcap-mmc-highspeed;\n\t\t\tmax-frequency = <0x3197500>;\n\t\t\tmmc-ddr-1_8v;')


EMMC_NEW = emmc_ddr52(EMMC_OLD)

# ---------------- sd_emmc_a 加 wifi@1 子节点 ----------------
SDIO_RE = re.search(r'(\t\tsd_emmc_a: mmc@ffe03000 \{.*?\n\t\t\};)', dts, re.S)
SDIO_OLD = SDIO_RE.group(1)
# 在最后一行 }; 前插入 wifi 子节点
SDIO_NEW = SDIO_OLD.replace(
    '\t\t};',
    '''\n\t\t\twifi: wifi@1 {
\t\t\t\treg = <0x01>;
\t\t\t\tcompatible = "sprd,unisoc-wifi";
\t\t\t};
\t\t};'''
)

ALIAS_OLD = '\t\twifi32k = "/wifi32k";'
ALIAS_NEW = '\t\twifi32k = "/wifi32k";\n\t\twifi = "/soc/mmc@ffe03000/wifi@1";'


def build(fname, with_wifi_node):
    d = dts
    d = d.replace(MEM_OLD, MEM_NEW)
    d = d.replace(EMMC_OLD, EMMC_NEW)
    d = d.replace(PWM_OLD, PWM_NEW)
    d = d.replace(W32_OLD, W32_NEW)
    if with_wifi_node:
        d = d.replace(SDIO_OLD, SDIO_NEW)
        if ALIAS_OLD in d:
            d = d.replace(ALIAS_OLD, ALIAS_NEW)
    p = os.path.join(W, fname + '.dts')
    open(p, 'w').write(d)
    outp = os.path.join(OUT, fname)
    rr = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', outp, p],
                        capture_output=True, text=True)
    ok = rr.returncode == 0 and os.path.exists(outp)
    if not ok:
        print(f"  ERR {fname}: {(rr.stderr or '').strip().splitlines()[:2]}")
        return
    print(f"  OK  {fname:<34} {os.path.getsize(outp):>7,} B")


print()
print("=" * 90)
print(" 4. 生成变体")
print("=" * 90)
build('v6-wifi-pwm.dtb', False)
build('v6-wifi-pwm-node.dtb', True)

# ---------------- 校验 ----------------
print()
print("=" * 90)
print(" 5. 校验（反编译确认）")
print("=" * 90)
for f in ['v6-wifi-pwm.dtb', 'v6-wifi-pwm-node.dtb']:
    p = os.path.join(OUT, f)
    if not os.path.exists(p):
        continue
    rr = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', p], capture_output=True, text=True)
    t = rr.stdout
    mem = re.search(r'memory@0 \{\n\t\tdevice_type = "memory";\n\t\treg = <([^>]*)>;', t)
    pwm = re.search(r'pwm_ef: pwm@19000 \{(.*?)\n\t\t\t\};', t, re.S)
    pwm_txt = pwm.group(1) if pwm else ''
    em = re.search(r'sd_emmc_c: mmc@ffe07000 \{.*?\n\t\t\};', t, re.S)
    emtxt = em.group(0) if em else ''
    print(f"\n  === {f} ===")
    print(f"    magic      = {open(p,'rb').read(4).hex(' ')}")
    print(f"    memory reg = <{mem.group(1) if mem else '?'}>  ({len((mem.group(1) if mem else '').split())} cells)")
    print(f"    pwm_ef     : status={'okay' if 'status = \"okay\"' in pwm_txt else 'BAD'} "
          f"pinctrl={'yes' if 'pinctrl-0' in pwm_txt else 'no'} "
          f"clknames={'yes' if 'clock-names' in pwm_txt else 'no'}")
    print(f"    wifi32k    : pwms={'label' if 'pwms = <0x71' in t or 'pwm_ef' in (re.search(r'wifi32k: wifi32k \{(.*?)\n\t\};', t, re.S).group(1) if re.search(r'wifi32k: wifi32k \{(.*?)\n\t\};', t, re.S) else '') else '?'}")
    print(f"    eMMC freq  = <{re.search(r'sd_emmc_c: mmc@ffe07000.*?max-frequency = <([^>]*)>', t, re.S).group(1) if re.search(r'sd_emmc_c.*?max-frequency = <([^>]*)>', t, re.S) else '?'}>, ddr={'mmc-ddr-1_8v' in emtxt}, hs200={'mmc-hs200-1_8v' in emtxt}")
    print(f"    wifi@1节点 = {'有' if 'wifi@1' in t else '无'}")

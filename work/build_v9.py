#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 DDR52 超频变体：保持 v8b 的全部修改（3.97G 内存 / WiFi 四节点 / DDR52），
只改 sd_emmc_c 的 max-frequency。
"""
import os, re, shutil, subprocess, sys

BASE = "/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas"
VARI = os.path.join(BASE, "dtb-variants")
SRC = os.path.join(VARI, "v8b-uwe5621ds-3.97g-ddr52.dtb")
WORK = "/tmp/bv9"

# 目标频率 (Hz) -> 标签
FREQS = [
    (52_000_000,  "base52"),
    (65_000_000,  "oc65"),
    (75_000_000,  "oc75"),
    (80_000_000,  "oc80"),
    (100_000_000, "oc100"),
    (150_000_000, "oc150"),
]


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def get_emmc_block(dts):
    """取 mmc@ffe07000 节点整块（节点名可能带 label 前缀）"""
    m = re.search(r'\n(\t+)(?:[A-Za-z_][\w-]*\s*:\s*)?mmc@ffe07000 \{(.*?)\n\1\};',
                  dts, re.S)
    return m


def main():
    os.makedirs(WORK, exist_ok=True)
    if not os.path.exists(SRC):
        print("找不到基准 dtb:", SRC); sys.exit(1)

    # 解编译基准
    dts = os.path.join(WORK, "base.dts")
    r = run(f"dtc -I dtb -O dts -o {dts} {SRC}")
    if not os.path.exists(dts):
        print("dtc 解编译失败:", r.stderr); sys.exit(1)

    base = open(dts, encoding='utf-8', errors='surrogateescape').read()

    m = get_emmc_block(base)
    if not m:
        print("找不到 mmc@ffe07000 节点"); sys.exit(1)

    blk = m.group(0)
    print("=== 基准 sd_emmc_c 关键属性 ===")
    for line in blk.splitlines():
        if any(k in line for k in ('bus-width', 'max-frequency', 'mmc-ddr',
                                   'mmc-hs200', 'mmc-hs400', 'cap-mmc')):
            print("   ", line.strip())

    outdir = os.path.join(VARI, "emmc-speed")
    os.makedirs(outdir, exist_ok=True)

    print("\n=== 生成变体 ===")
    for hz, tag in FREQS:
        nb = re.sub(r'max-frequency = <[^>]*>;',
                    f'max-frequency = <0x{hz:x}>;  /* {hz//1000000} MHz */',
                    blk, count=1)
        if 'max-frequency' not in nb:
            # 节点里没有该属性 -> 插到 bus-width 后面
            nb = nb.replace('bus-width = <0x08>;',
                            f'bus-width = <0x08>;\n\t\t\tmax-frequency = <0x{hz:x}>;')
        newdts = base.replace(blk, nb, 1)

        dp = os.path.join(WORK, f"{tag}.dts")
        open(dp, 'w', encoding='utf-8', errors='surrogateescape').write(newdts)

        out = os.path.join(outdir, f"v9-ddr52-{hz//1000000}mhz.dtb")
        r = run(f"dtc -I dts -O dtb -o {out} {dp}")
        if not os.path.exists(out):
            print(f"  [FAIL] {tag}: {r.stderr.strip()[:120]}")
            continue

        # 校验
        chk = run(f"dtc -I dtb -O dts {out}").stdout
        mm = get_emmc_block(chk)
        ok_mem = '0xfe000000' in chk
        ok_ddr = 'mmc-ddr-1_8v' in (mm.group(0) if mm else '')
        ok_hs200 = 'mmc-hs200' in (mm.group(0) if mm else '')
        freq = re.search(r'max-frequency = <([^>]*)>', mm.group(0) if mm else '')
        ok_wifi = 'uwcnmodem,we5621ds' in chk

        print(f"  {os.path.basename(out):32s} size={os.path.getsize(out):6d}  "
              f"freq={freq.group(1) if freq else '?':>12s}  "
              f"mem={'OK' if ok_mem else 'FAIL'} "
              f"ddr={'OK' if ok_ddr else 'FAIL'} "
              f"hs200={'有' if ok_hs200 else '无'} "
              f"wifi={'OK' if ok_wifi else 'FAIL'}")

    print("\n=== 输出目录 ===")
    print(" ", outdir)
    for f in sorted(os.listdir(outdir)):
        print("   ", f, os.path.getsize(os.path.join(outdir, f)))


if __name__ == '__main__':
    main()

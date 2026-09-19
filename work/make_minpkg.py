#!/usr/bin/env python3
"""
制作"最小 bootloader 包"：
只烧 bootloader + dtb + platform.conf，不烧 boot/system/vendor。
烧完后 eMMC 有能干活的 U-Boot，但没有可启动系统，
U-Boot 的 storeboot 会失败 -> 自动 run update -> 扫描 U 盘 -> 启动飞牛。
"""
import subprocess
import sys
import os
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import amlpack

SRC = ('/mnt/c/Users/Administrator/Downloads/Compressed/'
       'S905L3A线刷4G运存夏杰语音当贝3(22C)/'
       'S905L3A线刷4G运存夏杰语音当贝3(22C).img')
EXT = '/tmp/min_ext'
OUT = '/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas/out/fnnas-e900v22c-minbootloader.img'

print("=" * 78)
print("步骤 1: 从官方 4G 线刷包提取必要分区")
print("=" * 78)
shutil.rmtree(EXT, ignore_errors=True)
os.makedirs(EXT, exist_ok=True)
r = subprocess.run(['python', 'work/amlpkg.py', 'extract', SRC, EXT],
                   cwd='/mnt/c/Users/Administrator/Desktop/e900v22c-fnnas',
                   capture_output=True, text=True)
# 只打印关键行
for line in (r.stdout or '').splitlines():
    if any(k in line for k in ['USB_DDR', 'USB_UBOOT', 'bootloader', '_aml_dtb', 'platform']):
        print("  " + line.strip())

# 找到文件（名字由 amlpkg.py 的 idx_main_sub 规则生成）
def find(name):
    for f in os.listdir(EXT):
        if f.startswith(name):
            return os.path.join(EXT, f)
    return None

f_ddr = find('00_USB_DDR')
f_dtb = find('02_PARTITION__aml_dtb')
f_conf = find('13_conf_platform') or find('15_conf_platform')
# 兼容不同包的编号
if not f_conf:
    for f in os.listdir(EXT):
        if 'conf_platform' in f:
            f_conf = os.path.join(EXT, f)

for lbl, p in [('bootloader', f_ddr), ('_aml_dtb', f_dtb), ('platform.conf', f_conf)]:
    if p and os.path.exists(p):
        print(f"  {lbl:<14} -> {os.path.basename(p)}  ({os.path.getsize(p):,} 字节)")
    else:
        print(f"  {lbl:<14} -> 缺失!")

if not (f_ddr and os.path.exists(f_ddr)):
    print("\n提取 bootloader 失败，中止")
    sys.exit(1)

bootloader = open(f_ddr, 'rb').read()
dtb = open(f_dtb, 'rb').read() if f_dtb and os.path.exists(f_dtb) else b''
conf = open(f_conf, 'rb').read() if f_conf and os.path.exists(f_conf) else b''

print()
print("=" * 78)
print("步骤 2: 构造最小包")
print("=" * 78)

items = [
    dict(main='USB', sub='DDR',       type=amlpack.TYPE_NORMAL,
         verify=0, is_backup=0, backup_id=0, data=bootloader),
    dict(main='USB', sub='UBOOT',     type=amlpack.TYPE_NORMAL,
         verify=0, is_backup=1, backup_id=1, data=bootloader),
]
if dtb:
    items.append(dict(main='PARTITION', sub='_aml_dtb', type=amlpack.TYPE_NORMAL,
                      verify=1, is_backup=0, backup_id=0, data=dtb))
items.append(dict(main='PARTITION', sub='bootloader', type=amlpack.TYPE_NORMAL,
                  verify=1, is_backup=1, backup_id=1, data=bootloader))
if conf:
    items.append(dict(main='conf', sub='platform', type=amlpack.TYPE_NORMAL,
                      verify=0, is_backup=0, backup_id=0, data=conf))

for it in items:
    print(f"  {it['main']:<10} {it['sub']:<12} size={len(it['data']):>10,}  "
          f"verify={it['verify']} bkp={it['is_backup']}")

data = amlpack.build(items)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'wb') as f:
    f.write(data)

print()
print("=" * 78)
print("步骤 3: 自检生成的包")
print("=" * 78)
ok = amlpack.verify(OUT)
print()
if ok:
    print(f"[成功] 已生成: {OUT}")
    print(f"       大小: {len(data):,} 字节 ({len(data)/1024/1024:.1f} MB)")
else:
    print("[失败] 包校验未通过")
    sys.exit(1)

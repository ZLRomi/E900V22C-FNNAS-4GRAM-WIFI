#!/usr/bin/env python3
"""
构造 Amlogic u-boot 可识别的 autoscript (uImage type=script)
- magic 0x27051956, os=5(LINUX) arch=2(ARM) type=6(SCRIPT) comp=0
- hcrc = crc32(header with hcrc=0), dcrc = crc32(data)
"""
import struct
import zlib
import sys
import os

IH_MAGIC = 0x27051956


def build_uimage(data, name=b'', load=0, ep=0, os_id=5, arch=2, typ=6, comp=0, ts=0):
    hdr = struct.pack('>IIIIIIIBBBB',
                      IH_MAGIC, 0, ts, len(data), load, ep,
                      zlib.crc32(data) & 0xffffffff,
                      os_id, arch, typ, comp)
    hdr += name.ljust(32, b'\x00')[:32]
    assert len(hdr) == 64, len(hdr)
    hcrc = zlib.crc32(hdr) & 0xffffffff
    hdr = hdr[:4] + struct.pack('>I', hcrc) + hdr[8:]
    return hdr + data


def parse_uimage(raw):
    magic, hcrc, ts, size, load, ep, dcrc, o, a, t, c = struct.unpack('>IIIIIIIBBBB', raw[:32])
    name = raw[32:64].split(b'\x00')[0]
    data = raw[64:64 + size]
    chdr = raw[:4] + b'\x00\x00\x00\x00' + raw[8:64]
    return dict(magic=magic, hcrc=hcrc, hcrc_calc=zlib.crc32(chdr) & 0xffffffff,
                size=size, load=load, ep=ep, dcrc=dcrc,
                dcrc_calc=zlib.crc32(data) & 0xffffffff,
                os=o, arch=a, type=t, comp=c, name=name, data=data)


def verify(path):
    raw = open(path, 'rb').read()
    p = parse_uimage(raw)
    ok = (p['magic'] == IH_MAGIC and p['hcrc'] == p['hcrc_calc']
          and p['dcrc'] == p['dcrc_calc'] and len(raw) == 64 + p['size'])
    print(f"  {os.path.basename(path)}: {len(raw)} B  magic={'OK' if p['magic']==IH_MAGIC else 'BAD'} "
          f"hcrc={'OK' if p['hcrc']==p['hcrc_calc'] else 'BAD'} "
          f"dcrc={'OK' if p['dcrc']==p['dcrc_calc'] else 'BAD'} "
          f"size={p['size']} type={p['type']} os={p['os']} arch={p['arch']} -> {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- 新脚本
SCRIPT = r'''echo "=========== custom boot (port-agnostic) ==========="
setenv kernel_addr_r 0x11000000
setenv ramdisk_addr_r 0x15000000
setenv fdt_addr_r 0x1000000
setenv lb "0 1 2 3"
echo "--- step1: look for u-boot.ext on usb 0..3 ---"
for d in ${lb} ; do if fatload usb ${d} 0x1000000 u-boot.ext; then echo "FOUND u-boot.ext on usb ${d}, jumping"; go 0x1000000; fi; done
echo "--- step2: fallback, boot kernel with old u-boot ---"
for d in ${lb} ; do if fatload usb ${d} 0x44000000 uEnv.txt; then echo "uEnv.txt on usb ${d}"; env import -t 0x44000000 ${filesize}; setenv bootargs ${APPEND}; if fatload usb ${d} ${kernel_addr_r} ${LINUX}; then if fatload usb ${d} ${ramdisk_addr_r} ${INITRD}; then if fatload usb ${d} ${fdt_addr_r} ${FDT}; then fdt addr ${fdt_addr_r}; echo "Booting kernel..."; booti ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}; fi; fi; fi; fi; done
echo "=========== all attempts failed ==========="
'''

if __name__ == '__main__':
    print("=" * 70)
    print("校验原始 autoscript 格式（确认我的解析器正确）")
    print("=" * 70)
    verify('/tmp/fn_boot/s905_autoscript')
    verify('/tmp/fn_boot/aml_autoscript')

    print()
    print("=" * 70)
    print("生成端口无关的 s905_autoscript")
    print("=" * 70)
    data = SCRIPT.encode('utf-8')
    img = build_uimage(data, name=b'aml_autoscript')
    out = '/tmp/new_s905_autoscript'
    open(out, 'wb').write(img)
    print(f"  脚本长度 {len(data)} B, uImage 总长 {len(img)} B")
    print()
    verify(out)
    print()
    print("--- 脚本内容 ---")
    print(SCRIPT)

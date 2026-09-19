#!/usr/bin/env python3
"""
检查 FnNAS 内核与 initrd 格式，判断能否做成 eMMC boot 分区用的 Android boot.img
"""
import gzip
import struct
import os

B = '/tmp/fn_boot'

print("=" * 78)
print("1. 内核 zImage / vmlinuz 格式")
print("=" * 78)
for name in ['zImage', 'vmlinuz-6.12.41-trim']:
    p = os.path.join(B, name)
    if not os.path.exists(p):
        print(f"  {name}: 不存在")
        continue
    d = open(p, 'rb').read(4096)
    sz = os.path.getsize(p)
    print(f"\n  {name}  ({sz:,} B)")
    print(f"    头 4 字节: {d[0:4].hex(' ')}")
    print(f"    +0x38 magic: {d[0x38:0x3C]!r}   (arm64 Image 应为 b'ARM\\x64')")
    # ARM64 Image header
    if d[0x38:0x3C] == b'ARM\x64':
        text_off = struct.unpack('<Q', d[8:16])[0]
        img_size = struct.unpack('<Q', d[0x10:0x18])[0]
        print(f"    *** arm64 Image 格式 ***  text_offset=0x{text_off:X}  image_size={img_size:,}")
    # zImage (compressed)
    if d[0:2] == b'\x1f\x8b':
        print("    *** gzip 压缩 ***")
    # uImage
    if d[0:4] == bytes.fromhex('27051956'):
        print("    *** uImage 格式 ***")
    # ARM zImage magic (32-bit)
    if d[0x24:0x28] == b'\x01\x60\x28\x00' or d[0x24:0x28] == bytes.fromhex('18286f01'):
        print("    *** 32-bit ARM zImage ***")

print()
print("=" * 78)
print("2. uInitrd 格式")
print("=" * 78)
for name in ['uInitrd', 'uInitrd-6.12.41-trim', 'initrd.img-6.12.41-trim']:
    p = os.path.join(B, name)
    if not os.path.exists(p):
        print(f"  {name}: 不存在")
        continue
    d = open(p, 'rb').read(128)
    sz = os.path.getsize(p)
    print(f"\n  {name}  ({sz:,} B)")
    print(f"    头 4 字节: {d[0:4].hex(' ')}")
    if d[0:4] == bytes.fromhex('27051956'):
        hcrc, ts, dsize, load, ep, dcrc, o, a, t, c = struct.unpack('>IIIIIIBBBB', d[4:32])
        name32 = d[32:64].split(b'\x00')[0]
        print(f"    *** uImage ***  name={name32!r} size={dsize:,} os={o} arch={a} type={t} comp={c}")
        # 解开看内部
        full = open(p, 'rb').read()
        data = full[64:64+dsize]
        inner = data[:8]
        print(f"    内部数据头: {inner.hex(' ')}")
        if inner[:2] == b'\x1f\x8b':
            print("    内部是 gzip 压缩的 cpio")
            try:
                dec = gzip.decompress(data)
                print(f"    解压后 {len(dec):,} B, magic={dec[:6]!r}")
            except Exception as e:
                print(f"    解压失败: {e}")
    elif d[0:2] == b'\x1f\x8b':
        print("    *** 纯 gzip ***")
    elif d[:6] in (b'070701', b'070702'):
        print("    *** 未压缩 cpio ***")

print()
print("=" * 78)
print("3. eMMC boot 分区容量限制")
print("=" * 78)
print("  4G 包里 boot 分区 = 16,777,216 B (16 MB)")
print(f"  内核 zImage        = {os.path.getsize(os.path.join(B,'zImage')):,} B")
print(f"  uInitrd            = {os.path.getsize(os.path.join(B,'uInitrd')):,} B")
tot = os.path.getsize(os.path.join(B, 'zImage')) + os.path.getsize(os.path.join(B, 'uInitrd'))
print(f"  内核+initrd 合计   = {tot:,} B ({tot/1024/1024:.1f} MB)")
print(f"  ==> 16MB 分区装不下！需要 {tot/1024/1024:.1f} MB")

#!/usr/bin/env python3
"""检查镜像与 U 盘的 rootfs (btrfs) 分区：UUID + 完整性"""
import struct
import sys
import os

IMG = '/mnt/c/Users/Administrator/Downloads/fnnas_amlogic_s905l3a_k6.12.41_2026.06.25.img'


def read_mbr(path):
    with open(path, 'rb') as f:
        f.seek(0x1BE)
        parts = []
        for i in range(4):
            e = f.read(16)
            status, c1, c2, c3, ptype = struct.unpack('<BBBB B'.replace(' ', ''), e[0:5])
            lba, cnt = struct.unpack('<II', e[8:16])
            parts.append(dict(idx=i + 1, type=ptype, lba=lba, sectors=cnt,
                              offset=lba * 512, size=cnt * 512))
        return parts


def btrfs_sb(path, offset):
    """读分区起始 +0x10000 处的 btrfs superblock"""
    with open(path, 'rb') as f:
        f.seek(offset + 0x10000)
        sb = f.read(0x1000)
    if len(sb) < 0x100:
        return None
    magic = sb[0x40:0x48]
    fsid = sb[0x20:0x30]
    bytenr = struct.unpack('<Q', sb[0x30:0x38])[0]
    total = struct.unpack('<Q', sb[0x70:0x78])[0]
    dev_item = sb[0xC0:0x100]

    def fmt_uuid(b):
        h = b.hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    return dict(magic=magic, fsid=fmt_uuid(fsid), raw_fsid=fsid,
                bytenr=bytenr, total=total)


print("=" * 78)
print("镜像分区表")
print("=" * 78)
parts = read_mbr(IMG)
for p in parts:
    if p['sectors']:
        print(f"  分区{p['idx']}  type=0x{p['type']:02X}  LBA={p['lba']:>10,}  "
              f"扇区={p['sectors']:>10,}  {p['size']/1024/1024:>10.1f} MB  "
              f"offset=0x{p['offset']:X}")

print()
print("=" * 78)
print("镜像内各分区 btrfs 探测")
print("=" * 78)
for p in parts:
    if not p['sectors']:
        continue
    sb = btrfs_sb(IMG, p['offset'])
    if sb and sb['magic'] == b'_BHRfS_M':
        print(f"  分区{p['idx']}:  *** btrfs ***  UUID={sb['fsid']}  "
              f"total={sb['total']/1024/1024/1024:.2f} GB")
    else:
        m = sb['magic'] if sb else b''
        print(f"  分区{p['idx']}: 非 btrfs (magic={m!r})")

print()
print("=" * 78)
print("uEnv.txt 里声明的 root UUID : 2c3ab232-463c-4079-a3ce-667ce47ef354")
print("=" * 78)

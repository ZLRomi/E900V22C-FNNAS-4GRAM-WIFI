#!/usr/bin/env python3
"""解析 Amlogic 烧录包 (AML image) 头部结构"""
import struct
import sys

def parse(path):
    with open(path, 'rb') as f:
        head = f.read(0x1000)

    print("=" * 78)
    print("文件:", path)
    print("=" * 78)

    magic = head[0:4]
    print(f"magic        : {magic.hex(' ')}")
    print(f"version      : {struct.unpack('<I', head[4:8])[0]}")
    print(f"@0x08 (magic2): 0x{struct.unpack('<I', head[8:12])[0]:08X}")
    print(f"@0x0C         : 0x{struct.unpack('<I', head[12:16])[0]:08X}")
    for off in (0x10, 0x14, 0x18, 0x1C):
        print(f"@0x{off:02X}         : 0x{struct.unpack('<I', head[off:off+4])[0]:08X}  ({struct.unpack('<I', head[off:off+4])[0]})")
    for off in (0x20, 0x50, 0x58):
        v = struct.unpack('<Q', head[off:off+8])[0]
        print(f"@0x{off:02X} (u64)   : 0x{v:X}  ({v})")

    # 扫描 ASCII 名称串
    print("\n--- 疑似 item 名称 (每 0x100 字节一档) ---")
    for off in range(0x40, 0x1000, 0x100):
        chunk = head[off:off + 0x100]
        name = chunk[0x20:0x60].split(b'\x00')[0]
        if name and all(32 <= c < 127 for c in name):
            vals = struct.unpack('<QQ', chunk[0x00:0x10])
            vals2 = struct.unpack('<QQ', chunk[0x10:0x20])
            print(f"  @0x{off:03X}  name={name.decode():<24} "
                  f"[0x00]={vals[0]:#x},{vals[1]:#x}  [0x10]={vals2[0]:#x},{vals2[1]:#x}")

    # 全区域 ASCII 串
    print("\n--- 头部内所有可打印串 (len>=3) ---")
    cur = b''
    start = 0
    for i, c in enumerate(head):
        if 32 <= c < 127:
            if not cur:
                start = i
            cur += bytes([c])
        else:
            if len(cur) >= 3:
                print(f"  0x{start:04X}: {cur.decode()}")
            cur = b''

if __name__ == '__main__':
    for p in sys.argv[1:]:
        parse(p)

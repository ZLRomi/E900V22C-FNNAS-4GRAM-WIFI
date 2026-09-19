#!/usr/bin/env python3
"""从安卓线刷包的 _aml_dtb 中提取 dtb 并查看内存/机型配置"""
import struct
import gzip
import sys
import os
import subprocess

FDT_MAGIC = b'\xd0\x0d\xfe\xed'


def extract_dtbs(path, outdir):
    raw = open(path, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        try:
            d = gzip.decompress(raw)
        except Exception as e:
            print(f"  gzip 失败: {e}")
            return []
    else:
        d = raw

    os.makedirs(outdir, exist_ok=True)
    found = []
    pos = 0
    idx = 0
    while True:
        p = d.find(FDT_MAGIC, pos)
        if p < 0:
            break
        # 从 magic 往前找 FDT 头起始（magic 就在头起始）
        if p + 4 <= len(d):
            totalsize = struct.unpack('>I', d[p+4:p+8])[0]
            if 0 < totalsize <= len(d) - p:
                blob = d[p:p+totalsize]
                fn = os.path.join(outdir, f'dtb_{idx}.bin')
                open(fn, 'wb').write(blob)
                found.append((fn, p, totalsize))
                idx += 1
                pos = p + totalsize
            else:
                pos = p + 4
        else:
            break
    return found


def show(fn):
    r = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', '-o', '/tmp/_x.dts', fn],
                       capture_output=True)
    if r.returncode != 0:
        return None
    txt = open('/tmp/_x.dts', encoding='utf-8', errors='replace').read()
    out = {}
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith('model = '):
            out['model'] = s.split('=', 1)[1].strip().strip('";')
            break
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith('compatible = ') and 'amlogic' in s:
            out['compat'] = s.split('=', 1)[1].strip().strip('";')
            break
    # memory 节点
    import re
    m = re.search(r'memory@[0-9a-f]*\s*\{[^}]*reg = <([^>]*)>', txt, re.S)
    if m:
        out['mem'] = m.group(1).strip()
    return out


if __name__ == '__main__':
    for pkg, label in [
        (sys.argv[1], '当前包'),
    ]:
        print('=' * 78)
        print(label, os.path.basename(pkg))
        print('=' * 78)
        outdir = sys.argv[2] if len(sys.argv) > 2 else '/tmp/aml_dtb_out'
        found = extract_dtbs(pkg, outdir)
        print(f"  找到 {len(found)} 个 FDT")
        for fn, off, size in found:
            info = show(fn)
            print(f"\n  --- offset=0x{off:X} size={size}")
            if info:
                print(f"      model      : {info.get('model','?')}")
                print(f"      compatible : {info.get('compat','?')}")
                mem = info.get('mem', '')
                if mem:
                    cells = mem.split()
                    print(f"      memory reg : {mem}")
                    try:
                        if len(cells) == 4:
                            hi, lo = int(cells[2], 16), int(cells[3], 16)
                            total = (hi << 32) | lo
                            print(f"      => 内存容量 : {total/1024/1024/1024:.2f} GB")
                    except Exception:
                        pass
            else:
                print("      (非 FDT 或解析失败)")

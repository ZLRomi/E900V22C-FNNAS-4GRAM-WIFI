$ErrorActionPreference = 'Stop'
$DISK = '\\.\PHYSICALDRIVE2'
$IMG  = 'C:\Users\Administrator\Downloads\fnnas_amlogic_s905l3a_k6.12.41_2026.06.25.img'

Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Collections.Generic;
using System.Text;

public static class RawCmp2 {
    public class Bucket {
        public string Name;
        public long Start;
        public long End;
        public long Diffs;
        public long FirstDiff = -1;
    }

    public static string Compare(string diskPath, string imgPath, long length, int chunk,
                                 long[] bounds, string[] names) {
        using (FileStream d = new FileStream(diskPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (FileStream f = new FileStream(imgPath, FileMode.Open, FileAccess.Read, FileShare.Read)) {
            byte[] a = new byte[chunk];
            byte[] b = new byte[chunk];
            int nb = names.Length;
            long[] diffs = new long[nb];
            long[] first = new long[nb];
            for (int i = 0; i < nb; i++) first[i] = -1;

            long pos = 0;
            while (pos < length) {
                int n = (int)Math.Min(chunk, length - pos);
                d.Seek(pos, SeekOrigin.Begin);
                f.Seek(pos, SeekOrigin.Begin);
                int ra = 0, rb = 0;
                while (ra < n) { int k = d.Read(a, ra, n - ra); if (k <= 0) break; ra += k; }
                while (rb < n) { int k = f.Read(b, rb, n - rb); if (k <= 0) break; rb += k; }
                if (ra != rb) return "READLEN|" + ra + "|" + rb + "|" + pos;
                for (int i = 0; i < n; i++) {
                    if (a[i] != b[i]) {
                        long off = pos + i;
                        for (int k = 0; k < nb; k++) {
                            if (off >= bounds[k] && off < bounds[k + 1]) {
                                diffs[k]++;
                                if (first[k] < 0) first[k] = off;
                                break;
                            }
                        }
                    }
                }
                pos += n;
            }

            StringBuilder sb = new StringBuilder();
            for (int k = 0; k < nb; k++) {
                sb.Append(names[k]).Append('|').Append(diffs[k]).Append('|')
                  .Append(first[k] < 0 ? "" : "0x" + first[k].ToString("X")).Append('\n');
            }
            return sb.ToString();
        }
    }
}
"@

$len = (Get-Item $IMG).Length
$b1 = 0x400000L          # BOOT start
$b2 = 0x20400000L        # rootfs start
$bounds = [long[]]@(0, $b1, $b2, $len)
$names  = [string[]]@('A: header/u-boot(0-4MB)', 'B: BOOT/FAT32(4MB-516MB)', 'C: rootfs/btrfs(516MB-end)')

Write-Host "Image length : $len bytes ($([math]::Round($len/1GB,2)) GB)"
Write-Host "Region B: 0x400000 - 0x20400000"
Write-Host "Region C: 0x20400000 - 0x$($len.ToString('X'))"
Write-Host ""
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = [RawCmp2]::Compare($DISK, $IMG, $len, 4194304, $bounds, $names)
$sw.Stop()
Write-Host $r
Write-Host ("elapsed: {0} sec" -f [math]::Round($sw.Elapsed.TotalSeconds,1))

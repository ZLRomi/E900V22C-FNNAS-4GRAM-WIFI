$ErrorActionPreference = 'Stop'
$DISK = '\\.\PHYSICALDRIVE2'
$IMG  = 'C:\Users\Administrator\Downloads\fnnas_amlogic_s905l3a_k6.12.41_2026.06.25.img'

function Fmt-Uuid($b) {
    $h = ($b | ForEach-Object { $_.ToString('x2') }) -join ''
    return "$($h.Substring(0,8))-$($h.Substring(8,4))-$($h.Substring(12,4))-$($h.Substring(16,4))-$($h.Substring(20,12))"
}

function Open-Raw($path) {
    return New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
}

function Read-At($fs, [long]$offset, [int]$len) {
    $buf = New-Object byte[] $len
    [void]$fs.Seek($offset, [System.IO.SeekOrigin]::Begin)
    $got = 0
    while ($got -lt $len) {
        $n = $fs.Read($buf, $got, $len - $got)
        if ($n -le 0) { break }
        $got += $n
    }
    return ,$buf
}

Write-Host "=============================================================="
Write-Host " RAW SECTOR CHECK: $DISK"
Write-Host "=============================================================="

try { $d = Open-Raw $DISK }
catch {
    Write-Host "[FAIL] Cannot open physical disk: $($_.Exception.Message)"
    exit 1
}

$imgFs = Open-Raw $IMG

$mbr    = Read-At $d 0 512
$imgMbr = Read-At $imgFs 0 512
$same = $true
for ($i = 0; $i -lt 512; $i++) { if ($mbr[$i] -ne $imgMbr[$i]) { $same = $false; $firstMb = $i; break } }
Write-Host ""
if ($same) { Write-Host "[MBR] identical to image  [OK]" }
else       { Write-Host "[MBR] DIFFERS at byte $firstMb" }

Write-Host ""
Write-Host "[PARTITIONS]"
$parts = @()
for ($i = 0; $i -lt 4; $i++) {
    $e = 0x1BE + $i * 16
    $ptype = $mbr[$e + 4]
    $lba = [BitConverter]::ToUInt32($mbr, $e + 8)
    $cnt = [BitConverter]::ToUInt32($mbr, $e + 12)
    if ($cnt -gt 0) {
        $off = [long]$lba * 512
        $parts += [pscustomobject]@{ Idx = $i+1; Type = $ptype; Offset = $off; SizeMB = [math]::Round($cnt*512/1MB,1) }
        Write-Host ("  part{0}  type=0x{1:X2}  {2,10} MB  offset=0x{3:X}" -f ($i+1), $ptype, [math]::Round($cnt*512/1MB,1), $off)
    }
}

Write-Host ""
Write-Host "[BTRFS SUPERBLOCK CHECK]"
$p2 = $parts | Where-Object { $_.Type -eq 0x83 } | Select-Object -First 1
if ($p2) {
    foreach ($tag in @('USB','IMG')) {
        if ($tag -eq 'USB') { $sb = Read-At $d ($p2.Offset + 0x10000) 4096 }
        else                { $sb = Read-At $imgFs ($p2.Offset + 0x10000) 4096 }
        $magic = -join ($sb[0x40..0x47] | ForEach-Object { [char]$_ })
        $uuid  = Fmt-Uuid $sb[0x20..0x2F]
        $total = [BitConverter]::ToUInt64($sb, 0x70)
        $ok = ($magic -eq '_BHRfS_M')
        $st = if ($ok) { 'OK' } else { 'BAD!' }
        Write-Host ("  {0,-4} magic={1,-10} {2}  UUID={3}  total={4} GB" -f $tag, $magic, $st, $uuid, [math]::Round($total/1GB,2))
    }
}

Write-Host ""
Write-Host "[FAT32 CHECK]"
$p1 = $parts | Where-Object { $_.Type -eq 0x0C -or $_.Type -eq 0x0B -or $_.Type -eq 0x0E } | Select-Object -First 1
if ($p1) {
    $fb = Read-At $d ($p1.Offset) 512
    $fsType = -join ($fb[0x52..0x56] | ForEach-Object { [char]$_ })
    $oem = -join ($fb[3..10] | ForEach-Object { [char]$_ })
    Write-Host ("  OEM='{0}'  fstype='{1}'" -f $oem, $fsType)
}

Write-Host ""
Write-Host "=============================================================="
Write-Host " FULL COMPARE: USB vs IMAGE"
Write-Host "=============================================================="

Add-Type -TypeDefinition @"
using System;
using System.IO;
public static class RawCmp {
    public static string Compare(string diskPath, string imgPath, long length, int chunk) {
        using (FileStream d = new FileStream(diskPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (FileStream f = new FileStream(imgPath, FileMode.Open, FileAccess.Read, FileShare.Read)) {
            byte[] a = new byte[chunk];
            byte[] b = new byte[chunk];
            long pos = 0;
            long firstDiff = -1;
            long diffs = 0;
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
                        diffs++;
                        if (firstDiff < 0) firstDiff = pos + i;
                    }
                }
                pos += n;
            }
            return "RESULT|" + diffs + "|" + firstDiff;
        }
    }
}
"@

$len = (Get-Item $IMG).Length
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = [RawCmp]::Compare($DISK, $IMG, $len, 4194304)
$sw.Stop()
Write-Host ""
Write-Host ("  {0}   ({1} sec)" -f $r, [math]::Round($sw.Elapsed.TotalSeconds,1))
$parts2 = $r -split '\|'
if ($parts2[0] -eq 'RESULT' -and $parts2[1] -eq '0') {
    Write-Host "  ==> IDENTICAL: USB content matches image byte-for-byte." 
} else {
    Write-Host "  ==> DIFFERENCES FOUND (see above)."
}

$d.Close(); $imgFs.Close()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "   E900V22C  u-boot.ext 方案切换" -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan

# 找 BOOT 分区
$boot = $null
foreach ($v in Get-Volume) {
    if ($v.DriveLetter -and ($v.FileSystemLabel -eq 'BOOT' -or $v.FileSystem -eq 'FAT32')) {
        $p = "$($v.DriveLetter):\"
        if (Test-Path (Join-Path $p 'uEnv.txt')) { $boot = $p; break }
    }
}
if (-not $boot) {
    Write-Host "`n  [错误] 没找到 U 盘的 BOOT 分区，请先插好 U 盘。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}
Write-Host "`n  BOOT 分区: $boot" -ForegroundColor Green

$ext = Join-Path $boot 'u-boot.ext'
Write-Host ""
Write-Host "  请选择方案（建议按顺序 1 -> 3 -> 4 -> 2 逐个试）:" -ForegroundColor Yellow
Write-Host ""
Write-Host "    [1] 不使用 u-boot.ext            <- 老 u-boot 直接 booti 内核（推荐先试）"
Write-Host "    [2] u-boot.ext = e900v22c(2022.04) <- 当前状态，会进循环"
Write-Host "    [3] u-boot.ext = u-boot.usb(2015.01) <- USB 专用版"
Write-Host "    [4] u-boot.ext = u-boot.sd(2015.01)  <- SD 专用版"
Write-Host "    [5] 只查看当前状态"
Write-Host ""
$sel = Read-Host "  输入编号后回车 (默认 1)"
if ([string]::IsNullOrWhiteSpace($sel)) { $sel = '1' }

function Show-Status {
    Write-Host "`n  [当前 BOOT 分区]" -ForegroundColor Yellow
    foreach ($n in @('aml_autoscript','s905_autoscript','emmc_autoscript','uEnv.txt','boot.scr','zImage','uInitrd')) {
        $p = Join-Path $boot $n
        if (Test-Path $p) { Write-Host ("    {0,-22} {1,12:N0} B" -f $n, (Get-Item $p).Length) -ForegroundColor Green }
        else { Write-Host ("    {0,-22} [缺失]" -f $n) -ForegroundColor Red }
    }
    foreach ($n in @('u-boot.ext','u-boot-e900v22c.bin','u-boot.usb','u-boot.sd','u-boot.emmc')) {
        $p = Join-Path $boot $n
        if (Test-Path $p) {
            $h = (Get-FileHash $p -Algorithm MD5).Hash.Substring(0,8)
            Write-Host ("    {0,-22} {1,12:N0} B  md5:{2}" -f $n, (Get-Item $p).Length, $h) -ForegroundColor Gray
        } else {
            Write-Host ("    {0,-22} (不存在)" -f $n) -ForegroundColor DarkGray
        }
    }
    $dtb = Join-Path $boot 'dtb\amlogic\meson-g12a-s905l3a-e900v22c.dtb'
    if (Test-Path $dtb) {
        $b = [IO.File]::ReadAllBytes($dtb)
        $magic = ($b[0..3] | ForEach-Object { $_.ToString('x2') }) -join ' '
        Write-Host ("    dtb                   {0,12:N0} B  魔数 {1}" -f $b.Length, $magic) -ForegroundColor Gray
    }
}

switch ($sel) {
    '1' {
        if (Test-Path $ext) {
            if (-not (Test-Path "$ext.2022bak")) { Copy-Item $ext "$ext.2022bak" -Force }
            Rename-Item $ext "$ext.disabled" -Force
            Write-Host "`n  已禁用 u-boot.ext -> u-boot.ext.disabled" -ForegroundColor Green
            Write-Host "  现在走【老 u-boot 直接 booti 内核】路径。" -ForegroundColor Cyan
        } else {
            Write-Host "`n  u-boot.ext 本来就不存在，已是该状态。" -ForegroundColor Yellow
        }
    }
    '2' {
        $src = Join-Path $boot 'u-boot-e900v22c.bin'
        if (-not (Test-Path $src)) { Write-Host "`n  找不到 u-boot-e900v22c.bin" -ForegroundColor Red; Read-Host; exit 1 }
        Copy-Item $src $ext -Force
        Write-Host "`n  已设置 u-boot.ext = u-boot-e900v22c.bin (2022.04)" -ForegroundColor Green
    }
    '3' {
        $src = Join-Path $boot 'u-boot.usb'
        if (-not (Test-Path $src)) { Write-Host "`n  找不到 u-boot.usb" -ForegroundColor Red; Read-Host; exit 1 }
        Copy-Item $src $ext -Force
        Write-Host "`n  已设置 u-boot.ext = u-boot.usb (2015.01 USB 版)" -ForegroundColor Green
    }
    '4' {
        $src = Join-Path $boot 'u-boot.sd'
        if (-not (Test-Path $src)) { Write-Host "`n  找不到 u-boot.sd" -ForegroundColor Red; Read-Host; exit 1 }
        Copy-Item $src $ext -Force
        Write-Host "`n  已设置 u-boot.ext = u-boot.sd (2015.01 SD 版)" -ForegroundColor Green
    }
    '5' { }
    default { Write-Host "`n  无效编号" -ForegroundColor Red; Read-Host; exit 1 }
}

Show-Status

# uEnv 换行检查
$u = Join-Path $boot 'uEnv.txt'
if (Test-Path $u) {
    $raw = [IO.File]::ReadAllBytes($u)
    $crlf = 0
    for ($i = 0; $i -lt $raw.Length - 1; $i++) { if ($raw[$i] -eq 13 -and $raw[$i+1] -eq 10) { $crlf++ } }
    if ($crlf -eq 0) { Write-Host "`n  uEnv.txt 换行: 纯 LF  [OK]" -ForegroundColor Green }
    else { Write-Host "`n  uEnv.txt 换行: 含 $crlf 个 CRLF  [警告，需转 Unix 换行]" -ForegroundColor Red }
}

Write-Host "`n  完成。安全弹出 U 盘 -> 插盒子 JB01 -> 上电。" -ForegroundColor Cyan
Write-Host ""
Read-Host "按回车退出"

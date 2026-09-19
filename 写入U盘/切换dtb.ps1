$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$varDir = Join-Path (Split-Path -Parent $here) 'dtb-variants'

Write-Host ""
Write-Host "  ============================================================"
Write-Host "   E900V22C  内存配置 dtb 切换工具"
Write-Host "  ============================================================" -ForegroundColor Cyan

# --- 找 BOOT 分区 ---
$boot = $null
foreach ($v in Get-Volume) {
    if ($v.DriveLetter -and ($v.FileSystemLabel -eq 'BOOT' -or $v.FileSystem -eq 'FAT32')) {
        $p = "$($v.DriveLetter):\"
        if (Test-Path (Join-Path $p 'uEnv.txt')) { $boot = $p; break }
    }
}
if (-not $boot) {
    Write-Host "`n  [错误] 没找到 U 盘的 BOOT 分区。请先插好 U 盘。" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}
Write-Host "`n  BOOT 分区: $boot" -ForegroundColor Green

# --- 列出可选 dtb ---
$files = Get-ChildItem $varDir -Filter '*.dtb' | Sort-Object Name
if ($files.Count -eq 0) {
    Write-Host "`n  [错误] dtb-variants 目录里没有 dtb 文件" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

$desc = @{
    'dtba-stock-2g.dtb'   = '原版 2GB        <- 最保险，先测这个'
    'dtbb-4g-usable.dtb'  = '4GB 可用3.83GB   <- 对齐安卓4G dtb，推荐'
    'dtbc-4g-full.dtb'    = '4GB 完整         <- 之前用的，疑似崩溃源'
    'dtbd-4g-reserve.dtb' = '4GB完整+保留区   <- 备选'
}

Write-Host "`n  可选 dtb:" -ForegroundColor Yellow
for ($i = 0; $i -lt $files.Count; $i++) {
    $d = $desc[$files[$i].Name]
    if (-not $d) { $d = '' }
    Write-Host ("    [{0}] {1,-24} {2}" -f ($i+1), $files[$i].Name, $d)
}
Write-Host ""
$sel = Read-Host "  输入编号后回车 (默认 1)"
if ([string]::IsNullOrWhiteSpace($sel)) { $sel = '1' }
$idx = [int]$sel - 1
if ($idx -lt 0 -or $idx -ge $files.Count) { Write-Host "  无效编号" -ForegroundColor Red; Read-Host; exit 1 }
$chosen = $files[$idx]

# --- 目标 dtb 路径 ---
$dtbDir = Join-Path $boot 'dtb\amlogic'
if (-not (Test-Path $dtbDir)) { New-Item -ItemType Directory -Force -Path $dtbDir | Out-Null }
$target = Join-Path $dtbDir 'meson-g12a-s905l3a-e900v22c.dtb'

# --- 备份当前的 ---
if (Test-Path $target) {
    $bak = "$target.bak"
    if (-not (Test-Path $bak)) {
        Copy-Item $target $bak -Force
        Write-Host "`n  已备份当前 dtb -> meson-g12a-s905l3a-e900v22c.dtb.bak" -ForegroundColor Gray
    }
}

# --- 覆盖 ---
Copy-Item $chosen.FullName $target -Force
Write-Host "`n  已部署: $($chosen.Name)" -ForegroundColor Green
Write-Host "  -> $target" -ForegroundColor Gray

# --- 自检 ---
Write-Host "`n  [自检]" -ForegroundColor Yellow
$b = [IO.File]::ReadAllBytes($target)
$magic = ($b[0..3] | ForEach-Object { $_.ToString('x2') }) -join ' '
if ($magic -eq 'd0 0d fe ed') {
    Write-Host "    dtb 魔数 $magic  [OK]   大小 $($b.Length) B" -ForegroundColor Green
} else {
    Write-Host "    dtb 魔数 $magic  [异常!]" -ForegroundColor Red
}

$u = Join-Path $boot 'uEnv.txt'
if (Test-Path $u) {
    $raw = [IO.File]::ReadAllBytes($u)
    $crlf = 0
    for ($i = 0; $i -lt $raw.Length - 1; $i++) { if ($raw[$i] -eq 13 -and $raw[$i+1] -eq 10) { $crlf++ } }
    if ($crlf -eq 0) { Write-Host "    uEnv.txt 纯 LF  [OK]" -ForegroundColor Green }
    else { Write-Host "    uEnv.txt 含 $crlf 个 CRLF  [警告]" -ForegroundColor Red }
}

foreach ($n in @('aml_autoscript','s905_autoscript','u-boot.ext','zImage','uInitrd')) {
    $p = Join-Path $boot $n
    $st = if (Test-Path $p) { 'OK' } else { '缺失' }
    $col = if (Test-Path $p) { 'Green' } else { 'Red' }
    Write-Host ("    {0,-20} {1}" -f $n, $st) -ForegroundColor $col
}

Write-Host "`n  完成。安全弹出 U 盘 -> 插盒子(JB01) -> 上电。" -ForegroundColor Cyan
Write-Host ""
Read-Host "按回车退出"

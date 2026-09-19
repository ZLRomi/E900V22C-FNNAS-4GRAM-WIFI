# =====================================================================
#  给飞牛 U 盘补上缺失的 u-boot.ext
#  原因：镜像里只有 u-boot-e900v22c.bin，但 s905_autoscript 找的是
#        u-boot.ext，缺这个文件 => 老 u-boot 无法链式加载 mainline U-Boot
# =====================================================================
$ErrorActionPreference = 'Stop'
Write-Host ""
Write-Host "  飞牛 U 盘 引导补丁工具" -ForegroundColor Cyan
Write-Host "  ================================" -ForegroundColor Cyan

# --- 1. 找 BOOT 分区 ---
Write-Host "`n[1/3] 查找 U 盘的 BOOT 分区 (FAT32)..." -ForegroundColor Yellow
$cands = @()
foreach ($v in Get-Volume) {
    if ($v.DriveLetter -and ($v.FileSystem -eq 'FAT32' -or $v.FileSystemLabel -eq 'BOOT')) {
        $cands += $v
    }
}
if ($cands.Count -eq 0) {
    Write-Host "  没找到 FAT32 分区。请确认 U 盘已插好、且没被格式化。" -ForegroundColor Red
    Write-Host "  若 U 盘在磁盘管理里没有盘符，先手动分配一个盘符再运行。" -ForegroundColor Red
    Read-Host "`n按回车退出"
    exit 1
}

$boot = $null
foreach ($c in $cands) {
    $drive = "$($c.DriveLetter):\"
    if (Test-Path (Join-Path $drive 'aml_autoscript')) { $boot = $c; break }
}
if (-not $boot) { $boot = $cands[0] }
$root = "$($boot.DriveLetter):\"
Write-Host "  使用分区: $root  (卷标: $($boot.FileSystemLabel), $([math]::Round($boot.Size/1MB)) MB)" -ForegroundColor Green

# --- 2. 复制 u-boot.ext ---
Write-Host "`n[2/3] 补写 u-boot.ext ..." -ForegroundColor Yellow
$src = Join-Path $root 'u-boot-e900v22c.bin'
$dst = Join-Path $root 'u-boot.ext'

if (-not (Test-Path $src)) {
    Write-Host "  找不到源文件 u-boot-e900v22c.bin，无法生成 u-boot.ext" -ForegroundColor Red
    Read-Host "`n按回车退出"
    exit 1
}
Copy-Item $src $dst -Force
$len = (Get-Item $dst).Length
Write-Host "  已生成: u-boot.ext  ($len 字节)" -ForegroundColor Green

# --- 3. 自检 ---
Write-Host "`n[3/3] 引导文件自检" -ForegroundColor Yellow
$need = @(
    @{n='aml_autoscript';  d='老 u-boot 自动脚本入口'},
    @{n='s905_autoscript'; d='U盘/SD 启动脚本'},
    @{n='u-boot.ext';      d='mainline U-Boot (链式加载)'},
    @{n='uEnv.txt';        d='启动参数(LINUX/INITRD/FDT/APPEND)'},
    @{n='boot.scr';        d='mainline U-Boot 引导脚本'},
    @{n='zImage';          d='内核'},
    @{n='uInitrd';         d='initramfs'}
)
$allOk = $true
foreach ($f in $need) {
    $p = Join-Path $root $f.n
    if (Test-Path $p) {
        Write-Host ("  [OK]   {0,-16} {1,12:N0} B   {2}" -f $f.n, (Get-Item $p).Length, $f.d) -ForegroundColor Green
    } else {
        Write-Host ("  [缺失] {0,-16} {1}" -f $f.n, $f.d) -ForegroundColor Red
        $allOk = $false
    }
}

$dtb = Join-Path $root 'dtb\amlogic\meson-g12a-s905l3a-e900v22c.dtb'
if (Test-Path $dtb) {
    $b = [IO.File]::ReadAllBytes($dtb)
    $magic = ($b[0..3] | ForEach-Object { $_.ToString('x2') }) -join ' '
    $ok = ($magic -eq 'd0 0d fe ed')
    Write-Host ("  [{0}] dtb\amlogic\meson-g12a-s905l3a-e900v22c.dtb  {1:N0} B  魔数={2}" -f $(if($ok){'OK'}else{'异常'}), $b.Length, $magic) -ForegroundColor $(if($ok){'Green'}else{'Red'})
}

# uEnv.txt 换行符
$u = Join-Path $root 'uEnv.txt'
if (Test-Path $u) {
    $raw = [IO.File]::ReadAllBytes($u)
    $crlf = 0
    for ($i = 0; $i -lt $raw.Length - 1; $i++) { if ($raw[$i] -eq 13 -and $raw[$i+1] -eq 10) { $crlf++ } }
    if ($crlf -eq 0) {
        Write-Host "  [OK]   uEnv.txt 为纯 LF 换行" -ForegroundColor Green
    } else {
        Write-Host "  [警告] uEnv.txt 含 $crlf 个 CRLF，u-boot 的 env import 可能失败！请用 Notepad++ 转成 Unix(LF)" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
if ($allOk) {
    Write-Host "  完成。现在可以：安全弹出 U 盘 -> 插盒子(靠网口的口) -> 上电。" -ForegroundColor Cyan
} else {
    Write-Host "  有项目未通过，见上面的红色项。" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "按回车退出"

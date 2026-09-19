#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot

function Write-Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

Write-Host "E900V22C 4G dtb 部署到 U 盘" -ForegroundColor Green
Write-Host "源目录: $src"

# ---------- 1. 定位 U 盘 BOOT 分区 ----------
Write-Section "1. 查找 U 盘 BOOT 分区"

$candidates = Get-Volume | Where-Object {
    $_.DriveType -eq 'Removable' -and $_.FileSystem -eq 'FAT32' -and $_.DriveLetter
}

$boot = $null
foreach ($v in $candidates) {
    $root = "$($v.DriveLetter):\"
    if (Test-Path "$root/uEnv.txt" -PathType Leaf) { $boot = $root; break }
    if (Test-Path "$root/boot/uEnv.txt" -PathType Leaf) { $boot = "$root/boot"; break }
}

if (-not $boot) {
    Write-Host "没找到 BOOT 分区。" -ForegroundColor Red
    Write-Host "已检测到的可移动 FAT32 卷:"
    if ($candidates) {
        $candidates | ForEach-Object { Write-Host ("   {0}:  标签={1}  {2}  剩余{3}MB" -f $_.DriveLetter, $_.FileSystemLabel, $_.FileSystem, [math]::Round($_.SizeRemaining/1MB)) }
    } else {
        Write-Host "   (无)"
    }
    Write-Host @"

可能原因:
  1. balenaEtcher 写完后弹出的“需要格式化”对话框 -> 必须点【取消】
  2. BOOT 分区没有盘符 -> Win+X 磁盘管理, 找到 511MB 的 FAT32 分区, 右键“更改驱动器号和路径”->添加
  3. U 盘没插好, 或 Windows 没识别
"@ -ForegroundColor Yellow
    exit 1
}

Write-Host "找到 BOOT 分区: $boot" -ForegroundColor Green
Write-Host "  剩余空间: $([math]::Round((Get-Volume -FilePath $boot).SizeRemaining/1MB)) MB"

$dtbDir = Join-Path $boot 'dtb/amlogic'
if (-not (Test-Path $dtbDir)) {
    Write-Host "目录不存在，创建: $dtbDir" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $dtbDir -Force | Out-Null
}

# ---------- 2. 备份并部署 ----------
Write-Section "2. 部署 dtb"

$target = Join-Path $dtbDir 'meson-g12a-s905l3a-e900v22c.dtb'

if (Test-Path $target) {
    $bak = "$target.bak"
    if (-not (Test-Path $bak)) {
        Copy-Item $target $bak -Force
        Write-Host "原文件已备份 -> $(Split-Path $bak -Leaf)"
    } else {
        Write-Host "备份已存在，跳过: $(Split-Path $bak -Leaf)"
    }
}

Copy-Item (Join-Path $src 'meson-g12a-s905l3a-e900v22c.dtb') $target -Force
Write-Host "已覆盖: meson-g12a-s905l3a-e900v22c.dtb  (4G + WiFi)" -ForegroundColor Green

# 备用文件一起放进去
Copy-Item (Join-Path $src 'meson-g12a-s905l3a-e900v22c-4g-wifi.dtb') $dtbDir -Force
Copy-Item (Join-Path $src 'meson-g12a-s905l3a-e900v22c-4g.dtb') $dtbDir -Force
Write-Host "已放入备用: -4g-wifi.dtb, -4g.dtb"

# ---------- 3. 自检 ----------
Write-Section "3. 自检"

# 3.1 dtb 魔数
$bytes = [System.IO.File]::ReadAllBytes($target)
$magic = ($bytes[0..3] | ForEach-Object { $_.ToString('x2') }) -join ' '
if ($magic -eq 'd0 0d fe ed') {
    Write-Host "[OK] dtb 魔数正确: $magic" -ForegroundColor Green
} else {
    Write-Host "[FAIL] dtb 魔数错误: $magic" -ForegroundColor Red
}
Write-Host "     文件大小: $($bytes.Length) 字节"

# 3.2 uEnv.txt
$uenv = Join-Path $boot 'uEnv.txt'
if (Test-Path $uenv) {
    $raw = [System.IO.File]::ReadAllBytes($uenv)
    $crlf = (0..($raw.Length-2) | Where-Object { $raw[$_] -eq 13 -and $raw[$_+1] -eq 10 }).Count
    $lf   = (0..($raw.Length-1) | Where-Object { $raw[$_] -eq 10 }).Count

    Write-Host "`n--- uEnv.txt ---"
    Get-Content $uenv | ForEach-Object { Write-Host "   $_" }
    Write-Host "   换行: CRLF=$crlf  LF总行=$lf"

    if ($crlf -gt 0) {
        Write-Host "   [警告] 存在 CRLF 换行，u-boot 可能解析失败" -ForegroundColor Red
    } else {
        Write-Host "   [OK] 纯 LF 换行" -ForegroundColor Green
    }

    $fdt = (Get-Content $uenv | Where-Object { $_ -match '^\s*FDT=' }) -join ''
    if ($fdt -match 'meson-g12a-s905l3a-e900v22c\.dtb') {
        Write-Host "   [OK] FDT 指向被覆盖的同名文件，无需再改 uEnv.txt" -ForegroundColor Green
    } else {
        Write-Host "   [注意] FDT = $fdt" -ForegroundColor Yellow
    }
} else {
    Write-Host "[注意] BOOT 根目录没有 uEnv.txt（可能用 extlinux 引导）" -ForegroundColor Yellow
}

# 3.3 列出 dtb 目录
Write-Host "`n--- $dtbDir 中的 e900v22c 相关文件 ---"
Get-ChildItem $dtbDir -Filter '*e900v22c*' | ForEach-Object {
    Write-Host ("   {0,-52} {1,8} 字节  {2}" -f $_.Name, $_.Length, $_.LastWriteTime.ToString('MM-dd HH:mm'))
}

# ---------- 4. 收尾 ----------
Write-Section "完成"
Write-Host @"
下一步:
  1. 右键开始菜单 -> 磁盘管理 或 任务栏“安全删除硬件”弹出 U 盘（不要直接拔）
  2. 盒子断电, 插 U 盘到【靠近网口的 USB 口】
  3. 若盒子已刷过安卓底包且开了 ADB:
         adb connect 盒子IP
         adb shell reboot update
     否则: 断电 -> 按住 AV 孔复位键 -> 插电 -> 约 10 秒松手
  4. 起来后 SSH 登录, 执行验证:
         free -h
         ls /sys/bus/sdio/devices/
"@ -ForegroundColor Green

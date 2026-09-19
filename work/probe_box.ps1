$ip = '10.10.10.109'
Write-Host "=================== 探测 $ip ==================="
Write-Host ""

# 反向 DNS
Write-Host "[1] 反向解析:"
try {
    $h = [System.Net.Dns]::GetHostEntry($ip)
    Write-Host "    HostName : $($h.HostName)"
    foreach ($a in $h.Aliases) { Write-Host "    Alias    : $a" }
} catch { Write-Host "    (无)" }

# 端口
Write-Host ""
Write-Host "[2] 端口扫描:"
$ports = @(22, 80, 443, 5666, 5000, 8000, 8080, 9090, 32400, 5000, 8006)
foreach ($p in $ports) {
    $c = New-Object System.Net.Sockets.TcpClient
    $ok = $false
    try {
        $iar = $c.BeginConnect($ip, $p, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(700, $false) -and $c.Connected
    } catch {} finally { $c.Close() }
    Write-Host ("    {0,-6} {1}" -f $p, $(if ($ok) { 'OPEN  <<<' } else { 'closed' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Gray' })
}

# HTTP 探测
Write-Host ""
Write-Host "[3] HTTP 探测:"
foreach ($p in @(5666, 80, 8000, 5000)) {
    try {
        $r = Invoke-WebRequest -Uri "http://${ip}:${p}/" -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop
        Write-Host "    http://${ip}:${p}/  ->  HTTP $($r.StatusCode)  $($r.Headers['Server'])" -ForegroundColor Green
        $t = $r.Content
        if ($t.Length -gt 200) { $t = $t.Substring(0,200) }
        Write-Host "       $($t -replace '\s+',' ')"
    } catch {
        Write-Host "    http://${ip}:${p}/  ->  $($_.Exception.Message.Split([char]10)[0])" -ForegroundColor DarkGray
    }
}

# SSH banner
Write-Host ""
Write-Host "[4] SSH banner (端口22):"
try {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect($ip, 22)
    $s = $c.GetStream()
    $s.ReadTimeout = 3000
    $buf = New-Object byte[] 256
    $n = $s.Read($buf, 0, 256)
    Write-Host "    $(-join ($buf[0..($n-1)] | ForEach-Object {[char]$_}))"
    $c.Close()
} catch { Write-Host "    连接失败: $($_.Exception.Message.Split([char]10)[0])" }

Write-Host ""
Write-Host "=================== 完成 ==================="

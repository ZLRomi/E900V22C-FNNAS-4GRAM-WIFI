# 扫描 10.10.10.0/24：存活主机 + 关键端口
$net = '10.10.10.'
Write-Host "`n[1] Ping 扫描 10.10.10.1-254 ..." -ForegroundColor Cyan
$tasks = @{}
1..254 | ForEach-Object {
    $ip = "$net$_"
    $p = New-Object System.Net.NetworkInformation.Ping
    $tasks[$ip] = $p.SendPingAsync($ip, 600)
}
Start-Sleep -Milliseconds 1500
$alive = @()
foreach ($ip in $tasks.Keys) {
    try {
        $r = $tasks[$ip].Result
        if ($r.Status -eq 'Success') { $alive += $ip }
    } catch {}
}
$alive = $alive | Sort-Object { [int]($_ -split '\.')[3] }
Write-Host "  存活: $($alive -join ', ')" -ForegroundColor Green

Write-Host "`n[2] 端口扫描 (22/5666/80/443/8000/8080) ..." -ForegroundColor Cyan
$ports = @(22, 5666, 80, 443, 8000, 8080)
foreach ($ip in $alive) {
    $open = @()
    foreach ($port in $ports) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $iar = $c.BeginConnect($ip, $port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(350, $false) -and $c.Connected) {
                $open += $port
            }
        } catch {} finally { $c.Close() }
    }
    if ($open.Count -gt 0) {
        Write-Host ("  {0,-16} 开放端口: {1}" -f $ip, ($open -join ', ')) -ForegroundColor Yellow
    } else {
        Write-Host ("  {0,-16} (无)" -f $ip)
    }
}

Write-Host "`n[3] ARP 表 ..." -ForegroundColor Cyan
Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like '10.10.10.*' -and $_.State -ne 'Unreachable' } |
    Select-Object IPAddress, LinkLayerAddress, State |
    Sort-Object { [int]($_.IPAddress -split '\.')[3] } |
    Format-Table -AutoSize | Out-String -Width 100 | Write-Host

$net = '10.10.10.'
Write-Host "Ping sweep 10.10.10.1-254 ..."
$tasks = @{}
1..254 | ForEach-Object {
    $ip = "$net$_"
    $p = New-Object System.Net.NetworkInformation.Ping
    $tasks[$ip] = $p.SendPingAsync($ip, 800)
}
Start-Sleep -Milliseconds 2000
$alive = @()
foreach ($ip in $tasks.Keys) {
    try { if ($tasks[$ip].Result.Status -eq 'Success') { $alive += $ip } } catch {}
}
$alive = $alive | Sort-Object { [int]($_ -split '\.')[3] }
Write-Host ("ALIVE: " + ($alive -join ', '))
Write-Host ""
Write-Host "Port 5666 / 22 check:"
foreach ($ip in $alive) {
    $open = @()
    foreach ($port in @(5666, 22, 80)) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $iar = $c.BeginConnect($ip, $port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(500, $false) -and $c.Connected) { $open += $port }
        } catch {} finally { $c.Close() }
    }
    if ($open.Count -gt 0) {
        Write-Host ("  {0,-16} open: {1}" -f $ip, ($open -join ',')) -ForegroundColor Green
    }
}
Write-Host ""
Write-Host "ARP:"
Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like '10.10.10.*' -and $_.State -ne 'Unreachable' } |
    Select-Object IPAddress, LinkLayerAddress, State |
    Sort-Object { [int]($_.IPAddress -split '\.')[3] } |
    Format-Table -AutoSize | Out-String -Width 100 | Write-Host

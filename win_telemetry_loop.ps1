$targetUrl = "http://100.80.126.45:8888/api/win_status"

while ($true) {
    try {
        $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
        if ($null -eq $cpu) { $cpu = 0 }
        
        $os = Get-CimInstance Win32_OperatingSystem
        $totalRamMb = [math]::Round($os.TotalVisibleMemorySize / 1024, 1)
        $freeRamMb = [math]::Round($os.FreePhysicalMemory / 1024, 1)
        $usedRamMb = [math]::Round($totalRamMb - $freeRamMb, 1)
        $ramPct = [math]::Round(($usedRamMb / $totalRamMb) * 100, 1)
        
        $c = Get-PSDrive C
        $cTotal = [math]::Round(($c.Used + $c.Free) / 1GB, 1)
        $cFree = [math]::Round($c.Free / 1GB, 1)
        $cPct = [math]::Round((($cTotal - $cFree) / $cTotal) * 100, 1)

        $d = Get-PSDrive D
        $dTotal = [math]::Round(($d.Used + $d.Free) / 1GB, 1)
        $dFree = [math]::Round($d.Free / 1GB, 1)
        $dPct = [math]::Round((($dTotal - $dFree) / $dTotal) * 100, 1)
        
        $uptime = (Get-Date) - $os.LastBootUpTime
        $uptimeStr = "{0}d {1}h {2}m" -f $uptime.Days, $uptime.Hours, $uptime.Minutes

        $procList = [System.Collections.Generic.List[object]]::new()
        Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object -First 8 ProcessName, MainWindowTitle | ForEach-Object {
            $procList.Add(@{ name = $_.ProcessName; title = $_.MainWindowTitle })
        }

        $payloadObj = @{
            hostname = $env:COMPUTERNAME
            os = "Windows 11 IoT Enterprise"
            cpu_pct = $cpu
            ram = @{
                total_mb = $totalRamMb
                used_mb = $usedRamMb
                free_mb = $freeRamMb
                pct = $ramPct
            }
            disks = @(
                @{ drive = "C:"; total_gb = $cTotal; free_gb = $cFree; used_pct = $cPct },
                @{ drive = "D:"; total_gb = $dTotal; free_gb = $dFree; used_pct = $dPct }
            )
            uptime = $uptimeStr
            windows = $procList
        }

        $payloadJson = $payloadObj | ConvertTo-Json -Depth 5 -Compress
        $null = Invoke-RestMethod -Uri $targetUrl -Method Post -Body $payloadJson -ContentType "application/json; charset=utf-8" -TimeoutSec 3
    } catch {
        # ignore transient error
    }
    Start-Sleep -Seconds 2
}

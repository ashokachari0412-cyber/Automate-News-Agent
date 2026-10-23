# Watchdog that keeps Alfred reachable from the phone at https://desktop-tvjj8s1.tailc3cb6c.ts.net
# Every minute it checks, and repairs:
#   1. local server on :8000      -> starts the "AlfredAI" scheduled task
#   2. Tailscale connected        -> tailscale up
#   3. Funnel on for :8000        -> tailscale funnel --bg 8000
#   4. public DNS record exists   -> tailscale funnel reset + --bg 8000 (Tailscale sometimes drops it)
# Installed as the "AlfredKeepAlive" scheduled task by install_keep_alive.ps1. Log: artifacts\keepalive.log
$project = $PSScriptRoot
$ts      = "C:\Program Files\Tailscale\tailscale.exe"
$log     = Join-Path $project "artifacts\keepalive.log"
$port    = 8000
$republishCooldown = 600   # seconds to wait after a republish (DNS takes ~3-5 min to appear)
$lastRepublish     = [datetime]::MinValue
$dnsMisses         = 0

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg
    Add-Content -Path $log -Value $line
    if ((Get-Item $log).Length -gt 1MB) { Get-Content $log -Tail 500 | Set-Content $log }
}

function Get-PublicHost {
    try { return ((& $ts status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".") } catch { return $null }
}

function Server-Up {
    try {
        Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 10 | Out-Null
        return $true
    } catch {
        # 401 (password protected) still means the server is up
        return [bool]($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -lt 500)
    }
}

function Publish-Funnel([bool]$reset) {
    if ($reset) { & $ts funnel reset 2>&1 | Out-Null }
    & $ts funnel --bg $port 2>&1 | Out-Null
    $script:lastRepublish = Get-Date
}

Log "Keep-alive started"
Publish-Funnel $false

while ($true) {
    try {
        # 1. Server
        if (-not (Server-Up)) {
            Log "Server not answering on :$port - starting AlfredAI task"
            Start-ScheduledTask -TaskName "AlfredAI" -ErrorAction SilentlyContinue
        }

        # 2. Tailscale connection
        $state = try { (& $ts status --json | ConvertFrom-Json).BackendState } catch { "Unknown" }
        if ($state -ne "Running") {
            Log "Tailscale state '$state' - reconnecting"
            & $ts up 2>&1 | Out-Null
        }

        # 3. Funnel configured
        $funnel = (& $ts funnel status 2>&1) -join "`n"
        if ($funnel -notmatch "Funnel on") {
            Log "Funnel was off - turning it on"
            Publish-Funnel $false
        }

        # 4. Public DNS record (what the phone actually needs)
        $hostName = Get-PublicHost
        $resolves = $false
        if ($hostName) {
            # Ask Google's public DNS over HTTPS: Windows routes normal *.ts.net lookups to Tailscale's
            # private MagicDNS (even with -Server 8.8.8.8), which would hide a missing public record.
            try {
                $r = Invoke-RestMethod "https://dns.google/resolve?name=$hostName&type=AAAA" -TimeoutSec 15
                $resolves = ($r.Status -eq 0) -and [bool]$r.Answer
            } catch { $resolves = $true }  # can't reach Google DNS: PC offline, not a Funnel problem
        }
        if ($resolves) {
            $dnsMisses = 0
        } else {
            $dnsMisses++
            $sinceRepublish = ((Get-Date) - $lastRepublish).TotalSeconds
            if ($dnsMisses -ge 2 -and $sinceRepublish -gt $republishCooldown) {
                Log "Public DNS for $hostName missing ($dnsMisses checks) - republishing Funnel"
                Publish-Funnel $true
            }
        }
    } catch {
        Log "Watchdog error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 60
}

# Keeps a Cloudflare quick tunnel to http://localhost:8000 alive forever.
# Quick tunnels expire / drop ("Tunnel not found"), so cloudflared is restarted whenever it exits
# or stops being reachable. The current public URL is written to artifacts\tunnel_url.txt.
$project = 'D:\Projects\Automate-News-Agent'
$cf      = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
if (-not (Test-Path $cf)) { $cf = (Get-ChildItem 'C:\Program Files*\cloudflared\cloudflared.exe' | Select-Object -First 1).FullName }
$log     = "$project\artifacts\tunnel.log"
$urlFile = "$project\artifacts\tunnel_url.txt"

while ($true) {
    Remove-Item $log -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $cf -ArgumentList 'tunnel', '--url', 'http://localhost:8000', '--no-autoupdate', '--logfile', $log `
         -WindowStyle Hidden -PassThru

    # Wait for the URL to appear in the log
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url -and -not $p.HasExited; $i++) {
        Start-Sleep -Seconds 2
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value; Set-Content $urlFile $url }
    }

    # Health check every minute; restart if cloudflared exits or the tunnel stops answering
    $fails = 0
    while ($url -and -not $p.HasExited -and $fails -lt 3) {
        Start-Sleep -Seconds 60
        try {
            Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 20 | Out-Null
            $fails = 0
        } catch {
            # 401 means the tunnel works (the app wants the password)
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { $fails = 0 } else { $fails++ }
        }
    }

    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 5
}

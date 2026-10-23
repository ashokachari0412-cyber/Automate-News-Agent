# Installs the Alfred keep-alive watchdog (keep_alive.ps1) as a hidden scheduled task that starts at
# logon and restarts itself if it ever stops. Also stops the PC from sleeping while plugged in.
# Remove with:  Unregister-ScheduledTask -TaskName "AlfredKeepAlive" -Confirm:$false
$project = $PSScriptRoot

$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$project\keep_alive.ps1`"" `
            -WorkingDirectory $project
$triggers = @(New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName "AlfredKeepAlive" -Action $action -Trigger $triggers -Settings $settings `
    -Description "Keeps Alfred's server, Tailscale Funnel and public address alive" -Force | Out-Null
Start-ScheduledTask -TaskName "AlfredKeepAlive"

# A sleeping PC takes the server offline: never sleep while plugged in (battery behaviour unchanged).
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Write-Host "Alfred keep-alive is running. Log: $project\artifacts\keepalive.log"

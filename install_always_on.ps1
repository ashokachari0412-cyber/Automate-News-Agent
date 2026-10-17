# Runs Alfred AI as a hidden background task: starts at logon, keeps running when the
# PC is locked, and restarts automatically if it crashes. Logs: artifacts\server.log
# Remove with:  Unregister-ScheduledTask -TaskName "AlfredAI" -Confirm:$false
$project = $PSScriptRoot
$pythonw = (Get-Command pythonw).Source

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "-m src.cli serve --host 0.0.0.0 --port 8000 --no-reload" -WorkingDirectory $project
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName "AlfredAI" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Alfred AI web app (http://localhost:8000)" -Force | Out-Null
Start-ScheduledTask -TaskName "AlfredAI"
Write-Host "Alfred AI is running in the background at http://localhost:8000"

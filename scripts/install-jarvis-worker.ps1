# Install/Update JARVIS Hermes Link Hub Worker as a Windows Scheduled Task
# Run this from PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\Users\zeroc\hermes-link\scripts\install-jarvis-worker.ps1

$TaskName = "JARVIS-HubWorker"
$HermesLinkDir = "$env:USERPROFILE\hermes-link"
$HomeDir = "$env:USERPROFILE\.hermes\link"
$Python = "py"
$WorkerCmd = "cd $HermesLinkDir && $Python -m hermes_link --home $HomeDir worker --poll-interval 2"

# Create the task action
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $WorkerCmd"

# Trigger: at startup
$Trigger = New-ScheduledTaskTrigger -AtStartup

# Settings: restart on failure, run whether user is logged on or not
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

# Principal: current user
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest

# Register the task
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force

Write-Host "Scheduled task '$TaskName' installed. It will run the hub worker at startup."
Write-Host "To start immediately: Start-ScheduledTask -TaskName '$TaskName'"

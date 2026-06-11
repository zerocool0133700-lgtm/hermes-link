# Durable Hermes Link receiver on Windows

This guide replaces a foreground PowerShell receiver with a user-level Windows Scheduled Task. It keeps Hermes Link running after the terminal window closes and restarts it if the process exits.

Use this for a Windows receiver started like:

```powershell
py -m hermes_link --home "$env:USERPROFILE\.hermes\link" serve --host 192.168.1.142 --port 8765
```

## Recommended approach

Use Task Scheduler first. It is built into Windows, runs under the normal user account, does not require a third-party service wrapper, and can restart the receiver on failure.

This recipe starts at user logon. If the receiver must run before user logon, use Task Scheduler's "Run whether user is logged on or not" option, but that requires storing user credentials in Windows.

## 1. Create the receiver wrapper script

Run this in PowerShell on the Windows box. Adjust `$BindHost` if the Windows LAN IP changes.

```powershell
$LinkHome = Join-Path $env:USERPROFILE ".hermes\link"
$Script = Join-Path $LinkHome "run-hermes-link-receiver.ps1"
New-Item -ItemType Directory -Force -Path $LinkHome | Out-Null

@'
$ErrorActionPreference = "Continue"

$LinkHome = Join-Path $env:USERPROFILE ".hermes\link"
$BindHost = "192.168.1.142"
$Port = 8765
$LogDir = Join-Path $LinkHome "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "receiver-$Stamp.log"

"[$(Get-Date -Format o)] Starting Hermes Link receiver" | Tee-Object -FilePath $LogFile -Append
"Home: $LinkHome" | Tee-Object -FilePath $LogFile -Append
"Bind: $BindHost`:$Port" | Tee-Object -FilePath $LogFile -Append

& py -m hermes_link --home "$LinkHome" serve --host $BindHost --port $Port *>> $LogFile

$Code = $LASTEXITCODE
"[$(Get-Date -Format o)] Hermes Link receiver exited with code $Code" | Add-Content -Path $LogFile
exit $Code
'@ | Set-Content -Path $Script -Encoding UTF8

Get-Item $Script
```

## 2. Register the scheduled task

```powershell
$TaskName = "Hermes Link Receiver"
$Script = Join-Path $env:USERPROFILE ".hermes\link\run-hermes-link-receiver.ps1"

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"

$Settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Days 0)

$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Principal $Principal `
  -Description "Durable user-level Hermes Link receiver" `
  -Force
```

## 3. Move from foreground terminal to scheduled task

Stop the foreground receiver first, otherwise the task cannot bind to port 8765.

In the PowerShell window that is currently running the receiver, press:

```text
Ctrl+C
```

Then start the scheduled task from any PowerShell window:

```powershell
Start-ScheduledTask -TaskName "Hermes Link Receiver"
```

## 4. Verify locally on Windows

```powershell
Get-ScheduledTask -TaskName "Hermes Link Receiver"
Get-ScheduledTaskInfo -TaskName "Hermes Link Receiver"

Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://192.168.1.142:8765/health
Invoke-RestMethod http://192.168.1.142:8765/nodes/self
```

## 5. Check logs

```powershell
Get-ChildItem "$env:USERPROFILE\.hermes\link\logs" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5

$Latest = Get-ChildItem "$env:USERPROFILE\.hermes\link\logs\receiver-*.log" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content $Latest.FullName -Tail 100 -Wait
```

## 6. Stop, start, or remove

```powershell
Stop-ScheduledTask -TaskName "Hermes Link Receiver"
Start-ScheduledTask -TaskName "Hermes Link Receiver"
Unregister-ScheduledTask -TaskName "Hermes Link Receiver" -Confirm:$false
```

## Security notes

- Bind to the specific LAN IP, not `0.0.0.0`, when possible.
- Do not add `--pairing-enabled` to the scheduled receiver.
- Create pairing tokens only when needed, with a short TTL:

```powershell
py -m hermes_link --home "$env:USERPROFILE\.hermes\link" pair-token create --ttl 300
```

- Keep Windows Firewall restricted to trusted LAN sources for TCP 8765.

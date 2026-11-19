<#
Creates a visible, normal-privilege scheduled task "SmartScreen (local)" that starts the app
when YOU log on. No admin needed. View/remove it any time in Task Scheduler (Task Scheduler Library).

Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$pyw = if (Test-Path $venvPyw) { $venvPyw } else { "C:\Python314\pythonw.exe" }
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found at $pyw" }
$main = Join-Path $root "main.py"

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$main`"" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT20S"   # give USB and LibreHardwareMonitor a moment after logon
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "SmartScreen (local)" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description "Drives the USB metrics screen. Project: $root" -Force
Write-Host "Registered 'SmartScreen (local)' -> $pyw $main"
Write-Host "Start it now with:  Start-ScheduledTask -TaskName 'SmartScreen (local)'"

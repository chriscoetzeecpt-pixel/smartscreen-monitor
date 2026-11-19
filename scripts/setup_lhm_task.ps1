<#
OPTIONAL. Starts LibreHardwareMonitor elevated at logon, so CPU temperature is available.
Must be run from an ADMIN PowerShell, because LHM needs admin to read CPU sensors.
Our own app stays unelevated and only reads http://localhost:8085/data.json.

Alternative: LHM's own menu "Options > Run On Windows Startup" does the same thing.

Usage (admin PowerShell):
  powershell -ExecutionPolicy Bypass -File scripts\setup_lhm_task.ps1 -LhmExe "C:\Tools\LibreHardwareMonitor\LibreHardwareMonitor.exe"
#>
param([Parameter(Mandatory = $true)][string]$LhmExe)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $LhmExe)) { throw "Not found: $LhmExe" }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this from an elevated (Administrator) PowerShell." }

$action    = New-ScheduledTaskAction -Execute $LhmExe -WorkingDirectory (Split-Path $LhmExe)
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "LibreHardwareMonitor (elevated)" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description "Sensor source for SmartScreen" -Force

# The LHM web server listens on all interfaces. Block other machines from reaching it;
# localhost traffic is not affected by this rule.
if (-not (Get-NetFirewallRule -DisplayName "Block LHM web server (LAN)" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Block LHM web server (LAN)" -Direction Inbound -Protocol TCP `
        -LocalPort 8085 -Action Block | Out-Null
}
Write-Host "Done. LHM will start elevated at logon; port 8085 is blocked from the LAN."

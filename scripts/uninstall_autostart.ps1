# Removes the autostart task created by install_autostart.ps1 and stops the app if running.
Stop-ScheduledTask -TaskName "SmartScreen (local)" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "SmartScreen (local)" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed 'SmartScreen (local)' (if it existed)."

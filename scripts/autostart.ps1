<#
.SYNOPSIS
    Sets Pult up to start automatically with the computer.

.DESCRIPTION
    Adds an "at logon" task to Task Scheduler.

    IMPORTANT: the task is deliberately created with ordinary user rights
    and an "at logon" trigger rather than "at startup". The reason is
    that on Windows a program must run inside the user's session to send
    mouse and keyboard events. Set up as a service it stays in session 0
    and cannot touch the desktop at all - a place many people trip over.

.PARAMETER Remove
    Removes the task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
#>
param(
    [switch]$Remove,
    [string]$TaskName = "Pult"
)

$ErrorActionPreference = "Stop"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Automatic startup has been removed." -ForegroundColor Yellow
    } else {
        Write-Host "There was no such task." -ForegroundColor DarkGray
    }
    return
}

$repo = Split-Path -Parent $PSScriptRoot

# Python without a console: pythonw.exe opens no window
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "Python not found. Install Python first." }
$pythonw = $python -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# Prefer a built .exe when there is one
$exe = Join-Path $repo "dist\Pult\Pult.exe"
if (Test-Path $exe) {
    $execute = $exe
    $arguments = ""
} else {
    $execute = $pythonw
    $arguments = "-m pult"
}

Write-Host "Project: $repo"
Write-Host "Program: $execute $arguments"

$action = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# A delay, to wait for the network: started before an IP address is
# assigned, the program could build its certificate for the wrong
# address.
$trigger.Delay = "PT15S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Limited: no administrator rights are requested. That is deliberate -
# the program does not need them, and not asking is safer.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Pult - control your computer from your phone" -Force | Out-Null

Write-Host ""
Write-Host "Ready. Pult will now start itself when you log in." -ForegroundColor Green
Write-Host "To try it right now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "To remove it:         .\scripts\autostart.ps1 -Remove"

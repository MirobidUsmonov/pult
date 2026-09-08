<#
.SYNOPSIS
    Pult'ni kompyuter yonganda avtomatik ishga tushirishni sozlaydi.

.DESCRIPTION
    Vazifa rejalashtiruvchisiga (Task Scheduler) "kirganda ishga tush"
    vazifasini qo'shadi.

    MUHIM: vazifa ataylab oddiy foydalanuvchi huquqi bilan va "kirganda"
    yaratiladi, "kompyuter yonganda" emas. Sababi Windows'da sichqoncha
    va klaviatura hodisalarini yuborish uchun dastur foydalanuvchi
    seansida ishlashi shart. Xizmat (service) sifatida qo'yilsa u
    0-seansda qoladi va ish stoliga umuman ta'sir qilolmaydi - bu ko'p
    odam qoqiladigan joy.

.PARAMETER Remove
    Vazifani o'chiradi.

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
        Write-Host "Avtomatik ishga tushirish o'chirildi." -ForegroundColor Yellow
    } else {
        Write-Host "Bunday vazifa yo'q edi." -ForegroundColor DarkGray
    }
    return
}

$repo = Split-Path -Parent $PSScriptRoot

# Konsolsiz Python: pythonw.exe oyna ochmaydi
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "Python topilmadi. Avval Python o'rnating." }
$pythonw = $python -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# Yig'ilgan .exe bo'lsa o'shani afzal ko'ramiz
$exe = Join-Path $repo "dist\Pult\Pult.exe"
if (Test-Path $exe) {
    $execute = $exe
    $arguments = ""
} else {
    $execute = $pythonw
    $arguments = "-m pult"
}

Write-Host "Loyiha:  $repo"
Write-Host "Dastur:  $execute $arguments"

$action = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# Kechikish: tarmoq ko'tarilishini kutamiz, aks holda dastur IP manzil
# berilmasidan oldin ishga tushib, sertifikatni noto'g'ri manzil bilan
# yasashi mumkin.
$trigger.Delay = "PT15S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Limited: administrator huquqi so'ralmaydi. Bu ataylab - dasturga
# administrator kerak emas va so'ramagani xavfsizroq.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Pult - telefondan kompyuterni boshqarish agenti" -Force | Out-Null

Write-Host ""
Write-Host "Tayyor. Pult endi siz tizimga kirganingizda o'zi ishga tushadi." -ForegroundColor Green
Write-Host "Hozir sinab ko'rish uchun:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "O'chirish uchun:            .\scripts\autostart.ps1 -Remove"

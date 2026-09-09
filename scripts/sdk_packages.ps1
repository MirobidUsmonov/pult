<#
.SYNOPSIS
    Android SDK paketlarini to'g'ridan-to'g'ri yuklab o'rnatadi.

.DESCRIPTION
    sdkmanager o'z yuklovchisi bilan juda sekin ishlaydi (o'lchovda 90 KB/s,
    o'sha serverdan curl esa 440 KB/s berdi). Paketlar oddiy zip fayllar
    bo'lgani uchun ularni to'g'ridan-to'g'ri olib, joyiga qo'yish mumkin.

    Manzillar Google'ning o'z ro'yxatidan (repository2-3.xml) olingan.
#>
param(
    [string]$Root = "E:\dev-tools"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$sdk = Join-Path $Root "android-sdk"
$dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $dl | Out-Null

# Har bir paket: manzil, ochilgandan keyingi joyi, arxiv ichidagi papka nomi
$packages = @(
    @{
        name  = "platforms/android-34"
        url   = "https://dl.google.com/android/repository/platform-34-ext7_r03.zip"
        zip   = "platform-34.zip"
        dest  = Join-Path $sdk "platforms\android-34"
        check = "android.jar"
    },
    @{
        name  = "build-tools/34.0.0"
        url   = "https://dl.google.com/android/repository/build-tools_r34-windows.zip"
        zip   = "build-tools-34.zip"
        dest  = Join-Path $sdk "build-tools\34.0.0"
        check = "aapt2.exe"
    }
)

foreach ($p in $packages) {
    Write-Host "=== $($p.name) ==="
    if (Test-Path (Join-Path $p.dest $p.check)) {
        Write-Host "  allaqachon o'rnatilgan"
        continue
    }

    $zip = Join-Path $dl $p.zip
    if (-not (Test-Path $zip)) {
        Write-Host "  yuklanmoqda..."
        & curl.exe -L --fail --silent --show-error -o $zip $p.url
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  XATO: yuklab bo'lmadi" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host ("  {0:N1} MB, ochilmoqda..." -f ((Get-Item $zip).Length / 1MB))

    $tmp = Join-Path $Root "_pkg_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force

    # Arxiv ichida bitta papka bo'ladi (odatda "android-14"), uni
    # kerakli nom ostiga ko'chiramiz
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force (Split-Path $p.dest) | Out-Null
    Remove-Item -Recurse -Force $p.dest -ErrorAction SilentlyContinue
    Move-Item $inner.FullName $p.dest
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

    if (Test-Path (Join-Path $p.dest $p.check)) {
        Write-Host "  tayyor" -ForegroundColor Green
    } else {
        Write-Host "  XATO: $($p.check) topilmadi" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Barcha paketlar joyida." -ForegroundColor Green

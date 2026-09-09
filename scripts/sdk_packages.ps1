<#
.SYNOPSIS
    Downloads and installs the Android SDK packages directly.

.DESCRIPTION
    sdkmanager's own downloader is very slow (measured at 90 KB/s, while
    curl gave 440 KB/s from the same server). The packages are plain zip
    files, so they can be fetched directly and put in place.

    The URLs come from Google's own index (repository2-3.xml).
#>
param(
    [string]$Root = "E:\dev-tools"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$sdk = Join-Path $Root "android-sdk"
$dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $dl | Out-Null

# Each package: URL, where it lands once unpacked, the folder name inside
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
            Write-Host "  ERROR: could not download" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host ("  {0:N1} MB, ochilmoqda..." -f ((Get-Item $zip).Length / 1MB))

    $tmp = Join-Path $Root "_pkg_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force

    # The archive holds a single folder (usually "android-14"); move it
    # under the name we need
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force (Split-Path $p.dest) | Out-Null
    Remove-Item -Recurse -Force $p.dest -ErrorAction SilentlyContinue
    Move-Item $inner.FullName $p.dest
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

    if (Test-Path (Join-Path $p.dest $p.check)) {
        Write-Host "  done" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: $($p.check) not found" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Barcha paketlar joyida." -ForegroundColor Green

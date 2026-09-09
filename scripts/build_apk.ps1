<#
.SYNOPSIS
    Builds the APK for the Pult app.

.DESCRIPTION
    Uses the tools android_toolchain.ps1 installed. It leaves the system
    environment alone: JAVA_HOME and ANDROID_HOME are set for this
    process only.

.PARAMETER Release
    A release build instead of debug. Needs a signing key
    (keystore.properties).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1
#>
param(
    [string]$Root = "E:\dev-tools",
    [switch]$Release,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$GradleVersion = "8.7"
$GradleUrl = "https://services.gradle.org/distributions/gradle-$GradleVersion-bin.zip"

$project = Join-Path (Split-Path -Parent $PSScriptRoot) "android"
$jdk = Join-Path $Root "jdk"
$sdk = Join-Path $Root "android-sdk"

if (-not (Test-Path (Join-Path $jdk "bin\java.exe"))) {
    throw "JDK not found. Run first: powershell -File scripts\android_toolchain.ps1"
}
if (-not (Test-Path (Join-Path $sdk "platforms"))) {
    throw "Android SDK not found. Run first: powershell -File scripts\android_toolchain.ps1"
}

# ------------------------------------------------------------- Gradle
$gradleDir = Join-Path $Root "gradle-$GradleVersion"
$gradleBin = Join-Path $gradleDir "bin\gradle.bat"
if (-not (Test-Path $gradleBin)) {
    $zip = Join-Path $Root "downloads\gradle-$GradleVersion.zip"

    # The archive may be half-downloaded (a parallel download, or an
    # interruption). Trying to unpack it gives a baffling error, so its
    # integrity is checked first.
    if (Test-Path $zip) {
        $valid = $false
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
            $valid = $z.Entries.Count -gt 0
            $z.Dispose()
        } catch { $valid = $false }
        if (-not $valid) {
            Write-Host "The Gradle archive is incomplete, downloading it again"
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path $zip)) {
        Write-Host "Downloading Gradle $GradleVersion..."
        & curl.exe -L --fail --silent --show-error -o $zip $GradleUrl
        if ($LASTEXITCODE -ne 0) { throw "could not download Gradle" }
    }

    Write-Host "Unpacking Gradle..."
    $tmp = Join-Path $Root "_gradle_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Move-Item (Join-Path $tmp "gradle-$GradleVersion") $gradleDir
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$env:JAVA_HOME = $jdk
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$env:PATH = "$jdk\bin;$env:PATH"

# Gradle reads the SDK location from this file. It is not committed
# (it is in .gitignore), because the path differs on every computer.
$local = Join-Path $project "local.properties"
"sdk.dir=$($sdk -replace '\\', '\\')" | Set-Content -Path $local -Encoding ASCII

# Generate the Gradle wrapper when it is missing, so anyone who clones
# the project can build without installing Gradle.
if (-not (Test-Path (Join-Path $project "gradlew.bat"))) {
    Write-Host "Generating the Gradle wrapper..."
    Push-Location $project
    & $gradleBin wrapper --gradle-version $GradleVersion --console=plain
    Pop-Location
}

$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Write-Host ""
Write-Host "=== Building: $task ==="

Push-Location $project
try {
    if ($Clean) { & $gradleBin clean --console=plain }
    & $gradleBin $task --console=plain
    if ($LASTEXITCODE -ne 0) { throw "the build failed (code $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$apk = Get-ChildItem (Join-Path $project "app\build\outputs\apk") -Recurse -Filter "*.apk" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($apk) {
    Write-Host ""
    Write-Host "=== Ready ===" -ForegroundColor Green
    Write-Host "  $($apk.FullName)"
    Write-Host ("  {0:N1} MB" -f ($apk.Length / 1MB))
} else {
    Write-Host "No APK found" -ForegroundColor Yellow
}

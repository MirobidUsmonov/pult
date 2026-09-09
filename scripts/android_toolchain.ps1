<#
.SYNOPSIS
    Installs the tools an Android APK build needs, portably.

.DESCRIPTION
    Downloads the JDK and the Android SDK into one folder. It leaves the
    system alone: neither PATH nor the registry is changed, and no
    administrator rights are needed. If you no longer want it, deleting
    the folder is enough.

    Running the script again is safe: whatever is already downloaded and
    unpacked is not fetched again.

.PARAMETER Root
    The folder the tools go into.
#>
param(
    [string]$Root = "E:\dev-tools"
)

# Careful: ErrorActionPreference is "Continue" on purpose.
# In PowerShell 5.1 anything an external program writes to stderr counts
# as an error, and with "Stop" the script would halt. Both java -version
# and sdkmanager write ordinary messages to stderr, so success is
# checked by exit code instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# The JDK comes from Amazon on purpose: in testing Adoptium gave
# 49 KB/s and Microsoft 54 KB/s, while Amazon gave 2 MB/s. The same
# file, a 40-fold difference.
$JdkUrl = "https://corretto.aws/downloads/latest/amazon-corretto-17-x64-windows-jdk.zip"
$CmdToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

function Die($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

function Fetch($url, $out) {
    if (Test-Path $out) {
        Write-Host "  already downloaded: $(Split-Path -Leaf $out)"
        return
    }
    Write-Host "  downloading: $(Split-Path -Leaf $out)"
    & curl.exe -L --fail --silent --show-error -o $out $url
    if ($LASTEXITCODE -ne 0) { Die "could not download: $url" }
    Write-Host ("  done: {0:N1} MB" -f ((Get-Item $out).Length / 1MB))
}

function Unpack($zip, $target, $innerName) {
    # The archive holds a single folder; move it where it belongs
    $tmp = Join-Path $Root "_unpack_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $inner = if ($innerName) {
        Get-Item (Join-Path $tmp $innerName)
    } else {
        Get-ChildItem $tmp -Directory | Select-Object -First 1
    }
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    Move-Item $inner.FullName $target
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force $Root | Out-Null
$dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $dl | Out-Null

# ---------------------------------------------------------------- JDK
$jdkZip = Join-Path $dl "jdk17.zip"
$jdkDir = Join-Path $Root "jdk"
$java = Join-Path $jdkDir "bin\java.exe"

Write-Host "=== JDK 17 ==="
if (Test-Path $java) {
    Write-Host "  already installed"
} else {
    Fetch $JdkUrl $jdkZip
    Write-Host "  unpacking..."
    Unpack $jdkZip $jdkDir $null
    if (-not (Test-Path $java)) { Die "the JDK did not unpack" }
}

$env:JAVA_HOME = $jdkDir
$env:PATH = "$jdkDir\bin;$env:PATH"

# ------------------------------------------------------- Android SDK
$sdk = Join-Path $Root "android-sdk"
$ctZip = Join-Path $dl "cmdline-tools.zip"
$ctTarget = Join-Path $sdk "cmdline-tools\latest"
$sdkmanager = Join-Path $ctTarget "bin\sdkmanager.bat"

Write-Host ""
Write-Host "=== Android SDK tools ==="
if (Test-Path $sdkmanager) {
    Write-Host "  already installed"
} else {
    Fetch $CmdToolsUrl $ctZip
    Write-Host "  unpacking..."
    # sdkmanager insists on finding itself inside "cmdline-tools/latest"
    Unpack $ctZip $ctTarget "cmdline-tools"
    if (-not (Test-Path $sdkmanager)) { Die "the SDK tools did not unpack" }
}

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk

Write-Host ""
Write-Host "=== Licences ==="
# sdkmanager asks about the licences interactively. Piping it a "y" is
# unreliable: the process may not be reading standard input at all, and
# then the install stops silently. So the acceptances are written as
# files, which is what CI systems do too. The values are the public SHA1
# fingerprints Google publishes.
$licenses = @{
    "android-sdk-license" = @(
        "8933bad161af4178b1185d1a37fbf41ea5269c55",
        "d56f5187479451eabf01fb78af6dfcb131a6481e",
        "24333f8a63b6825ea9c5514f83c2829b004d1fee")
    "android-sdk-preview-license"  = @("84831b9409646a918e30573bab4c9c91346d8abd")
    "android-sdk-arm-dbt-license"  = @("859f317696f67ef3d7f30a50a5560e7834b43903")
    "intel-android-extra-license"  = @("d975f751698a77b662f1254ddbeed3901e976f5a")
    "google-gdk-license"           = @("33b6a2b64607f11b759f320ef9dff4ae5c47d97a")
}
$licDir = Join-Path $sdk "licenses"
New-Item -ItemType Directory -Force $licDir | Out-Null
foreach ($name in $licenses.Keys) {
    $text = "`n" + ($licenses[$name] -join "`n") + "`n"
    Set-Content -Path (Join-Path $licDir $name) -Value $text -Encoding ASCII -NoNewline
}
Write-Host "  accepted ($($licenses.Count))"

Write-Host ""
Write-Host "=== Platform and build tools ==="
foreach ($pkg in @("platform-tools", "platforms;android-34", "build-tools;34.0.0")) {
    Write-Host "  installing: $pkg"
    # The output is not hidden: hiding it once buried the licence
    # question, and the failed install went unnoticed.
    & $sdkmanager --sdk_root="$sdk" $pkg | Where-Object { $_ -notmatch "^\[=*\s*\]" }
    if ($LASTEXITCODE -ne 0) { Die "not installed: $pkg" }
}

# ------------------------------------------------------------- checks
Write-Host ""
Write-Host "=== Checks ==="
$ok = $true
$checks = @(
    @{ n = "java";       p = $java },
    @{ n = "aapt2";      p = (Join-Path $sdk "build-tools\34.0.0\aapt2.exe") },
    @{ n = "d8";         p = (Join-Path $sdk "build-tools\34.0.0\d8.bat") },
    @{ n = "android-34"; p = (Join-Path $sdk "platforms\android-34\android.jar") },
    @{ n = "adb";        p = (Join-Path $sdk "platform-tools\adb.exe") }
)
foreach ($c in $checks) {
    if (Test-Path $c.p) {
        Write-Host ("  [OK]  {0}" -f $c.n)
    } else {
        Write-Host ("  [MISSING] {0}  ->  {1}" -f $c.n, $c.p) -ForegroundColor Yellow
        $ok = $false
    }
}

$size = (Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ""
Write-Host ("  total size: {0:N2} GB" -f $size)
Write-Host "  JAVA_HOME    = $jdkDir"
Write-Host "  ANDROID_HOME = $sdk"

if ($ok) {
    Write-Host ""
    Write-Host "Ready. Now:  powershell -File scripts\build_apk.ps1" -ForegroundColor Green
} else {
    Die "some tools were not installed"
}

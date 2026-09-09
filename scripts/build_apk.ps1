<#
.SYNOPSIS
    Pult ilovasining APK faylini yig'adi.

.DESCRIPTION
    android_toolchain.ps1 o'rnatgan asboblardan foydalanadi. Tizim
    o'zgaruvchilariga tegmaydi: JAVA_HOME va ANDROID_HOME faqat shu
    jarayon uchun qo'yiladi.

.PARAMETER Release
    Debug o'rniga release yig'ish. Imzo kaliti kerak (keystore.properties).

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
    throw "JDK topilmadi. Avval: powershell -File scripts\android_toolchain.ps1"
}
if (-not (Test-Path (Join-Path $sdk "platforms"))) {
    throw "Android SDK topilmadi. Avval: powershell -File scripts\android_toolchain.ps1"
}

# ------------------------------------------------------------- Gradle
$gradleDir = Join-Path $Root "gradle-$GradleVersion"
$gradleBin = Join-Path $gradleDir "bin\gradle.bat"
if (-not (Test-Path $gradleBin)) {
    $zip = Join-Path $Root "downloads\gradle-$GradleVersion.zip"

    # Arxiv yarim yuklangan bo'lishi mumkin (parallel yuklash yoki uzilish).
    # Uni ochishga urinish tushunarsiz xato beradi, shuning uchun avval
    # butunligini tekshiramiz.
    if (Test-Path $zip) {
        $valid = $false
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
            $valid = $z.Entries.Count -gt 0
            $z.Dispose()
        } catch { $valid = $false }
        if (-not $valid) {
            Write-Host "Gradle arxivi to'liq emas, qayta yuklanadi"
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path $zip)) {
        Write-Host "Gradle $GradleVersion yuklanmoqda..."
        & curl.exe -L --fail --silent --show-error -o $zip $GradleUrl
        if ($LASTEXITCODE -ne 0) { throw "Gradle yuklab bo'lmadi" }
    }

    Write-Host "Gradle ochilmoqda..."
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

# Gradle SDK joyini shu fayldan o'qiydi. Fayl loyihaga qo'shilmaydi
# (.gitignore'da), chunki yo'l har kompyuterda boshqacha.
$local = Join-Path $project "local.properties"
"sdk.dir=$($sdk -replace '\\', '\\')" | Set-Content -Path $local -Encoding ASCII

# Gradle o'ram fayllari (wrapper) bo'lmasa yasab qo'yamiz - shunda
# loyihani yuklab olgan odam Gradle o'rnatmasdan yig'a oladi.
if (-not (Test-Path (Join-Path $project "gradlew.bat"))) {
    Write-Host "Gradle o'rami yasalmoqda..."
    Push-Location $project
    & $gradleBin wrapper --gradle-version $GradleVersion --console=plain
    Pop-Location
}

$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Write-Host ""
Write-Host "=== Yig'ilmoqda: $task ==="

Push-Location $project
try {
    if ($Clean) { & $gradleBin clean --console=plain }
    & $gradleBin $task --console=plain
    if ($LASTEXITCODE -ne 0) { throw "yig'ish muvaffaqiyatsiz (kod $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$apk = Get-ChildItem (Join-Path $project "app\build\outputs\apk") -Recurse -Filter "*.apk" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($apk) {
    Write-Host ""
    Write-Host "=== Tayyor ===" -ForegroundColor Green
    Write-Host "  $($apk.FullName)"
    Write-Host ("  {0:N1} MB" -f ($apk.Length / 1MB))
} else {
    Write-Host "APK topilmadi" -ForegroundColor Yellow
}

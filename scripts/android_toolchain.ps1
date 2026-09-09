<#
.SYNOPSIS
    Android APK yig'ish uchun kerakli asboblarni ko'chma qilib o'rnatadi.

.DESCRIPTION
    JDK va Android SDK ni bitta papkaga yuklab oladi. Tizim sozlamalariga
    tegilmaydi: PATH ham, registr ham o'zgarmaydi, administrator huquqi
    kerak emas. Kerak bo'lmay qolsa papkani o'chirish kifoya.

    Bu ataylab: yig'ish uchun kerak bo'lgan asboblar foydalanuvchining
    kompyuterini o'zgartirib yuborishi shart emas.

.PARAMETER Root
    Asboblar joylashadigan papka.
#>
param(
    [string]$Root = "E:\dev-tools"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest ni sekinlashtiradi

# JDK manbasi ataylab Amazon: sinovda Adoptium 49 KB/s, Microsoft 54 KB/s
# bergan bo'lsa, Amazon 2 MB/s berdi. Bir xil fayl, 40 barobar farq -
# manba tanlash yuklab olish vaqtini soatlardan daqiqalarga tushiradi.
$JdkUrl = "https://corretto.aws/downloads/latest/amazon-corretto-17-x64-windows-jdk.zip"
$CmdToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

New-Item -ItemType Directory -Force $Root | Out-Null
$dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $dl | Out-Null

function Fetch($url, $out) {
    if (Test-Path $out) {
        Write-Host "  allaqachon yuklangan: $(Split-Path -Leaf $out)"
        return
    }
    Write-Host "  yuklanmoqda: $(Split-Path -Leaf $out)"
    & curl.exe -L --fail --silent --show-error -o $out $url
    if ($LASTEXITCODE -ne 0) { throw "yuklab bo'lmadi: $url" }
    $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host "  tayyor: $mb MB"
}

# ---------------------------------------------------------------- JDK
$jdkZip = Join-Path $dl "jdk17.zip"
$jdkDir = Join-Path $Root "jdk"
Write-Host "=== JDK 17 ==="
Fetch $JdkUrl $jdkZip
if (-not (Test-Path (Join-Path $jdkDir "bin\java.exe"))) {
    Write-Host "  ochilmoqda..."
    $tmp = Join-Path $Root "_jdk_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $jdkZip -DestinationPath $tmp -Force
    # Arxiv ichida bitta papka bo'ladi, uni yuqoriga ko'taramiz
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    Remove-Item -Recurse -Force $jdkDir -ErrorAction SilentlyContinue
    Move-Item $inner.FullName $jdkDir
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
$env:JAVA_HOME = $jdkDir
$env:PATH = "$jdkDir\bin;$env:PATH"
# Diqqat: java -version xabarni stderr'ga yozadi. PowerShell'da native
# dasturning stderr'ini 2>&1 bilan yo'naltirish uni xatoga aylantiradi
# va ErrorActionPreference="Stop" bilan skript to'xtab qoladi.
$verFile = Join-Path $dl "_java_version.txt"
& (Join-Path $jdkDir "bin/java.exe") -version 2> $verFile
$ver = (Get-Content $verFile -ErrorAction SilentlyContinue | Select-Object -First 1)
Remove-Item $verFile -ErrorAction SilentlyContinue
Write-Host "  java: $ver"

# ------------------------------------------------------- Android SDK
$sdk = Join-Path $Root "android-sdk"
$ctZip = Join-Path $dl "cmdline-tools.zip"
Write-Host ""
Write-Host "=== Android SDK asboblari ==="
Fetch $CmdToolsUrl $ctZip

$ctTarget = Join-Path $sdk "cmdline-tools\latest"
if (-not (Test-Path (Join-Path $ctTarget "bin\sdkmanager.bat"))) {
    Write-Host "  ochilmoqda..."
    $tmp = Join-Path $Root "_ct_tmp"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $ctZip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force (Split-Path $ctTarget) | Out-Null
    Remove-Item -Recurse -Force $ctTarget -ErrorAction SilentlyContinue
    # Arxiv ichida "cmdline-tools" papkasi bor, sdkmanager esa uni
    # "latest" nomi ostida ko'rishni talab qiladi
    Move-Item (Join-Path $tmp "cmdline-tools") $ctTarget
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$sdkmanager = Join-Path $ctTarget "bin\sdkmanager.bat"

Write-Host ""
Write-Host "=== Platforma va yig'ish asboblari ==="
# Litsenziyalar: sdkmanager ularni interaktiv so'raydi, biz oldindan
# tasdiqlaymiz - aks holda yig'ish jarayoni to'xtab qoladi.
$yes = ("y`n" * 30)
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"   # sdkmanager stderr'ga yozadi
$yes | & $sdkmanager --sdk_root="$sdk" --licenses | Out-Null

$packages = @(
    "platform-tools",
    "platforms;android-34",
    "build-tools;34.0.0"
)
foreach ($pkg in $packages) {
    Write-Host "  o'rnatilmoqda: $pkg"
    & $sdkmanager --sdk_root="$sdk" $pkg | Select-Object -Last 1
}
$ErrorActionPreference = $prev

Write-Host ""
Write-Host "=== Tayyor ==="
Write-Host "  JAVA_HOME    = $jdkDir"
Write-Host "  ANDROID_HOME = $sdk"
$size = (Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ("  jami hajm    = {0:N2} GB" -f $size)

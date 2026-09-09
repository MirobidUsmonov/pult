<#
.SYNOPSIS
    Android APK yig'ish uchun kerakli asboblarni ko'chma qilib o'rnatadi.

.DESCRIPTION
    JDK va Android SDK ni bitta papkaga yuklab oladi. Tizim sozlamalariga
    tegilmaydi: PATH ham, registr ham o'zgarmaydi, administrator huquqi
    kerak emas. Kerak bo'lmay qolsa papkani o'chirish kifoya.

    Skriptni qayta ishga tushirish xavfsiz: yuklab olingan va ochilgan
    narsalar qaytadan qilinmaydi.

.PARAMETER Root
    Asboblar joylashadigan papka.
#>
param(
    [string]$Root = "E:\dev-tools"
)

# Diqqat: ErrorActionPreference ataylab "Continue".
# PowerShell 5.1 da tashqi dasturning stderr'ga yozgani xato deb
# hisoblanadi va "Stop" bilan skript to'xtab qoladi. java -version ham,
# sdkmanager ham oddiy xabarlarini stderr'ga yozadi. Shuning uchun
# muvaffaqiyatni chiqish kodi bilan tekshiramiz.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# JDK manbasi ataylab Amazon: sinovda Adoptium 49 KB/s, Microsoft 54 KB/s
# bergan bo'lsa, Amazon 2 MB/s berdi. Bir xil fayl, 40 barobar farq.
$JdkUrl = "https://corretto.aws/downloads/latest/amazon-corretto-17-x64-windows-jdk.zip"
$CmdToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

function Die($msg) {
    Write-Host ""
    Write-Host "XATO: $msg" -ForegroundColor Red
    exit 1
}

function Fetch($url, $out) {
    if (Test-Path $out) {
        Write-Host "  allaqachon yuklangan: $(Split-Path -Leaf $out)"
        return
    }
    Write-Host "  yuklanmoqda: $(Split-Path -Leaf $out)"
    & curl.exe -L --fail --silent --show-error -o $out $url
    if ($LASTEXITCODE -ne 0) { Die "yuklab bo'lmadi: $url" }
    Write-Host ("  tayyor: {0:N1} MB" -f ((Get-Item $out).Length / 1MB))
}

function Unpack($zip, $target, $innerName) {
    # Arxiv ichida bitta papka bo'ladi, uni kerakli joyga ko'chiramiz
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
    Write-Host "  allaqachon o'rnatilgan"
} else {
    Fetch $JdkUrl $jdkZip
    Write-Host "  ochilmoqda..."
    Unpack $jdkZip $jdkDir $null
    if (-not (Test-Path $java)) { Die "JDK ochilmadi" }
}

$env:JAVA_HOME = $jdkDir
$env:PATH = "$jdkDir\bin;$env:PATH"

# ------------------------------------------------------- Android SDK
$sdk = Join-Path $Root "android-sdk"
$ctZip = Join-Path $dl "cmdline-tools.zip"
$ctTarget = Join-Path $sdk "cmdline-tools\latest"
$sdkmanager = Join-Path $ctTarget "bin\sdkmanager.bat"

Write-Host ""
Write-Host "=== Android SDK asboblari ==="
if (Test-Path $sdkmanager) {
    Write-Host "  allaqachon o'rnatilgan"
} else {
    Fetch $CmdToolsUrl $ctZip
    Write-Host "  ochilmoqda..."
    # sdkmanager o'zini "cmdline-tools/latest" ichida ko'rishni talab qiladi
    Unpack $ctZip $ctTarget "cmdline-tools"
    if (-not (Test-Path $sdkmanager)) { Die "SDK asboblari ochilmadi" }
}

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk

Write-Host ""
Write-Host "=== Litsenziyalar ==="
# sdkmanager litsenziyalarni interaktiv so'raydi. Unga "y" yuborish
# ishonchsiz: jarayon standart kirishni kutmasligi ham mumkin va shunda
# o'rnatish jimgina to'xtab qoladi. Shuning uchun tasdiqlarni fayl
# sifatida yozamiz - CI tizimlari ham shunday qiladi. Qiymatlar Google
# e'lon qilgan ochiq SHA1 barmoq izlari.
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
Write-Host "  tasdiqlandi ($($licenses.Count) ta)"

Write-Host ""
Write-Host "=== Platforma va yig'ish asboblari ==="
foreach ($pkg in @("platform-tools", "platforms;android-34", "build-tools;34.0.0")) {
    Write-Host "  o'rnatilmoqda: $pkg"
    # Chiqishni yashirmaymiz: yashirilganda litsenziya savoli ko'rinmay
    # qolib, o'rnatilmagani ham bilinmay ketgan edi.
    & $sdkmanager --sdk_root="$sdk" $pkg | Where-Object { $_ -notmatch "^\[=*\s*\]" }
    if ($LASTEXITCODE -ne 0) { Die "o'rnatilmadi: $pkg" }
}

# ------------------------------------------------------------ tekshiruv
Write-Host ""
Write-Host "=== Tekshiruv ==="
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
        Write-Host ("  [YOQ] {0}  ->  {1}" -f $c.n, $c.p) -ForegroundColor Yellow
        $ok = $false
    }
}

$size = (Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ""
Write-Host ("  jami hajm: {0:N2} GB" -f $size)
Write-Host "  JAVA_HOME    = $jdkDir"
Write-Host "  ANDROID_HOME = $sdk"

if ($ok) {
    Write-Host ""
    Write-Host "Tayyor. Endi:  powershell -File scripts\build_apk.ps1" -ForegroundColor Green
} else {
    Die "ba'zi asboblar o'rnatilmadi"
}

# Register the native-messaging host for Chrome, Chromium, and Edge on Windows.
# Usage: powershell -File scripts/install-nm-host.ps1 -HostPath C:\path\to\local-translate-host.exe
param(
    [Parameter(Mandatory = $true)]
    [string]$HostPath
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$identity = Get-Content -Raw (Join-Path $root "shared/extension-identity.json") | ConvertFrom-Json
if (-not (Test-Path $HostPath)) {
    throw "ホスト実行ファイルがありません: $HostPath"
}

$manifestPath = Join-Path $env:LOCALAPPDATA "local-translate-extension\com.localtranslate.host.json"
New-Item -ItemType Directory -Force -Path (Split-Path $manifestPath) | Out-Null
@{
    name = $identity.hostName
    description = "Local Translate companion host"
    path = $HostPath
    type = "stdio"
    allowed_origins = @("chrome-extension://$($identity.extensionId)/")
} | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding utf8

$browsers = @(
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$($identity.hostName)",
    "HKCU:\Software\Chromium\NativeMessagingHosts\$($identity.hostName)",
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$($identity.hostName)"
)
foreach ($key in $browsers) {
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name "(default)" -Value $manifestPath
    Write-Output "登録しました: $key"
}

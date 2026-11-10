# Windows setup for the companion. Syncs the virtualenv, then registers native messaging.
# Registry registration changes HKCU, so it asks before writing unless -Yes is passed.
param(
    [string]$HostPath = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $HostPath) {
    $HostPath = Join-Path $root "companion\.venv\Scripts\local-translate-host.exe"
}

Push-Location (Join-Path $root "companion")
try {
    uv sync --all-groups
} finally {
    Pop-Location
}
if (-not (Test-Path $HostPath)) {
    throw "ホスト実行ファイルがありません: $HostPath"
}

if (-not $Yes) {
    $answer = Read-Host "HKCU に Native Messaging ホストを登録します。続行しますか? [y/N]"
    if ($answer -notin @("y", "Y")) {
        throw "登録を中止しました"
    }
}

$state = Join-Path $env:LOCALAPPDATA "local-translate-extension"
New-Item -ItemType Directory -Force -Path $state | Out-Null
@(
    "Windows の Haas-ja は speech.cpp（Win x64、CPU / Vulkan）を使う。",
    "NeMo と WSL2 は、speech.cpp の品質差が許容できない場合だけ検討する。",
    "Web Store 公開は見送り、配布は GitHub Releases。"
) | Set-Content -Path (Join-Path $state "distribution-decisions.txt") -Encoding utf8

& (Join-Path $root "scripts\install-nm-host.ps1") -HostPath $HostPath
Write-Output "状態ディレクトリ: $state"
Write-Output "拡張は apps/extension をビルドし、.output/chrome-mv3 を開発者モードで読み込みます。"

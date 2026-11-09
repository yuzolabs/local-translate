# Windows setup for the companion. This script registers native messaging and
# records the speech.cpp decision. It does not bundle model weights.
param(
    [Parameter(Mandatory = $true)]
    [string]$HostPath
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
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

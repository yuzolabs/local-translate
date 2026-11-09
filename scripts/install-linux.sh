#!/bin/sh
# Install the companion into its virtualenv and register the native-messaging host.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root/companion"
uv sync --all-groups
"$root/scripts/install-nm-host.sh" "$root/companion/.venv/bin/local-translate-host"
echo "拡張は apps/extension で bun run build し、.output/chrome-mv3 を開発者モードで読み込みます。"

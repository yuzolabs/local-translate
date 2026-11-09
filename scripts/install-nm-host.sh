#!/bin/sh
# Register the native-messaging host for Chrome, Chromium, and Edge.
# Usage: scripts/install-nm-host.sh [/path/to/local-translate-host]
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
identity=$root/shared/extension-identity.json
host_name=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["hostName"])' "$identity")
extension_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["extensionId"])' "$identity")

if [ "${1:-}" != "" ]; then
  host_path=$1
else
  host_path=$root/companion/.venv/bin/local-translate-host
fi
if [ ! -x "$host_path" ]; then
  echo "ホスト実行ファイルがありません: $host_path" >&2
  echo "先に companion で uv sync してください。" >&2
  exit 1
fi

manifest=$(python3 - "$host_name" "$host_path" "$extension_id" <<'PY'
import json, sys
name, path, extension_id = sys.argv[1:]
print(json.dumps({
    "name": name,
    "description": "Local Translate companion host",
    "path": path,
    "type": "stdio",
    "allowed_origins": [f"chrome-extension://{extension_id}/"],
}, ensure_ascii=False, indent=2))
PY
)

config=${XDG_CONFIG_HOME:-$HOME/.config}
for browser in google-chrome chromium microsoft-edge; do
  dir=$config/$browser/NativeMessagingHosts
  mkdir -p "$dir"
  printf '%s\n' "$manifest" > "$dir/$host_name.json"
  echo "登録しました: $dir/$host_name.json"
done

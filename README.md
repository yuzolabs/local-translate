# local-translate-extension

完全ローカルで翻訳と音声認識を実行する Chrome 拡張と Python コンパニオンのモノレポです。

ページ全体翻訳、選択範囲翻訳、入力ボックス翻訳、動画の字幕翻訳、任意の GGUF モデルの登録に対応します。モデルの初回ダウンロード時を除き、原文や音声を外部へ送信しません。

## 構成

- `apps/extension`: WXT + TypeScript + Preact の MV3 拡張です。権限は `nativeMessaging`、`tabCapture`、`offscreen`、`contextMenus`、`storage`、`scripting`、`sidePanel`、`activeTab`、`tabs` です。`host_permissions` は `http://127.0.0.1/*` と `http://localhost/*` です。
- `companion`: Python パッケージ `local_translate` です。`uv` で管理し、エントリは `local-translate-daemon` と `local-translate-host` です。
- `shared/extension-identity.json`: 拡張 ID は `extensionId` で定義しており、manifest の `key` と一致します。
- `scripts/install-nm-host.sh`、`scripts/install-nm-host.ps1`: Native Messaging ホスト名は `com.localtranslate.host` です。Linux の登録は `scripts/install-nm-host.sh`、Windows の登録は `scripts/install-nm-host.ps1` です。
- `scripts/smoke_translate.py`: 0.8B モデルの取得と CPU での翻訳疎通を確認するスクリプトです。

## 通信とランタイム

### 通信と認証

拡張は Native Messaging で `port` と `token` を受け取り、以後は `127.0.0.1` の HTTP と WebSocket で通信します。HTTP リクエストには `Authorization: Bearer` を付けます。ブラウザの WebSocket は最初のテキストフレーム `{"type":"auth","token":"..."}` でも認証を受け付けます。

### 翻訳モデル

翻訳モデルは `llama.cpp`（ピンは `b12211`）の `llama-server` を子プロセス起動します。PLaMo は公式の PFML3 を Completions API に渡し、任意の GGUF は汎用の翻訳指示を使います。

アイドル 10 分で `llama-server` を止めます。既定の翻訳先は日本語で、文体は `polite` と `plain` です。用語集とサイト別設定はグローバル設定に含まれます。

### 音声認識モデル

ASR の既定は `speech.cpp` `0.10.1` の `haas-ja-v1.0` です。字幕の本番経路は `speech serve` に `haas-ja-v1.0` と `silero-vad` を載せて Realtime の `server_vad` を使います。`segment-worker` 経路も実装してあります。

多言語の音声認識は `whisper.cpp` `b5454` の `ggml-large-v3-turbo` と `ggml-base` で、代替は `speech.cpp` の `qwen3-asr-0.6b` です。

### 状態とデータの保存先

Linux での保存先は次のパスです。

- 状態: `~/.local/state/local-translate-extension`
- データ: `~/.local/share/local-translate-extension`

上書きには環境変数 `LOCAL_TRANSLATE_STATE_DIR` と `LOCAL_TRANSLATE_DATA_DIR` を使います。

### ライセンス表記と配布

About に `Built with PLaMo` を表示します。Web Store 公開は見送り、配布先は GitHub Releases です。Windows の Haas-ja は `speech.cpp` を使う判断で、NeMo や WSL2 は品質差が許容できない場合だけ検討します。

## 開発手順

### 依存関係のインストール

依存関係はリポジトリ直下の `bun install`（ロックファイルがある場合は `bun install --frozen-lockfile`）と、`companion` での `uv sync` で揃えます。

```bash
bun install --frozen-lockfile
cd companion && uv sync
```

### 拡張のビルドと読み込み

拡張のビルドは `apps/extension` で `bun run build` を実行します。成果物は `apps/extension/.output/chrome-mv3` に出力されるため、Chrome の開発者モードで読み込みます。

```bash
cd apps/extension && bun run build
```

### Native Messaging ホストの登録

`scripts/install-nm-host.sh` にホスト実行ファイルのパスを渡して登録します。ホスト実行ファイルは `uv sync` 後の `companion/.venv/bin/local-translate-host` です。

```bash
scripts/install-nm-host.sh "$(pwd)/companion/.venv/bin/local-translate-host"
```

導入スクリプトに `scripts/install-linux.sh` と `scripts/install-companion.ps1` があります。Linux は `uv sync` と Native Messaging 登録を続けて行います。Windows はホスト実行ファイルのパスを渡しますが、Windows 実機では未実行です。

### 疎通確認

疎通確認には `scripts/smoke_translate.py` を使います。このスクリプトは `0.8B` `Q4_K_M` を同意後に取得し、CPU・コンテキスト 512 で `"Hello."` を訳します。

```bash
cd companion && uv run python ../scripts/smoke_translate.py
```

### テスト

テストは `companion` で `uv run pytest`、拡張で `bun run test`、拡張 UI は `bunx playwright test` です。安定版 Chrome が `--load-extension` を無視するため、Playwright は CDP の `Extensions.loadUnpacked` で拡張を読み込みます。`DISPLAY` がないときはテストが `Xvfb` を起動します。

```bash
cd companion && uv run pytest
cd apps/extension && bun run test
cd apps/extension && bunx playwright test
```

## 検証結果

### 検証済みの事実

この開発機で `0.8B` CPU、コンテキスト 512 のとき、`curl` 相当の `/api/translate` と拡張のオプションページ「翻訳する」の両方が `"Hello."` を「こんにちは。」と返しました。モデル起動を含む壁時計は約 6 秒、起動後の生成レイテンシは `metrics.jsonl` で約 0.3 秒でした。

拡張のコンテンツスクリプト経由でも、ページ全体は `"The meeting has been rescheduled to next Monday."` が「会議は来週の月曜日に延期されました。」、選択範囲と入力欄は `"Please save your changes."` が「変更内容を保存してください。」となることを確認しました。記録は `reports/translation-0.8b-cpu.json` です。

`speech.cpp` `0.10.1` の CPU ビルドは起動し、`silero-vad` は 1 秒の無音で区間 0、`haas-ja-v1.0` の `Q8_0` は worker が `ready` になり文字起こし要求が完了しました。実音声の品質、`F16` との比較、`asr-ja-bench` の CER、字幕の E2E レイテンシは未測定です。記録は `reports/speech-cpp-haas-cpu.json` です。

### 未実施の項目

- Haas-ja の実音声
- 31B モデル
- CUDA / Vulkan の実測
- Windows 実機
- Web Store 提出

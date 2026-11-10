# local-translate-extension

完全ローカルで翻訳と音声認識を実行する Chrome 拡張と Python コンパニオンのモノレポです。

ページ全体翻訳、選択範囲翻訳、入力ボックス翻訳、動画の字幕翻訳、任意の GGUF モデルの登録に対応します。モデルの初回ダウンロード時を除き、原文や音声を外部へ送信しません。

## 構成

- `apps/extension`: WXT + TypeScript + Preact の MV3 拡張です。権限は `nativeMessaging`、`tabCapture`、`offscreen`、`contextMenus`、`storage`、`scripting`、`sidePanel`、`activeTab`、`tabs` です。`host_permissions` は `http://127.0.0.1/*` と `http://localhost/*` です。
- `companion`: Python パッケージ `local_translate` です。`uv` で管理し、エントリは `local-translate-daemon` と `local-translate-host` です。
- `shared/extension-identity.json`: 拡張 ID は `extensionId` で定義しており、manifest の `key` と一致します。
- `scripts/install-nm-host.sh`、`scripts/install-nm-host.ps1`、`scripts/install-companion.ps1`: Native Messaging ホスト名は `com.localtranslate.host` です。Linux の登録は `scripts/install-nm-host.sh`、Windows の登録は `scripts/install-nm-host.ps1` です。Windows 導入用の `scripts/install-companion.ps1` は `uv sync --all-groups` を実行し、`-HostPath` 省略時は `companion\.venv\Scripts\local-translate-host.exe` を使います。HKCU への登録前に確認を行い、`-Yes` で確認をスキップします。
- `scripts/smoke_translate.py`: 0.8B モデルの取得と翻訳疎通を確認するスクリプトです。既定は CPU で、`--backend` で CUDA なども選べます。

## 通信とランタイム

### 通信と認証

拡張は Native Messaging で `port` と `token` を受け取り、以後は `127.0.0.1` の HTTP と WebSocket で通信します。HTTP リクエストには `Authorization: Bearer` を付けます。ブラウザの WebSocket は最初のテキストフレーム `{"type":"auth","token":"..."}` でも認証を受け付けます。

拡張は daemon の `WS /api/events` を購読し、モデル DL 進捗・`llama-server` の起動/停止・エラーを設定 UI へ反映します。進捗イベントは約 1 秒間隔で表示を更新します。

### 翻訳モデル

翻訳モデルは `llama.cpp`（ピンは `b12211`）の `llama-server` を子プロセス起動します。Windows の llama.cpp CUDA 版は別資産の cudart も取得します。設定 `gpu_backend=cuda` は「NVIDIA GPU を使う」であり、翻訳は CUDA、speech.cpp は同じ GPU を Vulkan で使います。PLaMo は公式の PFML3 を Completions API に渡し、任意の GGUF は汎用の翻訳指示を使います。

アイドル 10 分で `llama-server` を止めます。既定の翻訳先は日本語で、文体は `polite` と `plain` です。用語集とサイト別設定はグローバル設定に含まれます。daemon は起動時に保存済みの `parallel` 設定を翻訳セマフォへ反映します。

Hugging Face からの GGUF ダウンロードは、公開 LFS ポインターの SHA-256 と照合してから採用します。ダウンロード中は 0.5 秒間隔で `model.download.progress` イベントを通知します。`speech.cpp` カタログは従来どおり `speech pull` の SHA-256 検証を使います。

### 音声認識モデル

ASR の既定は `speech.cpp` `0.10.1` の `haas-ja-v1.0` です。字幕の本番経路は `speech serve` に `haas-ja-v1.0` と `silero-vad` を載せて Realtime の `server_vad` を使います。`segment-worker` 経路も実装してあります。

多言語の音声認識は `whisper.cpp` `b5454` の `ggml-large-v3-turbo` と `ggml-base` で、代替は `speech.cpp` の `qwen3-asr-0.6b` です。

字幕翻訳は直前の確定セグメント（最大 2 件）を文脈としてプロンプトへ渡します。字幕開始時に字幕スタイル（位置・文字サイズ・文字色・原文表示）がオーバーレイへ渡り、設定を変更すると表示中のオーバーレイへ即時反映されます。

### 状態とデータの保存先

Linux での保存先は次のパスです。

- 状態: `~/.local/state/local-translate-extension`
- データ: `~/.local/share/local-translate-extension`

Windows での既定の保存先は次のパスです。状態もデータも `%LOCALAPPDATA%\local-translate-extension` 配下で、データは `data` サブディレクトリです。

- 状態: `%LOCALAPPDATA%\local-translate-extension`
- データ: `%LOCALAPPDATA%\local-translate-extension\data`

上書きは今までどおり環境変数 `LOCAL_TRANSLATE_STATE_DIR` と `LOCAL_TRANSLATE_DATA_DIR` を使います。

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

導入スクリプトに `scripts/install-linux.sh` と `scripts/install-companion.ps1` があります。Linux は `uv sync` と Native Messaging 登録を続けて行います。Windows の `scripts/install-companion.ps1` は `uv sync --all-groups` を実行し、`-HostPath` を省略すると `companion\.venv\Scripts\local-translate-host.exe` を使います。HKCU への Native Messaging 登録の前に確認を行いますが、`-Yes` を付けると確認を飛ばします。なお、導入スクリプトのレジストリ登録そのものは、この作業では実行していません。コードと単体テスト、実測は Windows 実機で行いました。

### 疎通確認

疎通確認には `scripts/smoke_translate.py` を使います。このスクリプトは `0.8B` `Q4_K_M` を同意後に取得し、コンテキスト 512 で `"Hello."` を訳します。既定は今までどおり CPU ですが、`--backend` で Backend を選ぶこともできます。

```bash
cd companion && uv run python ../scripts/smoke_translate.py
# CUDA を指定する場合
cd companion && uv run python ../scripts/smoke_translate.py --backend cuda
```

### テスト

テストは `companion` で `uv run pytest`、拡張で `bun run test`、拡張 UI は `bunx playwright test` です。安定版 Chrome が `--load-extension` を無視するため、Playwright は CDP の `Extensions.loadUnpacked` で拡張を読み込みます。Xvfb は Linux だけで起動します（`DISPLAY` がないとき）。

Playwright の options ページテストは既定でモック daemon（`apps/extension/e2e/mock-daemon.mjs` と `apps/extension/e2e/nm-mock-host.py`）を使い、モデル一覧・ライセンス同意・翻訳表示まで確認します。Windows では Playwright の NM モック e2e を既定でスキップし、`LT_E2E_NM_HOST=1` のときだけ HKCU の登録を退避して戻します。実体なしの従来モードは `LT_E2E_DAEMON=real bunx playwright test` で実行します。

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

daemon アイドル時の常駐メモリを実測しました。`GET /v1/status` の `rss_mb` で確認でき、記録は `reports/companion-memory.json` です（38 MB、バージョン 0.1.0、`llama-server` と `speech.cpp` 未起動、CPU 4 コア + Intel 内蔵 GPU + RAM 7.5GB の開発機）。

### 2026-11-10 の Windows + NVIDIA 実測

機種: NVIDIA GeForce RTX 4070 Ti SUPER、VRAM 16376 MiB、ドライバ 610.88、Windows 11。

- 0.8B Q4_K_M、CUDA、コンテキスト 512: モデル起動を含む壁時計 11.69 秒。起動後の `"Hello."` は「こんにちは。」で 0.228 秒。ページ文と入力文も CPU 実測と同じ訳。記録は `reports/translation-cuda-win.json` です。
- 8B Q4_K_M、CUDA、コンテキスト 8192: 読み込み 3.997 秒、VRAM 使用 6469 MiB。同じ3文。
- 31B Q3_K_M、コンテキスト 2048: 読み込み 9.062 秒、VRAM 使用 15953 MiB、空き 109 MiB。同じ3文は 8B と一致。
- 31B Q2_K、コンテキスト 4096: 読み込み 8.012 秒、VRAM 使用 13892 MiB、空き 2170 MiB。2文目は「変更されました」になり、8B の「延期されました」と違います。
- 16GB の段の代替は Q3_K_M のままです。遅すぎず訳が揃ったためです。ただし空きが 109 MiB しかないので字幕と同時には使えません。
- Haas-ja を speech.cpp 0.10.1 の Windows Vulkan 版で、NVIDIA GPU 上で実行しました。FLEURS ja_jp test から asr-ja-bench v1.1 の修正を当てた 30 本。CER 0.0921（`cer.py`。公表の jacer 6.12% とは直接比べられません）。字幕の確定まで平均 0.879 秒（末尾 0.8 秒の無音を含む。30 本すべて翻訳あり）。記録は `reports/speech-cpp-haas-vulkan-win.json` です。
- whisper large-v3-turbo の CUDA 版、言語 ja を明示、同じ 30 本で CER 0.0874。auto のままでは英語として認識されました。記録は `reports/whisper-cuda-win.json` です。
- daemon アイドル時の `rss_mb` は 45。コンソール窓は出ません。daemon を強制終了すると `llama-server.exe` と `speech.exe` は消え、GPU メモリは 786 MiB まで戻りました。記録は `reports/companion-memory-win.json` です。

### 未実施の項目

- Chrome 拡張からの字幕の手動確認は未実施。
- Web Store 提出。
- Linux での GPU 実測は未実施。
- Haas-ja の CER を公表値と直接比べる正規化は未実施。

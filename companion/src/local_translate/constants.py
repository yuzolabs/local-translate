"""Pinned runtimes, host name, and distribution decisions."""

from __future__ import annotations

APP_VERSION = "0.1.0"
NM_HOST_NAME = "com.localtranslate.host"
PRODUCT_NAME = "local-translate-extension"
BUILT_WITH_PLAMO = "Built with PLaMo"

# llama.cpp release that ships llama-server with the flags this daemon passes.
LLAMA_CPP_VERSION = "b12211"
LLAMA_CPP_REPO = "ggml-org/llama.cpp"

# speech.cpp release whose catalog includes haas-ja-v1.0 and silero-vad.
SPEECH_CPP_VERSION = "0.10.1"
SPEECH_CPP_REPO = "nyosegawa/speech.cpp"

# Nightly build named by whisper.cpp v1.9.5. Stable tags ship no binaries.
WHISPER_CPP_VERSION = "b5454"
WHISPER_CPP_REPO = "ggml-org/whisper.cpp"

# Release assets の SHA-256。llama と whisper はピン時に実ファイルをダウンロードして
# 計算し、speech はリリース同梱の SHA256SUMS から採った。ピンがない資産は
# 初回ダウンロード時に digest を記録し、以後の再利用・再インストールで照合する (TOFU)。
RUNTIME_ASSET_SHA256 = {
    f"llama-{LLAMA_CPP_VERSION}-bin-ubuntu-x64.tar.gz": "962924b5431bc1237a9e5d26ba072d37b6fe05f79b495206e7ff6134887624c5",
    f"llama-{LLAMA_CPP_VERSION}-bin-win-cpu-x64.zip": "23729000408c6968faf5e322afc99319d3b8c576ba872ef7663d9b89810090f3",
    f"llama-{LLAMA_CPP_VERSION}-bin-win-cuda-12.4-x64.zip": "e5914eb78199ee0874c4f2febc9fc45b9b3fafbbedd36663f203716b9289a83f",
    f"llama-{LLAMA_CPP_VERSION}-bin-win-vulkan-x64.zip": "20954cbad1a4626436c6ebaed5e48477f68427b075934689f360301ac426a2f1",
    "cudart-llama-bin-win-cuda-12.4-x64.zip": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    "whisper-bin-ubuntu-x64.tar.gz": "a72becf15d7917f990f6313867a52638b82b7f9ef237fb0c980dac56a135781c",
    f"speech-{SPEECH_CPP_VERSION}-linux-x64-cpu.zip": "3396d6b2ec38c2922ff0f24a760f7b45c4ac601788f53f03eb77134393bc768b",
    f"speech-{SPEECH_CPP_VERSION}-linux-x64-vulkan.zip": "187d639813c1836f24bcd22f518b9050f8c10bfd6af3b8198c2d8fb6122165ba",
    f"speech-{SPEECH_CPP_VERSION}-windows-x64-vulkan.zip": "a8fe0e4b4f463c460e7ace9d38c363bec08b254cac5a7f764b7967af35d4fa1a",
}

DEFAULT_IDLE_TIMEOUT_SEC = 600
DEFAULT_TARGET_LANG = "ja"
DEFAULT_SOURCE_LANG = "auto"
DEFAULT_FORM = "polite"
TRAINING_MAX_TOKENS = 32768
LLAMA_SERVER_ALIAS = "plamo-3-translate"

# Distribution decisions. Web Store publication stays deferred; GitHub Releases is the channel.
WEB_STORE_DECISION = "defer"
WEB_STORE_DECISION_REASON = (
    "配布は GitHub Releases を中心にする。"
    "Web Store 公開は GPU 端末での実測とライセンス確認が終わるまで行わない。"
)
WINDOWS_HAAS_DECISION = "speech.cpp"
WINDOWS_HAAS_DECISION_REASON = (
    "Windows の Haas-ja は speech.cpp の Win x64（CPU / Vulkan）を使う。"
    "NeMo 経路と WSL2 は、speech.cpp の品質差が許容できない場合だけ検討する。"
)
MULTILINGUAL_ASR_DECISION = "whisper.cpp"
MULTILINGUAL_ASR_DECISION_REASON = (
    "多言語字幕は whisper.cpp の ggml-large-v3-turbo を併用する。"
    "低メモリ機向けに ggml-base も選べる。"
    "speech.cpp の qwen3-asr-0.6b は同じ実行系で動く代替として残す。"
    "kotoba-whisper は GGML の配布が whisper.cpp バイナリと揃わないため今回は採用しない。"
)

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

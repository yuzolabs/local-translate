"""License summaries shown before the first download. Full text stays on the publisher's site."""

from __future__ import annotations

PLAMO_LICENSE_URL = "https://plamo.preferredai.jp/info/plamo-community-license-en"
HAAS_LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
WHISPER_LICENSE_URL = "https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE"

LICENSES: dict[str, dict[str, str]] = {
    "plamo-community": {
        "id": "plamo-community",
        "name": "PLaMo Community License",
        "url": PLAMO_LICENSE_URL,
        "summary": (
            "個人利用と非商用（学習・趣味・学術を含む）は登録なしで無償。"
            "商用は直近事業年度の収入または売上が 10 億円以下なら登録フォームの提出で無償、"
            "それを超える場合は別途契約が必要。"
            "この拡張はモデル重みを再配布しない。利用前に公式ライセンスを確認すること。"
        ),
    },
    "apache-2.0": {
        "id": "apache-2.0",
        "name": "Apache License 2.0",
        "url": HAAS_LICENSE_URL,
        "summary": "Haas-ja-v1.0 の重みと推論コードは Apache 2.0。同梱の第三者コンポーネントは各ライセンスに従う。",
    },
    "mit": {
        "id": "mit",
        "name": "MIT License",
        "url": WHISPER_LICENSE_URL,
        "summary": "whisper.cpp は MIT。モデル重みのライセンスは各ファイルの配布元に従う。",
    },
    "user-supplied": {
        "id": "user-supplied",
        "name": "ユーザー提供モデル",
        "url": "",
        "summary": "任意の GGUF はユーザーが利用権を持つファイルとして登録する。このソフトは重みを再配布しない。",
    },
}

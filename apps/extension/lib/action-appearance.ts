import type { UiState } from "./types";

/** ツールバーのアイコンの見た目。純粋関数なので単体テストできる形に切り出す。 */
export interface ActionAppearance {
  title: string;
  badge: string;
  badgeColor: string;
}

const DISCONNECTED_COLOR = "#8a8a8a";
const READY_COLOR = "#1f6f6a";
const PENDING_COLOR = "#d0892f";

function translateStateLabel(state: string): string {
  if (state === "ready") return "稼働中";
  if (state === "starting") return "起動中";
  if (state === "not_downloaded") return "未ダウンロード";
  return "待機中";
}

export function actionAppearance(state: UiState): ActionAppearance {
  const translate = state.status?.current?.translate;
  if (!state.connected || !translate) {
    // 未接続ではバッジを消し、ツールチップに未接続と出す。
    return { title: "ローカル翻訳 — 未接続", badge: "", badgeColor: DISCONNECTED_COLOR };
  }
  const label = translateStateLabel(translate.state);
  return {
    title: `ローカル翻訳 — ${translate.selected.label}（${label}）`,
    // バッジは 4 文字程度しか見えないので短い名前だけを出す。未ダウンロードでは出さない。
    badge: translate.state === "not_downloaded" ? "" : translate.selected.short_label,
    badgeColor: translate.state === "ready" ? READY_COLOR : PENDING_COLOR,
  };
}

export async function applyActionAppearance(state: UiState): Promise<void> {
  const appearance = actionAppearance(state);
  await chrome.action.setTitle({ title: appearance.title });
  await chrome.action.setBadgeText({ text: appearance.badge });
  await chrome.action.setBadgeBackgroundColor({ color: appearance.badgeColor });
}

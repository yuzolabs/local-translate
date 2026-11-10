import { describe, expect, it, vi } from "vitest";
import { DaemonClient } from "../lib/daemon-client";

function sseResponse(body: string): Response {
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("daemon client translate", () => {
  it("returns the translated text with the model label and short name from the done event", async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse(
        [
          'event: delta\ndata: {"text":"モ"}\n\n',
          'event: delta\ndata: {"text":"ック"}\n\n',
          'event: done\ndata: {"model_id":"plamo-3-translate-8b-GGUF","model_label":"PLaMo 3 Translate 8B","model_short":"8B"}\n\n',
        ].join(""),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new DaemonClient({ port: 9, token: "t" });
    const deltas: string[] = [];
    const result = await client.translate({ text: "Hello", source_lang: "auto", target_lang: "ja" }, (partial) =>
      deltas.push(partial),
    );
    expect(result.text).toBe("モック");
    expect(result.modelLabel).toBe("PLaMo 3 Translate 8B");
    expect(result.modelShort).toBe("8B");
    expect(deltas).toEqual(["モ", "モック"]);
    vi.unstubAllGlobals();
  });

  it("throws on error events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse('event: error\ndata: {"code":"x","message":"翻訳に失敗しました"}\n\n')),
    );
    const client = new DaemonClient({ port: 9, token: "t" });
    await expect(client.translate({ text: "Hi", source_lang: "auto", target_lang: "ja" }, () => undefined)).rejects.toThrow(
      "翻訳に失敗しました",
    );
    vi.unstubAllGlobals();
  });
});

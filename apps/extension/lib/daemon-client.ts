import { parseSseChunk } from "./sse-parser";
import type { Caption, Settings } from "./types";

export interface DaemonEndpoint {
  port: number;
  token: string;
}

export class DaemonClient {
  constructor(private endpoint: DaemonEndpoint) {}

  private url(path: string): string {
    return `http://127.0.0.1:${this.endpoint.port}${path}`;
  }

  private headers(json = false): HeadersInit {
    return {
      Authorization: `Bearer ${this.endpoint.token}`,
      ...(json ? { "Content-Type": "application/json" } : {}),
    };
  }

  async getJson<T>(path: string): Promise<T> {
    const response = await fetch(this.url(path), { headers: this.headers() });
    if (!response.ok) throw new Error(await errorMessage(response));
    return response.json() as Promise<T>;
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(this.url(path), {
      method: "POST",
      headers: this.headers(true),
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
    return response.json() as Promise<T>;
  }

  async translate(
    body: {
      text: string;
      source_lang: string;
      target_lang: string;
      form?: string;
      glossary?: Settings["glossary"];
      context?: string;
      model_id?: string;
    },
    onDelta: (text: string) => void,
  ): Promise<string> {
    const response = await fetch(this.url("/api/translate"), {
      method: "POST",
      headers: this.headers(true),
      body: JSON.stringify({ ...body, stream: true }),
    });
    if (!response.ok || !response.body) throw new Error(await errorMessage(response));
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let combined = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parsed = parseSseChunk(buffer);
      buffer = parsed.rest;
      for (const event of parsed.events) {
        const payload = JSON.parse(event.data) as { text?: string; message?: string };
        if (event.event === "delta" && payload.text) {
          combined += payload.text;
          onDelta(combined);
        }
        if (event.event === "error") throw new Error(payload.message ?? "翻訳に失敗しました");
      }
    }
    return combined;
  }

  subtitleSocket(onCaption: (caption: Caption) => void): WebSocket {
    const socket = new WebSocket(`ws://127.0.0.1:${this.endpoint.port}/api/subtitles`);
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ type: "auth", token: this.endpoint.token }));
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return;
      const payload = JSON.parse(event.data) as Caption;
      if (typeof payload.seq === "number") onCaption(payload);
    });
    return socket;
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: { message?: string } };
    return body.error?.message ?? `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

import type { GlossaryEntry } from "./types";

export interface TranslationCacheKeyInput {
  text: string;
  sourceLang: string;
  targetLang: string;
  modelId: string;
  form: string;
  glossary: GlossaryEntry[];
  context?: string;
}

export interface CacheStorage {
  read(): Promise<Record<string, string>>;
  write(entries: Record<string, string>): Promise<void>;
}

export async function translationCacheKey(input: TranslationCacheKeyInput): Promise<string> {
  const payload = JSON.stringify({
    text: input.text,
    sourceLang: input.sourceLang,
    targetLang: input.targetLang,
    modelId: input.modelId,
    form: input.form,
    glossary: input.glossary,
    context: input.context ?? "",
  });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export class LruTranslationCache {
  private memory = new Map<string, string>();
  private loaded = false;

  constructor(
    private maxEntries: number,
    private storage: CacheStorage,
  ) {}

  async get(key: string): Promise<string | null> {
    await this.hydrate();
    const value = this.memory.get(key);
    if (value === undefined) return null;
    this.memory.delete(key);
    this.memory.set(key, value);
    return value;
  }

  async set(key: string, value: string): Promise<void> {
    await this.hydrate();
    if (this.memory.has(key)) this.memory.delete(key);
    this.memory.set(key, value);
    while (this.memory.size > this.maxEntries) {
      const oldest = this.memory.keys().next().value;
      if (oldest === undefined) break;
      this.memory.delete(oldest);
    }
    await this.storage.write(Object.fromEntries(this.memory));
  }

  private async hydrate(): Promise<void> {
    if (this.loaded) return;
    const stored = await this.storage.read();
    for (const [key, value] of Object.entries(stored)) this.memory.set(key, value);
    this.loaded = true;
  }
}

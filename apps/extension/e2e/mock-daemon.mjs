// In-process mock daemon for the E2E tests. Talks the same routes as the real
// companion so the extension UI can be exercised without Python or models.
import { createServer } from "node:http";

const port = Number(process.env.LT_MOCK_PORT || 9639);
const token = process.env.LT_MOCK_TOKEN || "mock-token";

const settings = {
  target_lang: "ja",
  source_lang: "auto",
  form: "polite",
  glossary: [],
  gpu_backend: "auto",
  n_gpu_layers: null,
  context_size: null,
  parallel: null,
  idle_timeout_sec: 600,
  active_translate_model: "plamo-3-translate-0.8b-GGUF",
  active_asr_model: "Haas-ja-v1.0",
  asr_engine: "haas",
  subtitle_backend: "speech-serve",
  translate_interim: false,
  subtitle_style: {
    font_size_px: 18,
    text_color: "#f4f7f6",
    background: "rgba(20, 32, 43, 0.78)",
    position: "bottom",
    show_original: true,
  },
  site_rules: [],
  experimental_igpu: false,
  whisper_model: "whisper-base",
};

const models = [
  {
    id: "plamo-3-translate-0.8b-GGUF",
    kind: "translate",
    format: "GGUF",
    size_gb: 0.57,
    quant: "Q4_K_M",
    status: "ready",
    progress: 1,
    consented: true,
    license_id: "plamo-community",
    preset: true,
    runtime: "llama.cpp",
    repo: "pfnet/plamo-3-translate-0.8b-GGUF",
    filename: "plamo-3-translate-0.8b-Q4_K_M.gguf",
    catalog_name: "",
    note: "モックの開発標準モデル",
    active: true,
  },
  {
    id: "plamo-3-translate-8b-GGUF",
    kind: "translate",
    format: "GGUF",
    size_gb: 5.28,
    quant: "Q4_K_M",
    status: "missing",
    progress: 0,
    consented: false,
    license_id: "plamo-community",
    preset: true,
    runtime: "llama.cpp",
    repo: "pfnet/plamo-3-translate-8b-GGUF",
    filename: "plamo-3-translate-8b-Q4_K_M.gguf",
    catalog_name: "",
    note: "モックの 8B",
    active: false,
  },
];

const json = (res, body, status = 200) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

const readBody = async (req) => {
  let body = "";
  for await (const chunk of req) body += chunk;
  try {
    return JSON.parse(body || "{}");
  } catch {
    return {};
  }
};

const server = createServer(async (req, res) => {
  const origin = req.headers.origin ?? "";
  if (origin.startsWith("chrome-extension://")) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Private-Network", "true");
  }
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }
  if ((req.headers.authorization ?? "") !== `Bearer ${token}`) {
    json(res, { error: { code: "unauthorized", message: "トークンが違います" } }, 401);
    return;
  }
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
  const route = url.pathname;

  if (route === "/v1/status" && req.method === "GET") {
    return json(res, {
      version: "0.0.0-mock",
      gpu: {
        backend: "cpu",
        name: "Mock CPU",
        vram_total_mb: null,
        vram_free_mb: null,
        experimental_igpu: false,
        available_ram_mb: 8000,
        vulkan_available: false,
        cuda_available: false,
      },
      rss_mb: 12,
      suggestion: {
        model_id: "plamo-3-translate-0.8b-GGUF",
        context_size: 2048,
        n_gpu_layers: 0,
        parallel: 1,
        reason: "モックの提案です。",
        alternatives: [],
      },
      llama: { state: "ready", model_id: "plamo-3-translate-0.8b-GGUF", port: 1 },
      active_sessions: { subtitles: 0 },
      capabilities: ["translate", "subtitles", "custom-gguf", "cpu"],
      uptime_sec: 1,
    });
  }
  if (route === "/v1/about" && req.method === "GET") {
    return json(res, {
      name: "local-translate-extension",
      version: "0.0.0-mock",
      built_with: "Built with PLaMo",
      web_store: { decision: "defer", reason: "モック: Web Store は見送りです。" },
      windows_haas: { decision: "speech.cpp", reason: "モック: speech.cpp を使います。" },
      multilingual_asr: { decision: "whisper.cpp", reason: "モック: whisper.cpp を併用します。" },
    });
  }
  if (route === "/v1/settings") {
    if (req.method === "GET") return json(res, settings);
    Object.assign(settings, await readBody(req));
    return json(res, settings);
  }
  if (route === "/v1/models" && req.method === "GET") {
    return json(res, { models });
  }
  if (route.startsWith("/v1/models/") && route.endsWith("/license") && req.method === "GET") {
    return json(res, {
      id: "plamo-community",
      name: "PLaMo Community License",
      url: "https://example.invalid/plamo-license",
      summary: "モックのライセンス概要です。",
      fetched_text: null,
      model_id: route.split("/")[3],
    });
  }
  if (route.startsWith("/v1/models/") && route.endsWith("/consent") && req.method === "POST") {
    const modelId = route.split("/")[3];
    const row = models.find((item) => item.id === modelId);
    if (row) row.consented = true;
    return json(res, { model_id: modelId, consent: { license_id: row?.license_id ?? "plamo-community" } });
  }
  if (route.startsWith("/v1/models/") && route.endsWith("/download") && req.method === "POST") {
    return json(res, { status: "started", model_id: route.split("/")[3] }, 202);
  }
  if (route.startsWith("/v1/models/") && route.endsWith("/activate") && req.method === "POST") {
    return json(res, { status: "ready", model_id: route.split("/")[3], llama: { state: "ready" } });
  }
  if (route === "/v1/models" && req.method === "POST") {
    return json(res, { status: "ready", model_id: "custom-mock" }, 201);
  }
  if (route === "/v1/control" && req.method === "POST") {
    return json(res, { status: "mock" });
  }
  if (route === "/api/translate" && req.method === "POST") {
    await readBody(req);
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    res.write(`event: delta\ndata: ${JSON.stringify({ text: "モック" })}\n\n`);
    res.write(`event: delta\ndata: ${JSON.stringify({ text: "訳" })}\n\n`);
    res.write(`event: done\ndata: ${JSON.stringify({ model_id: settings.active_translate_model })}\n\n`);
    res.end();
    return;
  }
  json(res, { error: { code: "not_found", message: "モックにこの経路はありません" } }, 404);
});

server.listen(port, "127.0.0.1", () => {
  console.log(`mock daemon listening on 127.0.0.1:${port}`);
});

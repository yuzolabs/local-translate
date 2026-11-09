let socket: WebSocket | null = null;
let audioContext: AudioContext | null = null;
let tabId = 0;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "offscreen:start") {
    tabId = message.tabId;
    void chrome.storage.session.set({ subtitleTabId: tabId });
    void startCapture(message.streamId, message.port, message.token)
      .then(() => sendResponse({ ok: true }))
      .catch((error: Error) => sendResponse({ error: error.message }));
    return true;
  }
  if (message.type === "offscreen:stop") {
    stopCapture();
    sendResponse({ ok: true });
  }
  return undefined;
});

async function startCapture(streamId: string, port: number, token: string): Promise<void> {
  stopCapture();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    } as MediaTrackConstraints,
    video: false,
  });
  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule(chrome.runtime.getURL("audio-capture-worklet.js"));
  const source = audioContext.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(audioContext, "pcm-capture");
  source.connect(node);
  source.connect(audioContext.destination);
  socket = new WebSocket(`ws://127.0.0.1:${port}/api/subtitles`);
  socket.addEventListener("open", () => {
    socket?.send(JSON.stringify({ type: "auth", token }));
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    const caption = JSON.parse(event.data);
    if (typeof caption.seq === "number") {
      chrome.runtime.sendMessage({ type: "caption", caption, tabId });
    }
  });
  node.port.onmessage = (event) => {
    const samples = event.data as Int16Array;
    if (socket?.readyState === WebSocket.OPEN) socket.send(samples.buffer);
  };
}

function stopCapture(): void {
  socket?.close();
  socket = null;
  void audioContext?.close();
  audioContext = null;
}

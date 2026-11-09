class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.outputIndex = 0;
    this.buffer = new Float32Array(0);
    this.bufferStart = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !channel.length) return true;
    const merged = new Float32Array(this.buffer.length + channel.length);
    merged.set(this.buffer);
    merged.set(channel, this.buffer.length);
    this.buffer = merged;
    const output = [];
    for (;;) {
      const position = this.outputIndex * this.ratio;
      const left = Math.floor(position) - this.bufferStart;
      const right = left + 1;
      if (right >= this.buffer.length) break;
      const frac = position - Math.floor(position);
      const sample = this.buffer[left] + (this.buffer[right] - this.buffer[left]) * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      output.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      this.outputIndex += 1;
    }
    const consumed = Math.min(this.buffer.length, Math.max(0, Math.floor(this.outputIndex * this.ratio) - this.bufferStart));
    this.bufferStart += consumed;
    if (consumed) this.buffer = this.buffer.slice(consumed);
    if (output.length) this.port.postMessage(Int16Array.from(output));
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.cursor = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !channel.length) return true;
    const output = [];
    for (let index = 0; index < channel.length; index += 1) {
      const next = this.cursor + this.ratio;
      if (Math.floor(next) === Math.floor(this.cursor) && index !== 0) {
        this.cursor = next;
        continue;
      }
      const position = index;
      const left = Math.max(0, Math.floor(position));
      const right = Math.min(channel.length - 1, left + 1);
      const frac = position - left;
      const sample = channel[left] + (channel[right] - channel[left]) * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      output.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      this.cursor = next;
    }
    if (output.length) this.port.postMessage(Int16Array.from(output));
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);

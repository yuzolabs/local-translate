import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

interface FakePort {
  messages: Int16Array[];
  postMessage(data: Int16Array): void;
}

interface FakeProcessor {
  port: FakePort;
  process(inputs: Float32Array[][]): boolean;
}

class FakeAudioWorkletProcessor {
  port: FakePort;
  constructor() {
    this.port = {
      messages: [],
      postMessage(data: Int16Array) {
        this.messages.push(data);
      },
    };
  }
}

function loadProcessor(): new () => FakeProcessor {
  const workletPath = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "audio-capture-worklet.js");
  const source = readFileSync(workletPath, "utf8");
  const registerProcessor = vi.fn();
  new Function("sampleRate", "AudioWorkletProcessor", "registerProcessor", source)(
    48000,
    FakeAudioWorkletProcessor,
    registerProcessor,
  );
  expect(registerProcessor).toHaveBeenCalledWith("pcm-capture", expect.any(Function));
  return registerProcessor.mock.calls[0][1] as new () => FakeProcessor;
}

function collectSamples(processor: FakeProcessor): number[] {
  const samples: number[] = [];
  for (const chunk of processor.port.messages) {
    for (const value of chunk) {
      samples.push(value / (value < 0 ? 0x8000 : 0x7fff));
    }
  }
  return samples;
}

describe("audio capture worklet", () => {
  it("decimates one second of 48kHz tab audio to about 16000 samples", () => {
    const Processor = loadProcessor();
    const processor = new Processor();
    const input = new Float32Array(48000);
    for (let i = 0; i < input.length; i += 1) input[i] = Math.sin((2 * Math.PI * 440 * i) / 48000);
    for (let offset = 0; offset < input.length; offset += 128) {
      const handled = processor.process([[input.subarray(offset, offset + 128)]]);
      expect(handled).toBe(true);
    }
    const total = processor.port.messages.reduce((sum, chunk) => sum + chunk.length, 0);
    expect(total).toBeGreaterThan(15500);
    expect(total).toBeLessThan(16500);
  });

  it("keeps the 440Hz sine period across quantum boundaries", () => {
    const Processor = loadProcessor();
    const processor = new Processor();
    const input = new Float32Array(48000);
    for (let i = 0; i < input.length; i += 1) input[i] = Math.sin((2 * Math.PI * 440 * i) / 48000);
    for (let offset = 0; offset < input.length; offset += 128) {
      processor.process([[input.subarray(offset, offset + 128)]]);
    }
    const wave = collectSamples(processor);
    const expectedPeriod = 16000 / 440;
    const crossings: number[] = [];
    for (let i = 1; i < wave.length; i += 1) {
      if (wave[i - 1] <= 0 && wave[i] > 0) crossings.push(i);
    }
    expect(crossings.length).toBeGreaterThanOrEqual(430);
    expect(crossings.length).toBeLessThanOrEqual(450);
    for (let i = 1; i < crossings.length; i += 1) {
      const spacing = crossings[i] - crossings[i - 1];
      expect(Math.abs(spacing - expectedPeriod)).toBeLessThan(1.5);
    }
  });
});

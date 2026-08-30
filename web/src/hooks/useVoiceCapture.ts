import { useCallback, useRef, useState } from "react";

const TARGET_RATE = 16000;

const WORKLET = `
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel);
    return true;
  }
}
registerProcessor("pcm-capture", CaptureProcessor);
`;

function floatToPcm16(input: Float32Array, inRate: number, outRate = TARGET_RATE): Int16Array {
  const ratio = inRate / outRate;
  const n = Math.max(1, Math.floor(input.length / ratio));
  const out = new Int16Array(n);
  for (let i = 0; i < n; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)] ?? 0));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

function encodeWav(pcm: Int16Array, sampleRate: number): Blob {
  const bytes = pcm.byteLength;
  const buffer = new ArrayBuffer(44 + bytes);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, bytes, true);
  new Uint8Array(buffer, 44).set(new Uint8Array(pcm.buffer, pcm.byteOffset, bytes));
  return new Blob([buffer], { type: "audio/wav" });
}

export function useVoiceCapture() {
  const ctxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const nodeRef = useRef<AudioWorkletNode | ScriptProcessorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const rateRef = useRef(48000);
  const [recording, setRecording] = useState(false);
  const [supported] = useState(() => typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia);

  const start = useCallback(async () => {
    if (recording) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
    streamRef.current = stream;
    const ctx = new AudioContext();
    ctxRef.current = ctx;
    rateRef.current = ctx.sampleRate;
    chunksRef.current = [];
    const source = ctx.createMediaStreamSource(stream);
    sourceRef.current = source;

    try {
      const blob = new Blob([WORKLET], { type: "application/javascript" });
      const url = URL.createObjectURL(blob);
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(ctx, "pcm-capture");
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        chunksRef.current.push(new Float32Array(ev.data));
      };
      source.connect(node);
      node.connect(ctx.destination);
      nodeRef.current = node;
    } catch {
      const node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (ev) => {
        chunksRef.current.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
      };
      source.connect(node);
      node.connect(ctx.destination);
      nodeRef.current = node;
    }
    setRecording(true);
  }, [recording]);

  const stop = useCallback(async (): Promise<Blob | null> => {
    const chunks = chunksRef.current;
    chunksRef.current = [];
    nodeRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    await ctxRef.current?.close().catch(() => undefined);
    nodeRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    ctxRef.current = null;
    setRecording(false);
    if (!chunks.length) return null;
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    return encodeWav(floatToPcm16(merged, rateRef.current), TARGET_RATE);
  }, []);

  return { supported, recording, start, stop };
}

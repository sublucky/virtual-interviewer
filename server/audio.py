"""PCM16 / WAV 互转，供 Omni Realtime 与 LiveTalking /humanaudio 使用。"""

from __future__ import annotations

import io
import struct
import wave


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(wav: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        frames = _lin2pcm16(frames, width)
    if channels > 1:
        frames = _to_mono(frames, channels)
    return frames, rate


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = memoryview(pcm).cast("h")
    n_out = max(1, int(len(samples) * dst_rate / src_rate))
    out = bytearray(n_out * 2)
    ratio = src_rate / dst_rate
    for i in range(n_out):
        src = min(int(i * ratio), len(samples) - 1)
        struct.pack_into("<h", out, i * 2, samples[src])
    return bytes(out)


def load_user_pcm16(data: bytes, *, target_rate: int = 16000) -> bytes:
    """浏览器上传的 WAV 或裸 PCM16 → Omni 需要的 16kHz mono PCM16。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        pcm, rate = wav_to_pcm16(data)
        return resample_pcm16(pcm, rate, target_rate)
    return data


def _lin2pcm16(frames: bytes, width: int) -> bytes:
    if width == 1:
        return b"".join(struct.pack("<h", (b - 128) * 256) for b in frames)
    if width == 2:
        return frames
    if width == 4:
        samples = memoryview(frames).cast("i")
        return b"".join(struct.pack("<h", max(-32768, min(32767, s >> 16))) for s in samples)
    raise ValueError(f"不支持的采样宽度: {width}")


def _to_mono(pcm: bytes, channels: int) -> bytes:
    samples = memoryview(pcm).cast("h")
    n = len(samples) // channels
    out = bytearray(n * 2)
    for i in range(n):
        acc = sum(samples[i * channels + c] for c in range(channels)) // channels
        struct.pack_into("<h", out, i * 2, acc)
    return bytes(out)

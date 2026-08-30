import { useCallback, useRef, useState } from "react";
import { openRtc } from "../api";

type Status = "idle" | "connecting" | "connected" | "failed" | "skipped";

/**
 * WHEP 拉流：只收数字人的音视频，不推本地流。
 * 连接失败或未启用数字人时不阻塞面试，上层退化为纯文本模式。
 */
export function useWebRTC() {
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");

  const disconnect = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setStatus("idle");
    setError("");
  }, []);

  const connect = useCallback(async (sessionId: string, video: HTMLVideoElement) => {
    if (!sessionId) {
      setStatus("skipped");
      return;
    }
    pcRef.current?.close();
    setStatus("connecting");
    setError("");
    const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
    pcRef.current = pc;

    const stream = new MediaStream();
    video.srcObject = stream;
    pc.addEventListener("track", (event) => stream.addTrack(event.track));
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIce(pc);
      if (pcRef.current !== pc) return;
      const answer = await openRtc(sessionId, pc.localDescription!.sdp);
      if (pcRef.current !== pc) return;
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
      setStatus("connected");
    } catch (e) {
      if (pcRef.current === pc) {
        pc.close();
        pcRef.current = null;
        setStatus("failed");
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  }, []);

  const skip = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setStatus("skipped");
    setError("");
  }, []);

  return { status, error, connect, disconnect, skip };
}

function waitForIce(pc: RTCPeerConnection, timeoutMs = 3000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, timeoutMs);
  });
}

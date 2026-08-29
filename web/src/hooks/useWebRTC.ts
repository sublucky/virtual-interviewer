import { useCallback, useRef, useState } from "react";
import { openRtc } from "../api";

type Status = "idle" | "connecting" | "connected" | "failed";

/**
 * WHEP 拉流：只收数字人的音视频，不推本地流。
 * 连接失败不阻塞面试，上层退化为纯文本模式。
 */
export function useWebRTC() {
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");

  const connect = useCallback(async (sessionId: string, video: HTMLVideoElement) => {
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
      const answer = await openRtc(sessionId, pc.localDescription!.sdp);
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
      setStatus("connected");
    } catch (e) {
      pc.close();
      pcRef.current = null;
      setStatus("failed");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const disconnect = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setStatus("idle");
  }, []);

  return { status, error, connect, disconnect };
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
    // 收集不全也要继续，避免弱网下卡在这一步
    setTimeout(done, timeoutMs);
  });
}

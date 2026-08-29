import { useEffect, useRef, useState } from "react";
import type { Turn } from "../types";

interface Props {
  state: string;
  turns: Turn[];
  streaming: string;
  thinking: boolean;
  rtcStatus: string;
  rtcError: string;
  micSupported: boolean;
  listening: boolean;
  partial: string;
  busy: boolean;
  enableRtc: boolean;
  onConnectVideo: (video: HTMLVideoElement) => void;
  onSubmit: (text: string) => void;
  onToggleMic: () => void;
  onEnd: () => void;
}

export function InterviewStage(props: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const connectedRef = useRef(false);
  const [draft, setDraft] = useState("");

  const connect = props.onConnectVideo;
  const enableRtc = props.enableRtc;
  useEffect(() => {
    if (!enableRtc || connectedRef.current || !videoRef.current) return;
    connectedRef.current = true;
    connect(videoRef.current);
  }, [connect, enableRtc]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight });
  }, [props.turns.length, props.streaming]);

  const caption = props.thinking ? "面试官正在组织下一个问题…" : props.streaming || "—";
  const rtcLabel =
    props.rtcStatus === "connecting"
      ? "数字人连接中…"
      : props.rtcStatus === "skipped"
        ? "未启用数字人，当前为文字模式"
        : "未连接数字人，当前为文字模式";

  return (
    <section className="stage">
      <div className="video-wrap">
        <video ref={videoRef} autoPlay playsInline />
        {props.rtcStatus !== "connected" && (
          <div className="video-fallback">
            {rtcLabel}
            {props.rtcError && <small>{props.rtcError}</small>}
          </div>
        )}
        <div className="caption">{caption}</div>
      </div>

      <div className="transcript" ref={transcriptRef} aria-live="polite">
        {props.turns.map((turn, i) => (
          <div key={i} className={`turn ${turn.role}`}>
            <span>{turn.role === "interviewer" ? "面试官" : "我"}</span>
            <p>{turn.text}</p>
          </div>
        ))}
        {props.streaming && (
          <div className="turn interviewer streaming">
            <span>面试官</span>
            <p>{props.streaming}</p>
          </div>
        )}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          const text = draft.trim();
          if (!text || props.busy) return;
          setDraft("");
          props.onSubmit(text);
        }}
      >
        <textarea
          rows={2}
          value={props.listening ? props.partial || "（正在听…）" : draft}
          readOnly={props.listening}
          disabled={props.busy}
          placeholder={props.busy ? "面试官发言中…" : "输入回答，或点麦克风口述"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="composer-actions">
          {props.micSupported && (
            <button
              type="button"
              className={props.listening ? "mic active" : "mic"}
              onClick={props.onToggleMic}
              disabled={props.busy}
            >
              {props.listening ? "停止" : "口述"}
            </button>
          )}
          <button type="submit" disabled={props.busy || !draft.trim()}>
            发送
          </button>
          <button type="button" className="ghost" onClick={props.onEnd} disabled={props.busy}>
            结束面试
          </button>
        </div>
        <span className="muted state">{props.state}</span>
      </form>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  fetchDebugHistory,
  fetchMeta,
  reportFromEvent,
  streamTurn,
  toggleDebug,
} from "./api";
import { DebugPanel } from "./components/DebugPanel";
import { InterviewStage } from "./components/InterviewStage";
import { ReportView } from "./components/ReportView";
import { SetupForm } from "./components/SetupForm";
import { useSpeech } from "./hooks/useSpeech";
import { useWebRTC } from "./hooks/useWebRTC";
import type {
  DebugEvent,
  InterviewConfig,
  Report,
  ServiceMeta,
  StreamEvent,
  Turn,
} from "./types";

type Phase = "setup" | "interview" | "report";

const EMPTY_CONFIG: InterviewConfig = {
  role: "后端工程师",
  company: "",
  jd: "",
  resume: "",
  style: "probe",
  rounds: 8,
  debug: true,
};

export default function App() {
  const [phase, setPhase] = useState<Phase>("setup");
  const [meta, setMeta] = useState<ServiceMeta | null>(null);
  const [config, setConfig] = useState<InterviewConfig>(EMPTY_CONFIG);
  const [sessionId, setSessionId] = useState("");
  const [debugOn, setDebugOn] = useState(false);
  const [state, setState] = useState("Created");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState("");
  const [thinking, setThinking] = useState(false);
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rtc = useWebRTC();
  const sessionRef = useRef("");
  const inFlightRef = useRef(false);
  const skipRtc = rtc.skip;
  const connectRtc = rtc.connect;

  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const patchConfig = useCallback((next: Partial<InterviewConfig>) => {
    setConfig((c) => ({ ...c, ...next }));
  }, []);

  const handleEvent = useCallback((event: StreamEvent) => {
    switch (event.event) {
      case "thinking":
        setThinking(true);
        break;
      case "delta":
        setThinking(false);
        setStreaming((s) => s + event.text);
        break;
      case "done":
        setThinking(false);
        setStreaming("");
        setState(event.state);
        if (event.text.trim()) {
          setTurns((t) => [...t, { role: "interviewer", text: event.text }]);
        }
        break;
      case "evaluating":
        setThinking(false);
        setState("Evaluating");
        break;
      case "report": {
        const next = reportFromEvent(event);
        if (next) setReport(next);
        setState("Done");
        setPhase("report");
        break;
      }
      case "debug":
        setDebugEvents((events) => [...events, event as unknown as DebugEvent].slice(-500));
        if (event.type === "state_change" && typeof event.to === "string") {
          setState(event.to);
        }
        break;
      case "error":
        setThinking(false);
        setError(event.message);
        break;
      default:
        break;
    }
  }, []);

  const run = useCallback(
    async (payload: { text?: string; kickoff?: boolean; end?: boolean }) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      setBusy(true);
      setError("");
      try {
        await streamTurn(sessionRef.current, payload, handleEvent);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        inFlightRef.current = false;
        setBusy(false);
        setThinking(false);
      }
    },
    [handleEvent],
  );

  const start = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setBusy(true);
    setError("");
    setTurns([]);
    setDebugEvents([]);
    setStreaming("");
    setThinking(false);
    try {
      const info = await createSession(config);
      sessionRef.current = info.session_id;
      setSessionId(info.session_id);
      setDebugOn(info.debug);
      setState(info.state);
      setPhase("interview");
      if (info.debug) {
        const history = await fetchDebugHistory(info.session_id);
        setDebugEvents(history.events.slice(-500));
      }
      if (!meta?.avatar.ok) skipRtc();
      // kickoff 走同一把 inFlight 锁：先释放再交给 run
      inFlightRef.current = false;
      await run({ kickoff: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      inFlightRef.current = false;
      setBusy(false);
    }
  }, [config, meta, run, skipRtc]);

  const submit = useCallback(
    (text: string) => {
      if (inFlightRef.current || busy) return;
      setTurns((t) => [...t, { role: "candidate", text }]);
      void run({ text });
    },
    [busy, run],
  );

  const speech = useSpeech(submit);

  const handleToggleDebug = useCallback(
    async (enabled: boolean) => {
      if (!sessionRef.current) return;
      await toggleDebug(sessionRef.current, enabled);
      setDebugOn(enabled);
      if (enabled) {
        const history = await fetchDebugHistory(sessionRef.current);
        setDebugEvents(history.events.slice(-500));
      }
    },
    [],
  );

  const restart = () => {
    rtc.disconnect();
    sessionRef.current = "";
    setSessionId("");
    setTurns([]);
    setDebugEvents([]);
    setReport(null);
    setStreaming("");
    setThinking(false);
    setError("");
    setPhase("setup");
    fetchMeta().then(setMeta).catch(() => undefined);
  };

  const connectVideo = useCallback(
    (video: HTMLVideoElement) => {
      void connectRtc(sessionId, video);
    },
    [connectRtc, sessionId],
  );
  const enableRtc = Boolean(meta?.avatar.ok) && phase === "interview" && Boolean(sessionId);

  return (
    <main className={debugOn && phase !== "setup" ? "app with-debug" : "app"}>
      <div className="main-col">
        {error && <div className="error">{error}</div>}

        {phase === "setup" && (
          <SetupForm busy={busy} meta={meta} config={config} onChange={patchConfig} onStart={() => void start()} />
        )}

        {phase === "interview" && (
          <InterviewStage
            state={state}
            turns={turns}
            streaming={streaming}
            thinking={thinking}
            rtcStatus={rtc.status}
            rtcError={rtc.error}
            micSupported={speech.supported}
            listening={speech.listening}
            partial={speech.partial}
            busy={busy}
            enableRtc={enableRtc}
            onConnectVideo={connectVideo}
            onSubmit={submit}
            onToggleMic={() => (speech.listening ? speech.stop() : speech.start())}
            onEnd={() => void run({ end: true })}
          />
        )}

        {phase === "report" && report && <ReportView report={report} onRestart={restart} />}
      </div>

      {debugOn && phase !== "setup" && (
        <DebugPanel events={debugEvents} state={state} onToggle={(enabled) => void handleToggleDebug(enabled)} />
      )}
    </main>
  );
}

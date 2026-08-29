import { useCallback, useRef, useState } from "react";
import { createSession, streamTurn } from "./api";
import { DebugPanel } from "./components/DebugPanel";
import { InterviewStage } from "./components/InterviewStage";
import { ReportView } from "./components/ReportView";
import { SetupForm } from "./components/SetupForm";
import { useSpeech } from "./hooks/useSpeech";
import { useWebRTC } from "./hooks/useWebRTC";
import type { DebugEvent, InterviewConfig, Report, StreamEvent, Turn } from "./types";

type Phase = "setup" | "interview" | "report";

export default function App() {
  const [phase, setPhase] = useState<Phase>("setup");
  const [sessionId, setSessionId] = useState("");
  const [debugOn, setDebugOn] = useState(false);
  const [state, setState] = useState("Created");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState("");
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rtc = useWebRTC();
  const sessionRef = useRef("");

  const handleEvent = useCallback((event: StreamEvent) => {
    switch (event.event) {
      case "delta":
        setStreaming((s) => s + event.text);
        break;
      case "done":
        setStreaming("");
        setState(event.state);
        if (event.text.trim()) {
          setTurns((t) => [...t, { role: "interviewer", text: event.text }]);
        }
        break;
      case "evaluating":
        setState("Evaluating");
        break;
      case "report":
        setReport(event as unknown as Report);
        setState("Done");
        setPhase("report");
        break;
      case "debug":
        setDebugEvents((events) => [...events, event as unknown as DebugEvent].slice(-500));
        break;
      case "error":
        setError(event.message);
        break;
      default:
        break;
    }
  }, []);

  const run = useCallback(
    async (payload: { text?: string; kickoff?: boolean; end?: boolean }) => {
      setBusy(true);
      setError("");
      try {
        await streamTurn(sessionRef.current, payload, handleEvent);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [handleEvent],
  );

  const start = useCallback(
    async (config: InterviewConfig) => {
      setBusy(true);
      setError("");
      try {
        const info = await createSession(config);
        sessionRef.current = info.session_id;
        setSessionId(info.session_id);
        setDebugOn(info.debug);
        setState(info.state);
        setPhase("interview");
        await run({ kickoff: true });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    },
    [run],
  );

  const submit = useCallback(
    (text: string) => {
      setTurns((t) => [...t, { role: "candidate", text }]);
      void run({ text });
    },
    [run],
  );

  const speech = useSpeech(submit);

  const restart = () => {
    rtc.disconnect();
    sessionRef.current = "";
    setSessionId("");
    setTurns([]);
    setDebugEvents([]);
    setReport(null);
    setStreaming("");
    setPhase("setup");
  };

  return (
    <main className={debugOn && phase !== "setup" ? "app with-debug" : "app"}>
      <div className="main-col">
        {error && <div className="error">{error}</div>}

        {phase === "setup" && <SetupForm busy={busy} onStart={start} />}

        {phase === "interview" && (
          <InterviewStage
            state={state}
            turns={turns}
            streaming={streaming}
            rtcStatus={rtc.status}
            rtcError={rtc.error}
            micSupported={speech.supported}
            listening={speech.listening}
            partial={speech.partial}
            busy={busy}
            onConnectVideo={(video) => void rtc.connect(sessionId, video)}
            onSubmit={submit}
            onToggleMic={() => (speech.listening ? speech.stop() : speech.start())}
            onEnd={() => void run({ end: true })}
          />
        )}

        {phase === "report" && report && <ReportView report={report} onRestart={restart} />}
      </div>

      {debugOn && phase !== "setup" && <DebugPanel events={debugEvents} state={state} />}
    </main>
  );
}

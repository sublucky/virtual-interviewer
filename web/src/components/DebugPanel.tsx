import { useMemo, useState } from "react";
import type { DebugEvent } from "../types";

const TABS = ["状态机", "数据流", "通讯", "延迟", "RAG", "日志"] as const;
type Tab = (typeof TABS)[number];

const MACHINE = [
  "Created",
  "Opening",
  "Listening",
  "Thinking",
  "Speaking",
  "Closing",
  "Evaluating",
  "Done",
];

/** 架构 §4 延迟预算（ms），超时标红 */
const BUDGET: Record<string, number> = {
  took_ms: 200,
  llm_first_token_ms: 400,
  first_sentence_ms: 700,
  speak_total_ms: 1500,
  evaluate_ms: 8000,
};

const FLOW = [
  { key: "asr", label: "ASR", source: "浏览器" },
  { key: "rag", label: "RAG", source: "retriever" },
  { key: "llm", label: "LLM", source: "llm" },
  { key: "tts", label: "TTS", source: "livetalking" },
  { key: "avatar", label: "数字人", source: "livetalking" },
] as const;

export function DebugPanel({
  events,
  state,
  onToggle,
}: {
  events: DebugEvent[];
  state: string;
  onToggle?: (enabled: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>("状态机");
  const [collapsed, setCollapsed] = useState(false);

  const filtered = useMemo(() => eventsFor(tab, events).slice(-80).reverse(), [events, tab]);
  const latestLatency = useMemo(
    () => events.filter((e) => e.type === "latency").slice(-1)[0],
    [events],
  );
  const lamps = useMemo(() => flowLamps(events), [events]);

  if (collapsed) {
    return (
      <aside className="debug collapsed">
        <button type="button" className="ghost" onClick={() => setCollapsed(false)}>
          Debug
        </button>
      </aside>
    );
  }

  return (
    <aside className="debug">
      <header>
        <strong>Debug</strong>
        <span className="pill">{state}</span>
        <span className="muted">{events.length} 事件</span>
        <span className="debug-actions">
          {onToggle && (
            <button type="button" className="ghost tiny" onClick={() => onToggle(false)}>
              关闭
            </button>
          )}
          <button type="button" className="ghost tiny" onClick={() => setCollapsed(true)}>
            收起
          </button>
        </span>
      </header>

      {latestLatency && (
        <div className="metrics">
          {Object.entries(latestLatency)
            .filter(([k, v]) => k.endsWith("_ms") && typeof v === "number" && v >= 0)
            .map(([k, v]) => {
              const ms = v as number;
              const budget = BUDGET[k];
              const over = budget != null && ms > budget;
              return (
                <span key={k} className={over ? "metric over" : "metric"}>
                  {k.replace(/_ms$/, "")} <b>{ms}ms</b>
                  {budget != null && <em>/{budget}</em>}
                </span>
              );
            })}
        </div>
      )}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} type="button" className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {tab === "状态机" && (
        <ol className="machine">
          {MACHINE.map((node) => (
            <li key={node} className={node === state ? "current" : ""}>
              {node}
            </li>
          ))}
        </ol>
      )}

      {tab === "数据流" && (
        <ul className="flow">
          {FLOW.map((step) => {
            const lamp = lamps[step.key];
            return (
              <li key={step.key} className={lamp.status}>
                <i />
                <div>
                  <strong>{step.label}</strong>
                  <small>
                    {step.source} · {lamp.label}
                  </small>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {tab === "RAG" && (
        <ul className="rag-hits">
          {events
            .filter((e) => e.type === "retrieval")
            .slice(-8)
            .reverse()
            .map((event, i) => (
              <li key={`${event.at}-${i}`}>
                <header>
                  <time>{event.at.slice(11, 23)}</time>
                  <span>{String(event.took_ms)}ms</span>
                </header>
                <p className="query">{String(event.query ?? "")}</p>
                <ul>
                  {((event.hits as { id: string; score: number; kind?: string }[]) ?? []).map((hit) => (
                    <li key={hit.id}>
                      <code>{hit.id}</code>
                      <span>{hit.kind}</span>
                      <b>{Number(hit.score).toFixed(3)}</b>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
        </ul>
      )}

      {(tab === "状态机" || tab === "通讯" || tab === "延迟" || tab === "日志") && (
        <ul className="events">
          {filtered.length === 0 && <li className="muted">暂无事件</li>}
          {filtered.map((event, i) => (
            <li key={`${event.at}-${i}`}>
              <time>{event.at.slice(11, 23)}</time>
              <code>{summarize(event)}</code>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function eventsFor(tab: Tab, events: DebugEvent[]): DebugEvent[] {
  if (tab === "数据流" || tab === "RAG") return [];
  const type: Record<Exclude<Tab, "数据流" | "RAG">, DebugEvent["type"]> = {
    状态机: "state_change",
    通讯: "comm",
    延迟: "latency",
    日志: "debug_log",
  };
  return events.filter((e) => e.type === type[tab]);
}

function flowLamps(events: DebugEvent[]): Record<string, { status: string; label: string }> {
  const lastRetrieval = events.filter((e) => e.type === "retrieval").slice(-1)[0];
  const lastLlm = events.filter((e) => e.type === "comm" && e.target === "llm").slice(-1)[0];
  const lastAvatar = events.filter((e) => e.type === "comm" && e.target === "livetalking").slice(-1)[0];
  const lastLog = events.filter((e) => e.type === "debug_log").slice(-1)[0];
  const llmFail = lastLog && String(lastLog.message ?? "").includes("LLM");

  return {
    asr: { status: "idle", label: "浏览器 Web Speech" },
    rag: lastRetrieval
      ? { status: "ok", label: `${lastRetrieval.took_ms}ms · ${((lastRetrieval.hits as unknown[]) ?? []).length} 命中` }
      : { status: "idle", label: "尚未检索" },
    llm: llmFail
      ? { status: "down", label: String(lastLog.message) }
      : lastLlm
        ? { status: lastLlm.status === "error" ? "down" : "ok", label: `${lastLlm.action} ${lastLlm.took_ms}ms` }
        : { status: "idle", label: "等待首 token" },
    tts: lastAvatar
      ? { status: lastAvatar.status === "error" ? "down" : "ok", label: "LiveTalking 内置" }
      : { status: "idle", label: "未推送（文字模式）" },
    avatar: lastAvatar
      ? { status: lastAvatar.status === "error" ? "down" : "ok", label: `${lastAvatar.action} ${lastAvatar.took_ms}ms` }
      : { status: "idle", label: "未连接" },
  };
}

function summarize(event: DebugEvent): string {
  switch (event.type) {
    case "state_change":
      return `${event.from} → ${event.to}${event.reason ? `  (${event.reason})` : ""}`;
    case "retrieval": {
      const hits = (event.hits as { id: string; score: number }[]) ?? [];
      return `${event.took_ms}ms  ${hits.length} 命中  ${hits.map((h) => `${h.id}:${h.score}`).join(", ")}`;
    }
    case "comm":
      return `${event.target}.${event.action}  ${event.took_ms}ms  ${event.status ?? "ok"}`;
    case "latency":
      return Object.entries(event)
        .filter(([k]) => !["type", "at", "event"].includes(k))
        .map(([k, v]) => `${k}=${v}`)
        .join("  ");
    default:
      return String(event.message ?? JSON.stringify(event));
  }
}

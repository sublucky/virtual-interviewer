import { useMemo, useState } from "react";
import type { DebugEvent } from "../types";

const TABS = ["状态机", "检索", "通讯", "延迟", "日志"] as const;
type Tab = (typeof TABS)[number];

const TYPE_BY_TAB: Record<Tab, DebugEvent["type"]> = {
  状态机: "state_change",
  检索: "retrieval",
  通讯: "comm",
  延迟: "latency",
  日志: "debug_log",
};

/** Debug 面板（需求 §4.3）：事件搭在面试 SSE 上，不额外建长连接。 */
export function DebugPanel({ events, state }: { events: DebugEvent[]; state: string }) {
  const [tab, setTab] = useState<Tab>("状态机");
  const filtered = useMemo(
    () => events.filter((e) => e.type === TYPE_BY_TAB[tab]).slice(-80).reverse(),
    [events, tab],
  );
  const latest = useMemo(
    () => events.filter((e) => e.type === "latency").slice(-1)[0],
    [events],
  );

  return (
    <aside className="debug">
      <header>
        <strong>Debug</strong>
        <span className="pill">{state}</span>
        <span className="muted">{events.length} 事件</span>
      </header>

      {latest && (
        <div className="metrics">
          {Object.entries(latest)
            .filter(([k, v]) => k.endsWith("_ms") && typeof v === "number" && v >= 0)
            .map(([k, v]) => (
              <span key={k} className="metric">
                {k.replace(/_ms$/, "")} <b>{v as number}ms</b>
              </span>
            ))}
        </div>
      )}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      <ul className="events">
        {filtered.length === 0 && <li className="muted">暂无事件</li>}
        {filtered.map((event, i) => (
          <li key={`${event.at}-${i}`}>
            <time>{event.at.slice(11, 23)}</time>
            <code>{summarize(event)}</code>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function summarize(event: DebugEvent): string {
  switch (event.type) {
    case "state_change":
      return `${event.from} → ${event.to}${event.reason ? `  (${event.reason})` : ""}`;
    case "retrieval": {
      const hits = (event.hits as { id: string; score: number }[]) ?? [];
      return `${event.took_ms}ms  ${hits.length} 命中  ${hits
        .map((h) => `${h.id}:${h.score}`)
        .join(", ")}`;
    }
    case "comm":
      return `${event.target}.${event.action}  ${event.took_ms}ms  ${event.status}`;
    case "latency":
      return Object.entries(event)
        .filter(([k]) => !["type", "at"].includes(k))
        .map(([k, v]) => `${k}=${v}`)
        .join("  ");
    default:
      return String(event.message ?? JSON.stringify(event));
  }
}

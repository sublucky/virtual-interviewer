import type {
  CorpusEntry,
  CorpusKind,
  CorpusMeta,
  CorpusStats,
  CorpusStatus,
  DebugEvent,
  InterviewConfig,
  Report,
  ServiceMeta,
  SessionInfo,
  StreamEvent,
} from "./types";

const json = { "Content-Type": "application/json" };

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as T;
}

export async function fetchMeta() {
  return parse<ServiceMeta>(await fetch("/api/meta"));
}

export async function createSession(config: InterviewConfig) {
  return parse<SessionInfo>(
    await fetch("/api/sessions", { method: "POST", headers: json, body: JSON.stringify(config) }),
  );
}

export async function toggleDebug(sessionId: string, enabled: boolean) {
  return parse<{ enabled: boolean }>(
    await fetch(`/api/sessions/${sessionId}/debug`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ enabled }),
    }),
  );
}

export async function fetchDebugHistory(sessionId: string) {
  return parse<{ enabled: boolean; events: DebugEvent[] }>(
    await fetch(`/api/sessions/${sessionId}/debug/history`),
  );
}

export async function fetchReport(sessionId: string) {
  return parse<{ ready: boolean; report?: Report }>(
    await fetch(`/api/sessions/${sessionId}/report`),
  );
}

export async function openRtc(sessionId: string, offerSdp: string): Promise<string> {
  const resp = await fetch(`/api/sessions/${sessionId}/rtc/offer`, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: offerSdp,
  });
  if (!resp.ok) throw new Error(`数字人连接失败：${await resp.text()}`);
  return resp.text();
}

export function reportFromEvent(event: StreamEvent): Report | null {
  if (event.event !== "report") return null;
  const { event: _kind, ...rest } = event;
  return rest as Report;
}

function emitFrame(frame: string, onEvent: (event: StreamEvent) => void) {
  for (const raw of frame.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith("data:")) continue;
    try {
      onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent);
    } catch {
      // 半包或非 JSON 心跳，等下一帧
    }
  }
}

// --------------------------------------------------------------------------
// 语料管理
// --------------------------------------------------------------------------

export async function fetchCorpusStats() {
  return parse<CorpusStats>(await fetch("/api/corpus/stats"));
}

export async function listCorpus(params: {
  role?: string;
  kind?: string;
  status?: string;
  limit?: number;
  withContent?: boolean;
}) {
  const q = new URLSearchParams();
  if (params.role) q.set("role", params.role);
  if (params.kind) q.set("kind", params.kind);
  if (params.status) q.set("status", params.status);
  if (params.limit) q.set("limit", String(params.limit));
  if (params.withContent) q.set("with_content", "true");
  const suffix = q.toString() ? `?${q}` : "";
  return parse<{ items: CorpusMeta[] }>(await fetch(`/api/corpus${suffix}`));
}

export async function getCorpus(id: string) {
  return parse<{ entry: CorpusEntry }>(await fetch(`/api/corpus/${encodeURIComponent(id)}`));
}

export async function upsertCorpus(entries: CorpusEntry[]) {
  return parse<{ upserted: number }>(
    await fetch("/api/corpus", { method: "POST", headers: json, body: JSON.stringify({ entries }) }),
  );
}

export async function setCorpusStatus(ids: string[], status: CorpusStatus) {
  return parse<{ updated: number }>(
    await fetch("/api/corpus/status", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ ids, status }),
    }),
  );
}

export async function deleteCorpus(ids: string[]) {
  return parse<{ deleted: number }>(
    await fetch("/api/corpus/delete", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ ids }),
    }),
  );
}

export async function runCorpusAgent(payload: {
  role: string;
  topic: string;
  count?: number;
  kind?: CorpusKind;
  save_as_draft?: boolean;
}) {
  return parse<{ entries: CorpusEntry[]; saved: boolean }>(
    await fetch("/api/corpus/agent", {
      method: "POST",
      headers: json,
      body: JSON.stringify({
        role: payload.role,
        topic: payload.topic,
        count: payload.count ?? 3,
        kind: payload.kind ?? "question",
        save_as_draft: payload.save_as_draft ?? true,
      }),
    }),
  );
}

export async function bootstrapCorpus(force = false) {
  return parse<{ imported: number }>(
    await fetch(`/api/corpus/bootstrap?force=${force ? "true" : "false"}`, { method: "POST" }),
  );
}

/**
 * 面试主链路：POST + SSE。用 fetch 而非 EventSource，因为需要带请求体。
 */
export async function streamTurn(
  sessionId: string,
  payload: { text?: string; kickoff?: boolean; end?: boolean },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sessionId}/message`, {
    method: "POST",
    headers: json,
    body: JSON.stringify({ text: payload.text ?? "", kickoff: !!payload.kickoff, end: !!payload.end }),
    signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`请求失败：${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) emitFrame(frame, onEvent);
  }
  if (buffer.trim()) emitFrame(buffer, onEvent);
}

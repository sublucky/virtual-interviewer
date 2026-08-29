import type { InterviewConfig, Report, SessionInfo, StreamEvent } from "./types";

const json = { "Content-Type": "application/json" };

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return (await resp.json()) as T;
}

export async function fetchMeta() {
  return parse<Record<string, any>>(await fetch("/api/meta"));
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
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent);
      } catch {
        // 忽略半包，等下一帧
      }
    }
  }
}

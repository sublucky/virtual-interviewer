export type InterviewStyle = "gentle" | "probe" | "system";

export interface InterviewConfig {
  role: string;
  company?: string;
  jd?: string;
  resume?: string;
  style: InterviewStyle;
  rounds: number;
  debug?: boolean;
}

export interface SessionInfo {
  session_id: string;
  state: string;
  debug: boolean;
  rounds: number;
}

export interface HealthStatus {
  ok: boolean;
  detail: string;
  extra: Record<string, unknown>;
}

export interface RolePreset {
  role: string;
  style: InterviewStyle;
  rounds: number;
}

export interface ServiceMeta {
  llm: HealthStatus;
  avatar: HealthStatus;
  vector: HealthStatus;
  omni?: HealthStatus;
  asr?: HealthStatus;
  tts?: HealthStatus;
  voice_mode?: "text" | "omni" | string;
  asr_provider?: string;
  tts_provider?: string;
  embedding: { provider: string; dim: number };
  llm_provider: string;
  debug_default: boolean;
  presets: RolePreset[];
}

export interface Report {
  overall: number;
  recommendation: string;
  level_guess: string;
  dimensions: { name: string; score: number; note: string }[];
  strengths: string[];
  risks: string[];
  evidence: { quote: string; why: string }[];
  next_round_focus: string[];
  summary: string;
}

/** 与 server/models.py DebugEvent 对齐 */
export interface DebugEvent {
  type: "state_change" | "retrieval" | "comm" | "latency" | "debug_log";
  at: string;
  [key: string]: unknown;
}

export type StreamEvent =
  | { event: "delta"; text: string }
  | { event: "thinking" }
  | { event: "done"; text: string; state: string }
  | { event: "transcript"; text: string }
  | { event: "assistant_audio"; format?: string; audio_b64?: string; interrupt?: boolean; bytes?: number; url?: string }
  | { event: "evaluating" }
  | ({ event: "report" } & Partial<Report>)
  | ({ event: "debug" } & DebugEvent)
  | { event: "error"; message: string };

export interface Turn {
  role: "interviewer" | "candidate";
  text: string;
}

export type CorpusKind = "question" | "rubric" | "knowledge" | "case";
export type CorpusSource = "manual" | "agent" | "import";
export type CorpusStatus = "draft" | "active" | "disabled";

export interface CorpusMeta {
  id: string;
  kind: CorpusKind | string;
  role: string;
  tags: string[];
  source: CorpusSource | string;
  status: CorpusStatus | string;
  version: number;
  updated_at: string;
  content?: string;
  rubric?: string | null;
  reference_answer?: string | null;
}

export interface CorpusEntry extends CorpusMeta {
  content: string;
  rubric?: string | null;
  reference_answer?: string | null;
}

export interface CorpusStats {
  by_status: Record<string, number>;
  vectors: number;
}

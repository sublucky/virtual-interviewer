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
  | { event: "evaluating" }
  | ({ event: "report" } & Partial<Report>)
  | ({ event: "debug" } & DebugEvent)
  | { event: "error"; message: string };

export interface Turn {
  role: "interviewer" | "candidate";
  text: string;
}

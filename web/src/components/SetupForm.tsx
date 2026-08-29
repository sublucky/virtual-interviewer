import { useState } from "react";
import type { InterviewConfig, InterviewStyle } from "../types";

const STYLES: { value: InterviewStyle; label: string }[] = [
  { value: "gentle", label: "温和引导" },
  { value: "probe", label: "追问施压" },
  { value: "system", label: "系统设计" },
];

export function SetupForm({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: (config: InterviewConfig) => void;
}) {
  const [config, setConfig] = useState<InterviewConfig>({
    role: "后端工程师",
    company: "",
    jd: "",
    resume: "",
    style: "probe",
    rounds: 8,
    debug: false,
  });

  const patch = (next: Partial<InterviewConfig>) => setConfig((c) => ({ ...c, ...next }));

  return (
    <form
      className="setup"
      onSubmit={(e) => {
        e.preventDefault();
        onStart(config);
      }}
    >
      <h1>虚拟面试官</h1>
      <p className="muted">配置岗位后开始，面试官会实时提问并在结束后生成评估报告。</p>

      <label>
        岗位
        <input
          value={config.role}
          required
          onChange={(e) => patch({ role: e.target.value })}
          placeholder="如 后端工程师"
        />
      </label>

      <label>
        公司（可选）
        <input value={config.company} onChange={(e) => patch({ company: e.target.value })} />
      </label>

      <label>
        岗位 JD（可选）
        <textarea rows={4} value={config.jd} onChange={(e) => patch({ jd: e.target.value })} />
      </label>

      <label>
        简历要点（可选）
        <textarea rows={4} value={config.resume} onChange={(e) => patch({ resume: e.target.value })} />
      </label>

      <div className="row">
        <label>
          风格
          <select
            value={config.style}
            onChange={(e) => patch({ style: e.target.value as InterviewStyle })}
          >
            {STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          轮次
          <input
            type="number"
            min={4}
            max={16}
            value={config.rounds}
            onChange={(e) => patch({ rounds: Number(e.target.value) })}
          />
        </label>
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={!!config.debug}
          onChange={(e) => patch({ debug: e.target.checked })}
        />
        开启 Debug 模式
      </label>

      <button type="submit" disabled={busy}>
        {busy ? "连接中…" : "开始面试"}
      </button>
    </form>
  );
}

import type { InterviewConfig, InterviewStyle, RolePreset, ServiceMeta } from "../types";

const STYLES: { value: InterviewStyle; label: string }[] = [
  { value: "gentle", label: "温和引导" },
  { value: "probe", label: "追问施压" },
  { value: "system", label: "系统设计" },
];

const FALLBACK_PRESETS: RolePreset[] = [
  { role: "后端工程师", style: "probe", rounds: 8 },
  { role: "前端工程师", style: "probe", rounds: 8 },
  { role: "算法工程师", style: "probe", rounds: 8 },
  { role: "产品经理", style: "gentle", rounds: 8 },
  { role: "客户端工程师", style: "probe", rounds: 8 },
];

export function SetupForm({
  busy,
  meta,
  config,
  onChange,
  onStart,
  onOpenCorpus,
}: {
  busy: boolean;
  meta: ServiceMeta | null;
  config: InterviewConfig;
  onChange: (next: Partial<InterviewConfig>) => void;
  onStart: () => void;
  onOpenCorpus?: () => void;
}) {
  const presets = meta?.presets?.length ? meta.presets : FALLBACK_PRESETS;

  return (
    <form
      className="setup"
      onSubmit={(e) => {
        e.preventDefault();
        onStart();
      }}
    >
      <h1>虚拟面试官</h1>
      <p className="muted">
        配置岗位后开始。数字人或 Omni 不可用时自动走文字模式
        {meta?.voice_mode === "omni" ? "（当前 VOICE_MODE=omni）" : ""}。
      </p>

      <HealthBar meta={meta} />

      <div className="presets" role="group" aria-label="岗位预设">
        {presets.map((preset) => (
          <button
            key={preset.role}
            type="button"
            className={config.role === preset.role ? "chip active" : "chip"}
            onClick={() => onChange({ role: preset.role, style: preset.style, rounds: preset.rounds })}
          >
            {preset.role}
          </button>
        ))}
      </div>

      <label>
        岗位
        <input
          value={config.role}
          required
          onChange={(e) => onChange({ role: e.target.value })}
          placeholder="如 后端工程师"
        />
      </label>

      <label>
        公司（可选）
        <input value={config.company} onChange={(e) => onChange({ company: e.target.value })} />
      </label>

      <label>
        岗位 JD（可选）
        <textarea rows={4} value={config.jd} onChange={(e) => onChange({ jd: e.target.value })} />
      </label>

      <label>
        简历要点（可选）
        <textarea rows={4} value={config.resume} onChange={(e) => onChange({ resume: e.target.value })} />
      </label>

      <div className="row">
        <label>
          风格
          <select
            value={config.style}
            onChange={(e) => onChange({ style: e.target.value as InterviewStyle })}
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
            onChange={(e) => onChange({ rounds: Number(e.target.value) })}
          />
        </label>
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={!!config.debug}
          onChange={(e) => onChange({ debug: e.target.checked })}
        />
        开启 Debug 模式
      </label>

      <button type="submit" disabled={busy}>
        {busy ? "连接中…" : "开始面试"}
      </button>

      {onOpenCorpus && (
        <button type="button" className="ghost" disabled={busy} onClick={onOpenCorpus}>
          语料管理（RAG）
        </button>
      )}
    </form>
  );
}

function HealthBar({ meta }: { meta: ServiceMeta | null }) {
  if (!meta) return <p className="muted">正在探测后端服务…</p>;
  const omniOk = Boolean(meta.omni?.ok);
  const items = [
    { name: "LLM", ok: meta.llm.ok, hint: meta.llm_provider || String(meta.llm.extra?.provider ?? "") },
    { name: "向量库", ok: meta.vector.ok, hint: String(meta.embedding.provider) },
    { name: "数字人", ok: meta.avatar.ok, hint: meta.avatar.ok ? "LiveTalking" : "文字模式" },
    {
      name: "Omni",
      ok: omniOk,
      hint: omniOk ? meta.voice_mode || "omni" : meta.voice_mode === "omni" ? "降级文字" : "未启用",
    },
  ];
  return (
    <ul className="health">
      {items.map((item) => (
        <li key={item.name} className={item.ok ? "ok" : "down"}>
          <i />
          <span>{item.name}</span>
          <small>{item.ok ? item.hint : item.hint || "不可用"}</small>
        </li>
      ))}
    </ul>
  );
}

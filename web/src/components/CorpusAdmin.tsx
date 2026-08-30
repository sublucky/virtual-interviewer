import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bootstrapCorpus,
  deleteCorpus,
  getCorpus,
  listCorpus,
  fetchCorpusStats,
  runCorpusAgent,
  setCorpusStatus,
  upsertCorpus,
} from "../api";
import type {
  CorpusEntry,
  CorpusKind,
  CorpusMeta,
  CorpusStats,
  CorpusStatus,
  RolePreset,
} from "../types";

const KINDS: CorpusKind[] = ["question", "rubric", "knowledge", "case"];
const STATUSES: Array<CorpusStatus | ""> = ["", "draft", "active", "disabled"];

const EMPTY_FORM: CorpusEntry = {
  id: "",
  kind: "question",
  role: "后端工程师",
  tags: [],
  content: "",
  rubric: "",
  reference_answer: "",
  source: "manual",
  status: "active",
  version: 1,
  updated_at: "",
};

type Tab = "list" | "create" | "agent";

export function CorpusAdmin({
  presets,
  onBack,
}: {
  presets: RolePreset[];
  onBack: () => void;
}) {
  const roles = useMemo(
    () => ["", ...presets.map((p) => p.role), "通用"].filter((v, i, a) => a.indexOf(v) === i),
    [presets],
  );

  const [tab, setTab] = useState<Tab>("list");
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [items, setItems] = useState<CorpusMeta[]>([]);
  const [role, setRole] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState<CorpusStatus | "">("draft");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<CorpusEntry | null>(null);
  const [form, setForm] = useState<CorpusEntry>(EMPTY_FORM);
  const [agentRole, setAgentRole] = useState(presets[0]?.role ?? "后端工程师");
  const [agentTopic, setAgentTopic] = useState("分布式事务");
  const [agentCount, setAgentCount] = useState(3);
  const [agentPreview, setAgentPreview] = useState<CorpusEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    const [nextStats, nextList] = await Promise.all([
      fetchCorpusStats(),
      listCorpus({
        role: role || undefined,
        kind: kind || undefined,
        status: status || undefined,
        limit: 300,
        withContent: true,
      }),
    ]);
    setStats(nextStats);
    setItems(nextList.items);
    setSelected(new Set());
  }, [role, kind, status]);

  useEffect(() => {
    void refresh().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [refresh]);

  const run = async (fn: () => Promise<void>, ok?: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      if (ok) setMessage(ok);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openDetail = async (id: string) => {
    setBusy(true);
    setError("");
    try {
      const local = items.find((i) => i.id === id);
      if (local?.content) {
        setDetail(local as CorpusEntry);
      } else {
        const { entry } = await getCorpus(id);
        setDetail(entry);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ids = [...selected];

  return (
    <section className="corpus">
      <header className="corpus-head">
        <div>
          <h1>语料管理</h1>
          <p className="muted">RAG 题库 / 评分要点 / 知识条目。Agent 草稿需审核后才会进入检索。</p>
        </div>
        <button type="button" className="ghost" onClick={onBack}>
          返回面试
        </button>
      </header>

      {stats && (
        <ul className="corpus-stats">
          <li>
            <b>{stats.vectors}</b>
            <span>向量</span>
          </li>
          <li>
            <b>{stats.by_status.active ?? 0}</b>
            <span>active</span>
          </li>
          <li className="warn">
            <b>{stats.by_status.draft ?? 0}</b>
            <span>draft 待审</span>
          </li>
          <li>
            <b>{stats.by_status.disabled ?? 0}</b>
            <span>disabled</span>
          </li>
        </ul>
      )}

      <nav className="corpus-tabs">
        {(
          [
            ["list", "列表审核"],
            ["create", "手工录入"],
            ["agent", "语料 Agent"],
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>

      {error && <div className="error">{error}</div>}
      {message && <div className="ok-banner">{message}</div>}

      {tab === "list" && (
        <>
          <div className="corpus-filters">
            <label>
              岗位
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {roles.map((r) => (
                  <option key={r || "all"} value={r}>
                    {r || "全部"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              类型
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="">全部</option>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label>
              状态
              <select value={status} onChange={(e) => setStatus(e.target.value as CorpusStatus | "")}>
                {STATUSES.map((s) => (
                  <option key={s || "all"} value={s}>
                    {s || "全部"}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="ghost" disabled={busy} onClick={() => void refresh()}>
              刷新
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy}
              onClick={() => void run(async () => { await bootstrapCorpus(true); }, "种子已强制重导")}
            >
              重导种子
            </button>
          </div>

          <div className="corpus-actions">
            <button
              type="button"
              disabled={busy || ids.length === 0}
              onClick={() => void run(async () => { await setCorpusStatus(ids, "active"); }, `已启用 ${ids.length} 条`)}
            >
              审核通过 → active
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy || ids.length === 0}
              onClick={() => void run(async () => { await setCorpusStatus(ids, "disabled"); }, `已停用 ${ids.length} 条`)}
            >
              停用 / 驳回
            </button>
            <button
              type="button"
              className="ghost danger"
              disabled={busy || ids.length === 0}
              onClick={() => void run(async () => { await deleteCorpus(ids); }, `已删除 ${ids.length} 条`)}
            >
              删除向量
            </button>
            <span className="muted">已选 {ids.length} / 共 {items.length}</span>
          </div>

          <div className="corpus-table-wrap">
            <table className="corpus-table">
              <thead>
                <tr>
                  <th />
                  <th>状态</th>
                  <th>岗位</th>
                  <th>类型</th>
                  <th>来源</th>
                  <th>内容</th>
                  <th>标签</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="muted">
                      无匹配语料
                    </td>
                  </tr>
                )}
                {items.map((item) => (
                  <tr key={item.id} className={selected.has(item.id) ? "selected" : ""}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(item.id)}
                        onChange={() => toggle(item.id)}
                      />
                    </td>
                    <td>
                      <span className={`status-pill ${item.status}`}>{item.status}</span>
                    </td>
                    <td>{item.role}</td>
                    <td>{item.kind}</td>
                    <td>{item.source}</td>
                    <td className="snippet" title={item.content || item.id}>
                      {item.content || item.id}
                    </td>
                    <td className="tags">{(item.tags || []).join(", ")}</td>
                    <td>
                      <button type="button" className="ghost tiny" onClick={() => void openDetail(item.id)}>
                        详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "create" && (
        <form
          className="corpus-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const id = form.id.trim() || `manual-${crypto.randomUUID().slice(0, 10)}`;
              const entry: CorpusEntry = {
                ...form,
                id,
                tags: form.tags,
                content: form.content.trim(),
                rubric: form.rubric || null,
                reference_answer: form.reference_answer || null,
                source: "manual",
                updated_at: new Date().toISOString(),
              };
              if (!entry.content) throw new Error("正文不能为空");
              await upsertCorpus([entry]);
              setForm({ ...EMPTY_FORM, role: form.role });
              setTab("list");
              setStatus(entry.status as CorpusStatus);
            }, "已保存语料");
          }}
        >
          <div className="row">
            <label>
              ID（可空自动生成）
              <input
                value={form.id}
                onChange={(e) => setForm((f) => ({ ...f, id: e.target.value }))}
                placeholder="manual-xxx"
              />
            </label>
            <label>
              岗位
              <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}>
                {roles.filter(Boolean).map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="row">
            <label>
              类型
              <select
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value as CorpusKind }))}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label>
              状态
              <select
                value={form.status}
                onChange={(e) => setForm((f) => ({ ...f, status: e.target.value as CorpusStatus }))}
              >
                <option value="active">active（直接生效）</option>
                <option value="draft">draft</option>
              </select>
            </label>
          </div>
          <label>
            标签（逗号分隔）
            <input
              value={form.tags.join(", ")}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  tags: e.target.value
                    .split(/[,，]/)
                    .map((t) => t.trim())
                    .filter(Boolean),
                }))
              }
            />
          </label>
          <label>
            正文
            <textarea
              rows={5}
              required
              value={form.content}
              onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
            />
          </label>
          <label>
            评分要点
            <textarea
              rows={3}
              value={form.rubric ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, rubric: e.target.value }))}
            />
          </label>
          <label>
            参考答案
            <textarea
              rows={3}
              value={form.reference_answer ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, reference_answer: e.target.value }))}
            />
          </label>
          <button type="submit" disabled={busy}>
            保存入库
          </button>
        </form>
      )}

      {tab === "agent" && (
        <div className="corpus-agent">
          <div className="row">
            <label>
              岗位
              <select value={agentRole} onChange={(e) => setAgentRole(e.target.value)}>
                {roles.filter(Boolean).map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label>
              条数
              <input
                type="number"
                min={1}
                max={10}
                value={agentCount}
                onChange={(e) => setAgentCount(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            主题
            <input value={agentTopic} onChange={(e) => setAgentTopic(e.target.value)} />
          </label>
          <div className="corpus-actions">
            <button
              type="button"
              disabled={busy || !agentTopic.trim()}
              onClick={() =>
                void run(async () => {
                  const res = await runCorpusAgent({
                    role: agentRole,
                    topic: agentTopic.trim(),
                    count: agentCount,
                    kind: "question",
                    save_as_draft: true,
                  });
                  setAgentPreview(res.entries);
                  setStatus("draft");
                  setTab("list");
                }, `已生成 ${agentCount} 条 draft`)
              }
            >
              生成并保存为 draft
            </button>
          </div>
          {agentPreview.length > 0 && (
            <ul className="agent-preview">
              {agentPreview.map((entry) => (
                <li key={entry.id}>
                  <strong>{entry.id}</strong>
                  <p>{entry.content}</p>
                </li>
              ))}
            </ul>
          )}
          <p className="muted">生成结果一律 draft，需在「列表审核」中勾选后点「审核通过」。</p>
        </div>
      )}

      {detail && (
        <div className="corpus-drawer" role="dialog" aria-label="语料详情">
          <div className="corpus-drawer-card">
            <header>
              <strong>{detail.id}</strong>
              <button type="button" className="ghost tiny" onClick={() => setDetail(null)}>
                关闭
              </button>
            </header>
            <p>
              <span className={`status-pill ${detail.status}`}>{detail.status}</span> {detail.role} ·{" "}
              {detail.kind} · {detail.source}
            </p>
            <h3>正文</h3>
            <p>{detail.content}</p>
            {detail.rubric && (
              <>
                <h3>评分要点</h3>
                <pre>{detail.rubric}</pre>
              </>
            )}
            {detail.reference_answer && (
              <>
                <h3>参考答案</h3>
                <pre>{detail.reference_answer}</pre>
              </>
            )}
            <div className="corpus-actions">
              {detail.status === "draft" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await setCorpusStatus([detail.id], "active");
                      setDetail(null);
                    }, "已启用")
                  }
                >
                  审核通过
                </button>
              )}
              {detail.status === "active" && (
                <button
                  type="button"
                  className="ghost"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await setCorpusStatus([detail.id], "disabled");
                      setDetail(null);
                    }, "已停用")
                  }
                >
                  停用
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

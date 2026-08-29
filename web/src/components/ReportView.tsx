import type { Report } from "../types";

const LABELS: Record<string, string> = {
  strong_hire: "强烈推荐",
  hire: "推荐",
  lean_hire: "倾向推荐",
  lean_no: "倾向不推荐",
  no_hire: "不推荐",
};

export function ReportView({ report, onRestart }: { report: Report; onRestart: () => void }) {
  return (
    <section className="report">
      <header>
        <h2>面试评估报告</h2>
        <div className="score">
          <b>{report.overall}</b>
          <span>{LABELS[report.recommendation] ?? report.recommendation}</span>
          {report.level_guess && <span className="pill">{report.level_guess}</span>}
        </div>
      </header>

      <p className="summary">{report.summary}</p>

      <div className="dimensions">
        {report.dimensions.map((d) => (
          <div key={d.name} className="dimension">
            <div className="dimension-head">
              <span>{d.name}</span>
              <b>{d.score}/5</b>
            </div>
            <div className="bar">
              <i style={{ width: `${(d.score / 5) * 100}%` }} />
            </div>
            <p className="muted">{d.note}</p>
          </div>
        ))}
      </div>

      <div className="lists">
        <Block title="亮点" items={report.strengths} />
        <Block title="风险" items={report.risks} />
        <Block title="下一轮建议考察" items={report.next_round_focus} />
      </div>

      {report.evidence.length > 0 && (
        <div className="evidence">
          <h3>证据</h3>
          {report.evidence.map((e, i) => (
            <blockquote key={i}>
              <p>“{e.quote}”</p>
              <cite>{e.why}</cite>
            </blockquote>
          ))}
        </div>
      )}

      <button onClick={onRestart}>再来一场</button>
    </section>
  );
}

function Block({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h3>{title}</h3>
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

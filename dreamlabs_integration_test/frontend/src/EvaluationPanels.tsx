import { useCallback, useEffect, useState } from "react";

type Component = {
  status: "MEASURED" | "NOT_MEASURED";
  effective_weight: number;
  points: number | null;
  penalty_points: number;
  detail: string;
};
type Incident = { incident_id: string; type: string; object_id: string; severity: string; penalty: number; explanation: string };
type CheckItem = { name: string; status: string; detail: string };
type Result = {
  outcome: string;
  score: number | null;
  coverage: number;
  total_penalties: number;
  config_version: string;
  caps_applied: string[];
  summary: string;
  components: Record<string, Component>;
  verification: { criterion: { type: string; object_id: string }; status: string; checks: CheckItem[] }[];
  scene_understanding: Record<string, { status: string; detail: string }>;
  interactions: Record<string, number>;
  incidents: Incident[];
  missing_evidence: string[];
  went_well: string[];
  went_wrong: string[];
  recommendations: string[];
};
type TaskReport = {
  execution: { status: string; duration_seconds: number | null; action_count: number | null; model_calls: number | null };
  evidence: { source: string };
  result: Result;
};
type BatchTask = { task_id: string; instruction: string; execution_status: string; outcome: string; score: number | null };
type BatchSummary = {
  batch_id: string;
  total_tasks: number;
  successful: number;
  partial: number;
  failed: number;
  unsafe: number;
  timed_out: number;
  not_evaluable: number;
  batch_score: number | null;
  score_coverage: number;
  success_rate: number | null;
  average_score: number | null;
  median_score: number | null;
  highest_score: number | null;
  lowest_score: number | null;
  total_penalties: number;
  total_disturbances: number;
  tasks: BatchTask[];
  sample_note: string;
};
export type EvaluationRef = { outcome: string; score: number | null; coverage: number; batch_id: string };

const label = (text: string) => text.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
const outcomeClass = (outcome: string) =>
  outcome === "SUCCESS" ? "good" : outcome === "PARTIAL_SUCCESS" ? "warn" : outcome === "NOT_EVALUABLE" ? "neutral" : "bad";
const show = (value: number | null | undefined, suffix = "") => (value == null ? "n/a" : `${value}${suffix}`);

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h3>{title}</h3>
      <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </div>
  );
}

export function TaskReportPanel({ taskId, evaluation, error }: { taskId: string; evaluation?: EvaluationRef | null; error?: string | null }) {
  const [report, setReport] = useState<TaskReport | null>(null);
  const [failed, setFailed] = useState("");

  useEffect(() => {
    if (!evaluation) return;
    let active = true;
    fetch(`/api/reports/tasks/${taskId}`)
      .then((response) => {
        if (!response.ok) throw new Error(`Report request failed (${response.status})`);
        return response.json();
      })
      .then((data) => active && setReport(data))
      .catch((reason) => active && setFailed(String(reason.message ?? reason)));
    return () => {
      active = false;
    };
  }, [taskId, evaluation]);

  if (error) return <div className="panel"><h2>Evaluation report</h2><div className="status error">{error}</div></div>;
  if (!evaluation) return null;
  if (failed) return <div className="panel"><h2>Evaluation report</h2><div className="status error">{failed}</div></div>;
  if (!report) return <div className="panel"><h2>Evaluation report</h2><p className="hint">Loading report...</p></div>;
  const result = report.result;
  const checks = result.verification.flatMap((item) => item.checks);
  return (
    <div className="panel evaluation" aria-label="Evaluation report">
      <div className="stream-bar">
        <h2>Evaluation report</h2>
        <span className="report-links">
          <a href={`/api/reports/tasks/${taskId}?format=html`} target="_blank" rel="noreferrer">HTML</a>
          <a href={`/api/reports/tasks/${taskId}`} target="_blank" rel="noreferrer">JSON</a>
        </span>
      </div>
      <div className="score-head">
        <div className="score-value">{result.score == null ? "Not scored" : <>{result.score}<small>/100</small></>}</div>
        <div>
          <span className={`pill ${outcomeClass(result.outcome)}`}>{label(result.outcome)}</span>
          <p>{result.summary}</p>
          <p className="hint">Coverage {result.coverage}% · {report.execution.action_count ?? "n/a"} actions · {report.execution.model_calls ?? "n/a"} Dream-1 calls · {result.config_version}</p>
          {result.caps_applied.map((cap) => <p className="hint" key={cap}>{cap}</p>)}
        </div>
      </div>
      <h3>Score breakdown</h3>
      {Object.entries(result.components).map(([name, item]) => (
        <div className="bar-row" key={name} title={item.detail}>
          <span>{label(name)}</span>
          <div className="bar"><div style={{ width: `${item.points == null || !item.effective_weight ? 0 : (item.points / item.effective_weight) * 100}%` }} /></div>
          <span>{item.status === "NOT_MEASURED" ? "Not measured" : `${item.points} / ${item.effective_weight}`}</span>
        </div>
      ))}
      <h3>Penalties {result.total_penalties > 0 ? `(-${result.total_penalties})` : ""}</h3>
      {result.incidents.length ? (
        <table><tbody>{result.incidents.map((item) => (
          <tr key={item.incident_id}><td>{label(item.type)}</td><td>{item.severity}</td><td>-{item.penalty}</td><td>{item.explanation}</td></tr>
        ))}</tbody></table>
      ) : <p className="hint">No penalties.</p>}
      <h3>Task checks</h3>
      {checks.length ? (
        <table><tbody>{checks.map((item) => (
          <tr key={item.name}><td>{label(item.name)}</td><td><span className={`pill small ${item.status === "PASS" ? "good" : item.status === "FAIL" ? "bad" : "neutral"}`}>{label(item.status)}</span></td><td>{item.detail}</td></tr>
        ))}</tbody></table>
      ) : <p className="hint">No automatic check is available for this instruction.</p>}
      <h3>Scene understanding evidence</h3>
      <table><tbody>{Object.entries(result.scene_understanding).map(([name, item]) => (
        <tr key={name}><td>{label(name)}</td><td><span className={`pill small ${item.status === "PASS" ? "good" : item.status === "FAIL" ? "bad" : item.status === "PARTIAL" ? "warn" : "neutral"}`}>{label(item.status)}</span></td><td>{item.detail}</td></tr>
      ))}</tbody></table>
      <div className="report-columns">
        <List title="What went well" items={result.went_well} />
        <List title="What went wrong" items={result.went_wrong} />
        <List title="Recommendations" items={result.recommendations} />
        <List title="Missing evidence" items={result.missing_evidence} />
      </div>
    </div>
  );
}

export function BatchPanel({ refresh, locked }: { refresh: string; locked: boolean }) {
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const batches = await fetch("/api/evaluation/batches");
    if (!batches.ok) return;
    const { current_batch: batchId } = await batches.json();
    const response = await fetch(`/api/reports/batches/${batchId}/summary`);
    if (response.ok) setSummary(await response.json());
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load, refresh]);

  const startNew = async () => {
    setError("");
    try {
      const response = await fetch("/api/evaluation/batches", { method: "POST" });
      if (!response.ok) throw new Error(`Could not start a new benchmark (${response.status})`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (!summary) return null;
  const stats: [string, number | string][] = [
    ["Tasks", summary.total_tasks],
    ["Success", summary.successful],
    ["Partial", summary.partial],
    ["Failed", summary.failed],
    ["Unsafe", summary.unsafe],
    ["Timed out", summary.timed_out],
    ["Not scored", summary.not_evaluable],
    ["Penalties", summary.total_penalties],
  ];
  return (
    <div className="panel evaluation" aria-label="Benchmark report">
      <div className="stream-bar">
        <h2>Benchmark report</h2>
        <span className="report-links">
          {summary.total_tasks > 0 && <a href={`/api/reports/batches/${summary.batch_id}?format=html`} target="_blank" rel="noreferrer">HTML</a>}
          {summary.total_tasks > 0 && <a href={`/api/reports/batches/${summary.batch_id}`} target="_blank" rel="noreferrer">JSON</a>}
          <button disabled={locked} onClick={startNew}>New benchmark</button>
        </span>
      </div>
      {error && <div className="status error">{error}</div>}
      {summary.total_tasks === 0 ? <p className="hint">Run a task to start this benchmark.</p> : (
        <>
          <div className="score-head">
            <div className="score-value">{summary.batch_score == null ? "Not scored" : <>{summary.batch_score}<small>/100</small></>}</div>
            <div>
              <p>Success rate {show(summary.success_rate, "%")} · score coverage {summary.score_coverage}%</p>
              <p className="hint">Median {show(summary.median_score)} · highest {show(summary.highest_score)} · lowest {show(summary.lowest_score)}</p>
            </div>
          </div>
          <div className="stat-grid">{stats.map(([name, value]) => <div key={name}><strong>{value}</strong><span>{name}</span></div>)}</div>
          <table>
            <thead><tr><th>Instruction</th><th>Execution</th><th>Outcome</th><th>Score</th><th>Report</th></tr></thead>
            <tbody>{summary.tasks.map((item) => (
              <tr key={item.task_id}>
                <td>{item.instruction}</td><td>{label(item.execution_status)}</td>
                <td><span className={`pill small ${outcomeClass(item.outcome)}`}>{label(item.outcome)}</span></td>
                <td>{show(item.score)}</td>
                <td><a href={`/api/reports/tasks/${item.task_id}?format=html`} target="_blank" rel="noreferrer">Open</a></td>
              </tr>
            ))}</tbody>
          </table>
          <p className="hint">{summary.sample_note}</p>
        </>
      )}
    </div>
  );
}

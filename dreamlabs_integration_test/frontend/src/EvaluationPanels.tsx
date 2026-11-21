import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

type Component = {
  status: "MEASURED" | "NOT_MEASURED";
  effective_weight: number;
  points: number | null;
  detail: string;
};
type Incident = { incident_id: string; type: string; severity: string; penalty: number; explanation: string };
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
  verification: { checks: CheckItem[] }[];
  scene_understanding: Record<string, { status: string; detail: string; source?: string }>;
  incidents: Incident[];
  missing_evidence: string[];
  went_well: string[];
  went_wrong: string[];
  recommendations: string[];
};
type AttemptReport = {
  task: { task_id: string; attempt: number; where: string };
  execution: { status: string; duration_seconds: number | null; action_count: number | null; model_calls: number | null };
  evidence: { videos: string[] };
  result: Result;
};
type AttemptRow = { attempt: number; where: string; execution_status: string; outcome: string; score: number | null; summary: string };
type RunSummary = {
  run_id: string;
  instruction: string;
  scene_id: string;
  started_at: number | null;
  outcome: string;
  final_score: number | null;
  best_score: number | null;
  worst_score: number | null;
  attempts_total: number;
  attempts_scored: number;
  attempts_succeeded: number;
  attempts: AttemptRow[];
  recommendations: string[];
};
type RunReport = { run: RunSummary; grid_url: string | null; attempts: AttemptReport[] };
type BatchEntry = {
  batch_id: string;
  is_current: boolean;
  first_started_at: number | null;
  last_started_at: number | null;
  runs: number;
  attempts: number;
  batch_score: number | null;
};
type BatchSummary = {
  batch_id: string;
  first_started_at: number | null;
  last_started_at: number | null;
  total_runs: number;
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
  median_score: number | null;
  highest_score: number | null;
  lowest_score: number | null;
  total_penalties: number;
  runs: RunSummary[];
  sample_note: string;
};
export type EvaluationRef = { outcome: string; score: number | null; coverage: number; batch_id: string };

const label = (text: string) => text.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
const outcomeClass = (outcome: string) =>
  outcome === "SUCCESS" ? "good" : outcome === "PARTIAL_SUCCESS" ? "warn" : outcome === "NOT_EVALUABLE" ? "neutral" : "bad";
const statusClass = (status: string) => (status === "PASS" ? "good" : status === "FAIL" ? "bad" : status === "PARTIAL" ? "warn" : "neutral");
const show = (value: number | null | undefined, suffix = "") => (value == null ? "n/a" : `${value}${suffix}`);
const when = (seconds: number | null | undefined) =>
  seconds == null
    ? "No runs yet"
    : new Date(seconds * 1000).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h3>{title}</h3>
      <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </div>
  );
}

function ScoreHead({ score, outcome, children }: { score: number | null; outcome: string; children: ReactNode }) {
  return (
    <div className="score-head">
      <div className="score-value">{score == null ? "Not scored" : <>{score}<small>/100</small></>}</div>
      <div>
        <span className={`pill ${outcomeClass(outcome)}`}>{label(outcome)}</span>
        {children}
      </div>
    </div>
  );
}

function AttemptView({ report }: { report: AttemptReport }) {
  const result = report.result;
  const checks = result.verification.flatMap((item) => item.checks);
  return (
    <>
      {report.evidence.videos[0] ? (
        <video className="report-video" controls muted playsInline preload="metadata" src={report.evidence.videos[0]} />
      ) : <p className="hint">No playable video was saved for this attempt.</p>}
      <ScoreHead score={result.score} outcome={result.outcome}>
        <p>{result.summary}</p>
        <p className="hint">
          {report.task.where === "live" ? "Live" : "Hidden"} attempt · coverage {result.coverage}% · {show(report.execution.action_count)} actions · {show(report.execution.model_calls)} Dream-1 calls · {result.config_version}
        </p>
        {result.caps_applied.map((cap) => <p className="hint" key={cap}>{cap}</p>)}
      </ScoreHead>
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
        <table><tbody>{checks.map((item, index) => (
          <tr key={`${item.name}-${index}`}><td>{label(item.name)}</td><td><span className={`pill small ${statusClass(item.status)}`}>{label(item.status)}</span></td><td>{item.detail}</td></tr>
        ))}</tbody></table>
      ) : <p className="hint">No automatic check is available for this instruction.</p>}
      <h3>Scene understanding evidence</h3>
      <table><tbody>{Object.entries(result.scene_understanding).map(([name, item]) => (
        <tr key={name}>
          <td>{name === "perception" ? "Perception (inferred)" : label(name)}</td>
          <td><span className={`pill small ${statusClass(item.status)}`}>{label(item.status)}</span></td>
          <td>{item.detail}</td>
        </tr>
      ))}</tbody></table>
      <div className="report-columns">
        <List title="What went well" items={result.went_well} />
        <List title="What went wrong" items={result.went_wrong} />
        <List title="Recommendations for this attempt" items={result.recommendations} />
        <List title="Missing evidence" items={result.missing_evidence} />
      </div>
    </>
  );
}

function FinalView({ report, openAttempt }: { report: RunReport; openAttempt: (attempt: number) => void }) {
  const run = report.run;
  const clips = report.attempts.filter((item) => item.evidence.videos[0]);
  return (
    <>
      <ScoreHead score={run.final_score} outcome={run.outcome}>
        <p>{run.attempts_succeeded} of {run.attempts_total} attempts succeeded · {run.attempts_scored} scored</p>
        <p className="hint">Final score is the average of scored attempts · best {show(run.best_score)} · worst {show(run.worst_score)}</p>
      </ScoreHead>
      {clips.length > 0 && (
        <div className="attempt-videos">
          {clips.map((item) => (
            <figure key={item.task.task_id} className={item.result.outcome === "SUCCESS" ? "passed" : item.result.outcome === "NOT_EVALUABLE" ? "" : "failed"}>
              <video controls muted loop playsInline preload="metadata" src={item.evidence.videos[0]} />
              <figcaption><strong>Attempt {item.task.attempt}</strong>: {label(item.result.outcome)} · {show(item.result.score)}</figcaption>
            </figure>
          ))}
        </div>
      )}
      <h3>Attempts</h3>
      <table>
        <thead><tr><th>Attempt</th><th>Execution</th><th>Outcome</th><th>Score</th><th>Summary</th></tr></thead>
        <tbody>{run.attempts.map((item) => (
          <tr className="run-row" key={item.attempt} onClick={() => openAttempt(item.attempt)}>
            <td>{item.attempt} ({item.where})</td><td>{label(item.execution_status)}</td>
            <td><span className={`pill small ${outcomeClass(item.outcome)}`}>{label(item.outcome)}</span></td>
            <td>{show(item.score)}</td><td>{item.summary}</td>
          </tr>
        ))}</tbody>
      </table>
      <List title="Run recommendations" items={run.recommendations} />
    </>
  );
}

function RunView({ runId }: { runId: string }) {
  const [report, setReport] = useState<RunReport | null>(null);
  const [tab, setTab] = useState<number>(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setReport(null);
    setError("");
    setTab(0);
    getJson<RunReport>(`/api/reports/runs/${runId}`)
      .then((data) => active && setReport(data))
      .catch((reason) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [runId]);

  if (error) return <div className="status error">{error}</div>;
  if (!report) return <p className="hint">Loading report...</p>;
  const current = report.attempts.find((item) => item.task.attempt === tab);
  return (
    <div>
      <div className="stream-bar" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0 }}>{report.run.instruction} <span className="muted">· {when(report.run.started_at)}</span></h3>
        <span className="report-links">
          <a href={`/api/reports/runs/${runId}?format=html`} target="_blank" rel="noreferrer">HTML</a>
          <a href={`/api/reports/runs/${runId}`} target="_blank" rel="noreferrer">JSON</a>
        </span>
      </div>
      <div className="report-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 0} className={tab === 0 ? "active" : ""} onClick={() => setTab(0)}>Final report</button>
        {report.attempts.map((item) => (
          <button role="tab" key={item.task.attempt} aria-selected={tab === item.task.attempt} className={tab === item.task.attempt ? "active" : ""} onClick={() => setTab(item.task.attempt)}>
            Attempt {item.task.attempt}{" "}
            <span className={item.result.outcome === "SUCCESS" ? "mark-good" : item.result.outcome === "NOT_EVALUABLE" ? "" : "mark-bad"}>
              {item.result.outcome === "SUCCESS" ? "✓" : item.result.outcome === "NOT_EVALUABLE" ? "–" : "✗"}
            </span>
          </button>
        ))}
      </div>
      {current ? <AttemptView key={current.task.task_id} report={current} /> : <FinalView report={report} openAttempt={setTab} />}
    </div>
  );
}

export function ReportsCenter({ latestRunId, error, locked }: { latestRunId: string; error?: string | null; locked: boolean }) {
  const [batches, setBatches] = useState<BatchEntry[]>([]);
  const [batchId, setBatchId] = useState("");
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [runId, setRunId] = useState("");
  const [problem, setProblem] = useState("");

  const openBatch = useCallback(async (id: string, run = "") => {
    const data = await getJson<BatchSummary>(`/api/reports/batches/${id}/summary`);
    setBatchId(id);
    setSummary(data);
    setRunId(run || data.runs?.[0]?.run_id || "");
  }, []);

  const loadHistory = useCallback(async (run = "") => {
    const data = await getJson<{ current_batch: string; batches: BatchEntry[] }>("/api/evaluation/batches");
    setBatches(data.batches ?? []);
    await openBatch(data.current_batch, run);
  }, [openBatch]);

  useEffect(() => {
    loadHistory(latestRunId).catch(() => undefined);
  }, [loadHistory, latestRunId]);

  const startNew = async () => {
    setProblem("");
    try {
      const response = await fetch("/api/evaluation/batches", { method: "POST" });
      if (!response.ok) throw new Error(`Could not start a new benchmark (${response.status})`);
      await loadHistory();
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const chooseBatch = (id: string) => {
    setProblem("");
    openBatch(id).catch((reason) => setProblem(reason.message));
  };

  if (!summary) return error ? <div className="panel"><h2>Reports</h2><div className="status error">{error}</div></div> : null;
  const stats: [string, number | string][] = [
    ["Runs", summary.total_runs],
    ["Attempts", summary.total_tasks],
    ["Success", summary.successful],
    ["Partial", summary.partial],
    ["Failed", summary.failed],
    ["Unsafe", summary.unsafe],
    ["Timed out", summary.timed_out],
    ["Not scored", summary.not_evaluable],
  ];
  return (
    <div className="panel evaluation" aria-label="Reports">
      <div className="stream-bar">
        <h2>Benchmark reports</h2>
        <span className="report-links">
          {summary.total_tasks > 0 && <a href={`/api/reports/batches/${summary.batch_id}?format=html`} target="_blank" rel="noreferrer">HTML</a>}
          {summary.total_tasks > 0 && <a href={`/api/reports/batches/${summary.batch_id}`} target="_blank" rel="noreferrer">JSON</a>}
          <button disabled={locked} onClick={startNew}>New benchmark</button>
        </span>
      </div>
      <label className="history">Benchmark history
        <select aria-label="Benchmark history" value={batchId} onChange={(event) => chooseBatch(event.target.value)}>
          {batches.map((item) => (
            <option key={item.batch_id} value={item.batch_id}>
              {item.is_current ? "Current · " : ""}{when(item.last_started_at)} · {item.runs} run{item.runs === 1 ? "" : "s"} · {item.attempts} attempt{item.attempts === 1 ? "" : "s"} · {item.batch_score == null ? "not scored" : `${item.batch_score}/100`}
            </option>
          ))}
        </select>
      </label>
      {(problem || error) && <div className="status error" style={{ marginTop: 8 }}>{problem || error}</div>}
      {summary.total_tasks === 0 ? <p className="hint">Run a task to start this benchmark.</p> : (
        <>
          <div className="score-head">
            <div className="score-value">{summary.batch_score == null ? "Not scored" : <>{summary.batch_score}<small>/100</small></>}</div>
            <div>
              <p>Success rate {show(summary.success_rate, "%")} · score coverage {summary.score_coverage}%</p>
              <p className="hint">{when(summary.first_started_at)} to {when(summary.last_started_at)} · median {show(summary.median_score)} · high {show(summary.highest_score)} · low {show(summary.lowest_score)} · penalties {summary.total_penalties}</p>
            </div>
          </div>
          <div className="stat-grid">{stats.map(([name, value]) => <div key={name}><strong>{value}</strong><span>{name}</span></div>)}</div>
          <table>
            <thead><tr><th>Time</th><th>Instruction</th><th>Attempts</th><th>Outcome</th><th>Score</th></tr></thead>
            <tbody>{summary.runs.map((item) => (
              <tr key={item.run_id} className={`run-row ${item.run_id === runId ? "open-run" : ""}`} onClick={() => setRunId(item.run_id)}>
                <td>{when(item.started_at)}</td><td>{item.instruction}</td>
                <td>{item.attempts_succeeded}/{item.attempts_total}</td>
                <td><span className={`pill small ${outcomeClass(item.outcome)}`}>{label(item.outcome)}</span></td>
                <td>{show(item.final_score)}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="hint">{summary.sample_note}</p>
          {runId && <RunView key={runId} runId={runId} />}
        </>
      )}
    </div>
  );
}

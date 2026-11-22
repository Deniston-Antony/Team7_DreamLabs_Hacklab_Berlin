import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { SpeedButtons, VideoPlayer } from "./VideoPlayer";

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
type Finding = { title: string; evidence: string; action: string };
type StageCount = { stage: string; label: string; count: number };
type AttemptFact = {
  attempt: number;
  instruction: string;
  scene: number;
  scene_label: string;
  stage: string;
  stage_label: string;
  closest_cm: number | null;
  lift_cm: number | null;
  contact_s: number | null;
  final_to_goal_cm: number | null;
  movement_s: number | null;
  decision_s: number | null;
  wait_s: number | null;
  timer_s: number | null;
  score: number | null;
};
type SceneCell = { scene: number; score: number | null; outcome: string | null; stage: string | null; attempt: number | null };
type Comparison = {
  comparable: boolean;
  scenes: { scene: number; label: string; average_score: number | null; attempts: number; successes: number; layout_check: { checked: boolean; same: boolean | null; max_difference_cm: number | null } }[];
  tasks: { instruction: string; average_score: number | null; successes: number; attempts: number; cells: SceneCell[] }[];
  best_task: string | null;
  best_scene: number | null;
  hardest_scene: number | null;
  same_layouts_verified: boolean | null;
  improvements: Finding[];
};
type VideoAnalysis = {
  status: string;
  detail: string;
  frames: number;
  target: string | null;
  target_seen: number;
  moved_objects: string[];
  objects: Record<string, { seen: number; change: number | null; moved: boolean }>;
};
type VisionObject = { score: number; detected: boolean; box: number[] | null; simulator_visible: boolean | null };
type Vision = {
  status: string;
  detail?: string;
  model?: string;
  target?: string | null;
  target_detected_in?: string[];
  destination?: string | null;
  destination_detected_in?: string[];
  agreement?: { matching: number; total: number };
  cameras?: Record<string, { url: string; width: number; height: number; objects: Record<string, VisionObject> }>;
  video?: VideoAnalysis | null;
};
type AttemptReport = {
  task: { task_id: string; attempt: number; where: string; instruction: string; scene_number?: number; scene_label?: string | null };
  execution: { status: string; duration_seconds: number | null; action_count: number | null; model_calls: number | null };
  evidence: { videos: string[]; vision?: Vision | null };
  result: Result;
};
type AttemptRow = { attempt: number; where: string; instruction?: string; scene_id?: string; execution_status: string; outcome: string; score: number | null; summary: string };
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
  task_groups?: { instruction: string; attempts: number; succeeded: number; average_score: number | null }[];
  attempts: AttemptRow[];
  recommendations: string[];
};
type RunReport = { run: RunSummary; grid_url: string | null; attempts: AttemptReport[]; insights?: { stages: StageCount[]; attempts: AttemptFact[]; findings: Finding[] }; comparison?: Comparison };
type BatchEntry = {
  batch_id: string;
  name?: string | null;
  is_current: boolean;
  first_started_at: number | null;
  last_started_at: number | null;
  runs: number;
  attempts: number;
  batch_score: number | null;
  number: number;
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
  task_ranking?: { instruction: string; attempts: number; successes: number; average_score: number | null }[];
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

function VisionCheck({ vision }: { vision?: Vision | null }) {
  if (!vision) return <><h3>Vision check</h3><p className="hint">This older report has no vision check.</p></>;
  if (vision.status === "running") return <><h3>Vision check</h3><p className="hint">Analysing this run's camera frames and video...</p></>;
  if (vision.status !== "done" || !vision.cameras) return <><h3>Vision check</h3><p className="hint">{vision.detail ?? "Unavailable."}</p></>;
  const cameras = Object.entries(vision.cameras);
  const names = Object.keys(cameras[0]?.[1].objects ?? {});
  return (
    <>
      <h3>Vision check (not scored)</h3>
      <p>
        Target <strong>{vision.target ?? "n/a"}</strong>: {vision.target_detected_in?.length ? `detected in ${vision.target_detected_in.join(", ")}` : "not detected"}
        {vision.destination ? <> · Destination <strong>{vision.destination}</strong>: {vision.destination_detected_in?.length ? `detected in ${vision.destination_detected_in.join(", ")}` : "not detected"}</> : null}
      </p>
      {!!vision.agreement?.total && <p className="hint">First-frame detections match MuJoCo visibility in {vision.agreement.matching} of {vision.agreement.total} checks.</p>}
      {vision.video && (
        <div className="video-analysis">
          <strong>From this run's video</strong> <span className={`pill small ${statusClass(vision.video.status)}`}>{label(vision.video.status)}</span>
          <p>{vision.video.detail}</p>
          <table>
            <thead><tr><th>Object</th><th>Located in frames</th><th>Video shows</th></tr></thead>
            <tbody>{Object.entries(vision.video.objects).map(([name, item]) => (
              <tr key={name} className={name === vision.video!.target ? "selected" : ""}>
                <td>{name}</td><td>{item.seen} of {vision.video!.frames}</td><td>{item.change == null ? "Not enough detections" : item.moved ? "Moved" : "Stayed in place"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <div className="vision-frames">
        {cameras.map(([camera, value]) => (
          <figure key={camera}>
            <div className="vision-frame">
              <img src={value.url} alt={`${camera} camera frame with detections`} />
              {Object.entries(value.objects).filter(([, item]) => item.box).map(([name, item]) => (
                <span
                  key={name}
                  className={name === vision.target ? "target" : ""}
                  style={{
                    left: `${(item.box![0] / value.width) * 100}%`,
                    top: `${(item.box![1] / value.height) * 100}%`,
                    width: `${((item.box![2] - item.box![0]) / value.width) * 100}%`,
                    height: `${((item.box![3] - item.box![1]) / value.height) * 100}%`,
                  }}
                ><em>{name} {Math.round(item.score * 100)}%</em></span>
              ))}
            </div>
            <figcaption>{camera}</figcaption>
          </figure>
        ))}
      </div>
      <table>
        <thead><tr><th>Object</th>{cameras.map(([camera]) => <th key={camera}>{camera}</th>)}</tr></thead>
        <tbody>{names.map((name) => (
          <tr key={name}>
            <td>{name}</td>
            {cameras.map(([camera, value]) => {
              const item = value.objects[name];
              return <td key={camera}>{item.detected ? `Detected ${Math.round(item.score * 100)}%` : "Not detected"}{item.simulator_visible == null ? "" : item.simulator_visible ? " · in view" : " · out of view"}</td>;
            })}
          </tr>
        ))}</tbody>
      </table>
    </>
  );
}

function AttemptView({ report }: { report: AttemptReport }) {
  const result = report.result;
  const checks = result.verification.flatMap((item) => item.checks);
  return (
    <>
      {report.evidence.videos[0] ? (
        <VideoPlayer className="report-video" src={report.evidence.videos[0]} />
      ) : <p className="hint">No playable video was saved for this attempt.</p>}
      <ScoreHead score={result.score} outcome={result.outcome}>
        <p>{result.summary}</p>
        <p className="hint">
          {report.task.instruction} · {report.task.scene_label ?? `Scene ${report.task.scene_number ?? report.task.attempt}`} · {report.task.where === "live" ? "live" : "parallel"} run · coverage {result.coverage}% · {show(report.execution.action_count)} actions · {show(report.execution.model_calls)} Dream-1 calls · {result.config_version}
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
          <td>{name === "perception" ? (item.source?.includes("video") ? "Perception (from video)" : "Perception (gripper motion)") : label(name)}</td>
          <td><span className={`pill small ${statusClass(item.status)}`}>{label(item.status)}</span></td>
          <td>{item.detail}</td>
        </tr>
      ))}</tbody></table>
      <VisionCheck vision={report.evidence.vision} />
      <div className="report-columns">
        <List title="What went well" items={result.went_well} />
        <List title="What went wrong" items={result.went_wrong} />
        <List title="Recommendations for this attempt" items={result.recommendations} />
        <List title="Missing evidence" items={result.missing_evidence} />
      </div>
    </>
  );
}

function FindingList({ title, items }: { title: string; items: Finding[] }) {
  if (!items.length) return null;
  return (
    <>
      <h3>{title}</h3>
      <div className="findings">
        {items.map((item) => (
          <article key={item.title}>
            <strong>{item.title}</strong>
            <p>{item.evidence}</p>
            <p className="next-step">Next: {item.action}</p>
          </article>
        ))}
      </div>
    </>
  );
}

function ComparisonView({ comparison, openAttempt }: { comparison: Comparison; openAttempt: (attempt: number) => void }) {
  return (
    <>
      <h3>Task and scene comparison</h3>
      <p className="hint">
        {comparison.best_task ? <>Best task: <strong>{comparison.best_task}</strong>. </> : null}
        {comparison.best_scene != null ? <>Best scene: <strong>Scene {comparison.best_scene}</strong>. </> : null}
        {comparison.hardest_scene != null ? <>Hardest scene: <strong>Scene {comparison.hardest_scene}</strong>. </> : null}
        {comparison.same_layouts_verified === true ? "Object positions were verified as the same for every task in each scene." : comparison.same_layouts_verified === false ? "Some tasks in a scene did not start from the same object positions." : ""}
      </p>
      <div className="table-scroll">
        <table className="comparison-table">
          <thead><tr><th>Task</th>{comparison.scenes.map((scene) => <th key={scene.scene} title={scene.label}>Scene {scene.scene}</th>)}<th>Average</th></tr></thead>
          <tbody>
            {comparison.tasks.map((item) => (
              <tr key={item.instruction}>
                <td>{item.instruction}</td>
                {item.cells.map((cell) => (
                  <td key={cell.scene}>
                    {cell.attempt == null ? "n/a" : (
                      <button className={`cell-link ${outcomeClass(cell.outcome ?? "")}`} title={cell.stage ?? ""} onClick={() => openAttempt(cell.attempt!)}>
                        {show(cell.score)}
                      </button>
                    )}
                  </td>
                ))}
                <td><strong>{show(item.average_score)}</strong></td>
              </tr>
            ))}
            <tr>
              <td><strong>Scene average</strong></td>
              {comparison.scenes.map((scene) => <td key={scene.scene}><strong>{show(scene.average_score)}</strong></td>)}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <FindingList title="Where to improve across tasks and scenes" items={comparison.improvements} />
    </>
  );
}

function FinalView({ report, openAttempt }: { report: RunReport; openAttempt: (attempt: number) => void }) {
  const run = report.run;
  const clips = report.attempts.filter((item) => item.evidence.videos[0]);
  const [allSpeed, setAllSpeed] = useState(1);
  const insights = report.insights;
  return (
    <>
      <ScoreHead score={run.final_score} outcome={run.outcome}>
        <p>{run.attempts_succeeded} of {run.attempts_total} attempts succeeded · {run.attempts_scored} scored</p>
        <p className="hint">Final score is the average of scored attempts · best {show(run.best_score)} · worst {show(run.worst_score)}</p>
      </ScoreHead>
      {report.grid_url && (
        <section className="combined-video">
          <div className="stream-bar">
            <h3>Combined video</h3>
            <a href={report.grid_url} target="_blank" rel="noreferrer">Open combined video</a>
          </div>
          <VideoPlayer className="report-video" src={report.grid_url} />
        </section>
      )}
      {clips.length > 0 && (
        <>
          {clips.length > 1 && <div className="all-speed"><span>Speed for all videos</span><SpeedButtons speed={allSpeed} onChange={setAllSpeed} label="Speed for all videos" /></div>}
          <div className="attempt-videos">
            {clips.map((item) => (
              <figure key={item.task.task_id} className={item.result.outcome === "SUCCESS" ? "passed" : item.result.outcome === "NOT_EVALUABLE" ? "" : "failed"}>
                <VideoPlayer src={item.evidence.videos[0]} loop groupSpeed={allSpeed} />
                <figcaption><strong>Attempt {item.task.attempt}</strong> · Scene {item.task.scene_number ?? item.task.attempt}: {label(item.result.outcome)} · {show(item.result.score)}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {report.comparison?.comparable && <ComparisonView comparison={report.comparison} openAttempt={openAttempt} />}
      {insights && insights.stages.length > 0 && (
        <>
          <h3>Where each attempt ended</h3>
          <div className="stage-row">{insights.stages.map((item) => <span key={item.stage} className={`pill ${item.stage === "success" ? "good" : item.stage === "not_scored" ? "neutral" : "bad"}`}>{item.label}: {item.count}</span>)}</div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Attempt</th><th>Task</th><th>Scene</th><th>Result</th><th>Closest (cm)</th><th>Lift (cm)</th><th>Contact (s)</th><th>To goal (cm)</th><th>Moved (s)</th><th>Decision (s)</th><th>Wait (s)</th><th>Total (s)</th><th>Score</th></tr></thead>
              <tbody>{insights.attempts.map((item) => (
                <tr className="run-row" key={item.attempt} onClick={() => openAttempt(item.attempt)}>
                  <td>{item.attempt}</td><td>{item.instruction}</td><td>{item.scene}</td><td>{item.stage_label}</td>
                  <td>{show(item.closest_cm)}</td><td>{show(item.lift_cm)}</td><td>{show(item.contact_s)}</td><td>{show(item.final_to_goal_cm)}</td><td>{show(item.movement_s)}</td><td>{show(item.decision_s)}</td><td>{show(item.wait_s)}</td><td>{show(item.timer_s)}</td><td>{show(item.score)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
      {insights ? <FindingList title="What the runs show and what to improve" items={insights.findings} /> : <List title="Run recommendations" items={run.recommendations} />}
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

  useEffect(() => {
    if (!report?.attempts.some((item) => item.evidence.vision?.status === "running")) return;
    const timer = window.setTimeout(() => {
      getJson<RunReport>(`/api/reports/runs/${runId}`).then(setReport).catch(() => undefined);
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [report, runId]);

  if (error) return <div className="status error">{error}</div>;
  if (!report) return <p className="hint">Loading report...</p>;
  const current = report.attempts.find((item) => item.task.attempt === tab);
  const needsVideoCheck = report.attempts.some((item) => item.evidence.videos.length > 0 && !item.evidence.vision?.video && item.evidence.vision?.status !== "running");
  const analyse = async () => {
    await fetch(`/api/reports/runs/${runId}/analyze-videos`, { method: "POST" });
    setReport(await getJson<RunReport>(`/api/reports/runs/${runId}`));
  };
  return (
    <div>
      <div className="stream-bar" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0 }}>{report.run.instruction} <span className="muted">· {when(report.run.started_at)}</span></h3>
        <span className="report-links">
          {needsVideoCheck && <button onClick={() => void analyse()}>Analyse videos</button>}
          <a href={`/api/reports/runs/${runId}?format=html`} target="_blank" rel="noreferrer">HTML</a>
          <a href={`/api/reports/runs/${runId}`} target="_blank" rel="noreferrer">JSON</a>
        </span>
      </div>
      <div className="report-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 0} className={tab === 0 ? "active" : ""} onClick={() => setTab(0)}>Final report</button>
        {report.attempts.map((item) => (
          <button role="tab" key={item.task.attempt} aria-selected={tab === item.task.attempt} className={tab === item.task.attempt ? "active" : ""} onClick={() => setTab(item.task.attempt)}>
            Attempt {item.task.attempt}{(report.comparison?.tasks.length ?? 0) > 1 ? ` · S${item.task.scene_number ?? item.task.attempt}` : ""}{" "}
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

export function BenchmarksPage({ latestRunId, locked }: { latestRunId: string; locked: boolean }) {
  const [batches, setBatches] = useState<BatchEntry[]>([]);
  const [batchId, setBatchId] = useState("");
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [runId, setRunId] = useState("");
  const [problem, setProblem] = useState("");
  const [editing, setEditing] = useState("");
  const [draft, setDraft] = useState("");

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
    loadHistory(latestRunId).catch((reason) => setProblem(reason.message));
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

  const title = (item?: BatchEntry) => item?.name || `Benchmark ${item?.number ?? ""}`;

  const saveName = async (id: string) => {
    const name = draft.trim();
    if (!name) {
      setProblem("The benchmark name cannot be empty.");
      return;
    }
    try {
      const response = await fetch(`/api/evaluation/batches/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(`Could not rename the benchmark (${response.status})`);
      const saved = await response.json();
      setBatches((items) => items.map((item) => (item.batch_id === id ? { ...item, name: saved.name } : item)));
      setEditing("");
      setProblem("");
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const active = batches.find((item) => item.batch_id === batchId);
  const stats: [string, number | string][] = summary ? [
    ["Test runs", summary.total_runs],
    ["Attempts", summary.total_tasks],
    ["Success", summary.successful],
    ["Partial", summary.partial],
    ["Failed", summary.failed],
    ["Unsafe", summary.unsafe],
    ["Timed out", summary.timed_out],
    ["Not scored", summary.not_evaluable],
  ] : [];
  return (
    <div className="benchmark-page">
      <aside className="panel benchmark-list" aria-label="Benchmarks">
        <div className="stream-bar">
          <h2>Benchmarks</h2>
          <button disabled={locked} onClick={startNew}>New</button>
        </div>
        {batches.map((item) => (
          <div className={`benchmark-row ${item.batch_id === batchId ? "active" : ""}`} key={item.batch_id}>
            {editing === item.batch_id ? (
              <form className="benchmark-rename" onSubmit={(event) => { event.preventDefault(); void saveName(item.batch_id); }}>
                <input
                  aria-label={`Name for benchmark ${item.number}`}
                  autoFocus
                  maxLength={60}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Escape") setEditing(""); }}
                />
                <button type="submit" className="primary">Save</button>
                <button type="button" onClick={() => setEditing("")}>Cancel</button>
              </form>
            ) : (
              <>
                <button className="benchmark-open" onClick={() => chooseBatch(item.batch_id)}>
                  <strong>{title(item)}{item.is_current ? " · Current" : ""}</strong>
                  <span>{when(item.first_started_at)}</span>
                  <span>{item.runs} test run{item.runs === 1 ? "" : "s"} · {item.attempts} attempt{item.attempts === 1 ? "" : "s"}{item.batch_score == null ? "" : ` · ${item.batch_score}/100`}</span>
                </button>
                <button className="benchmark-edit" aria-label={`Rename ${title(item)}`} onClick={() => { setDraft(title(item)); setEditing(item.batch_id); setProblem(""); }}>Rename</button>
              </>
            )}
          </div>
        ))}
      </aside>
      <section className="panel evaluation" aria-label="Reports">
        {problem && <div className="status error">{problem}</div>}
        {summary && (
          <>
            <div className="stream-bar">
              <h2>{title(active)}</h2>
              {summary.total_tasks > 0 && (
                <span className="report-links">
                  <a href={`/api/reports/batches/${summary.batch_id}?format=html`} target="_blank" rel="noreferrer">HTML</a>
                  <a href={`/api/reports/batches/${summary.batch_id}`} target="_blank" rel="noreferrer">JSON</a>
                </span>
              )}
            </div>
            {summary.total_tasks === 0 ? <p className="hint">No test runs yet. Run a task on the Simulator page to add it here.</p> : (
              <>
                <div className="score-head">
                  <div className="score-value">{summary.batch_score == null ? "Not scored" : <>{summary.batch_score}<small>/100</small></>}</div>
                  <div>
                    <p>Success rate {show(summary.success_rate, "%")} · score coverage {summary.score_coverage}%</p>
                    <p className="hint">{when(summary.first_started_at)} to {when(summary.last_started_at)} · median {show(summary.median_score)} · high {show(summary.highest_score)} · low {show(summary.lowest_score)} · penalties {summary.total_penalties}</p>
                  </div>
                </div>
                <div className="stat-grid">{stats.map(([name, value]) => <div key={name}><strong>{value}</strong><span>{name}</span></div>)}</div>
                {(summary.task_ranking?.length ?? 0) > 1 && (
                  <>
                    <h3>Best tasks in this benchmark</h3>
                    <table><tbody>{summary.task_ranking!.map((item, index) => (
                      <tr key={item.instruction}><td>{index + 1}</td><td>{item.instruction}</td><td>{item.successes}/{item.attempts} succeeded</td><td><strong>{show(item.average_score)}</strong></td></tr>
                    ))}</tbody></table>
                  </>
                )}
                <h3>Test runs in this benchmark</h3>
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
          </>
        )}
      </section>
    </div>
  );
}

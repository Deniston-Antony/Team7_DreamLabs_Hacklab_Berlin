import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEventHandler, ReactNode } from "react";
import { RunProgress, runTone } from "./RunProgress";
import { JudgePicker } from "./JudgePicker";
import type { JudgeId, JudgeOption } from "./JudgePicker";

type PlannedTest = { test: number; kind: string; label: string; change: string; suggestion: string; prompt: string; source_attempt: number; max_seconds: number };
type TestWorker = { attempt: number; name?: string; env?: string; state: string; timer_s?: number; limit_s?: number };
type Stats = { average_score: number | null; successes: number; attempts: number };
type ResultRow = {
  attempt: number;
  test: number;
  kind: string;
  label: string;
  change: string;
  suggestion: string;
  source_attempt: number;
  before_score: number | null;
  after_score: number | null;
  after_outcome: string;
  prompt?: string;
  judge?: JudgeId | null;
  judge_verdict?: string | null;
  effect: number | null;
  verdict: "control" | "helped" | "worse" | "no_change" | "not_scored";
};
type Comparison = { before: Stats; after: Stats; control: Stats; control_change: number | null; rows: ResultRow[]; recommendation: string; note: string; best_attempt: number | null; judge?: JudgeId | null; judging?: boolean };
type SavedResult = { run_id: string; started_at: number | null; comparison: Comparison };
type SuggestionState = {
  ready: boolean;
  reason: string | null;
  tests: PlannedTest[];
  untested: number[];
  cost_per_120s: number;
  running: { task_id: string; phase: string } | null;
  results: SavedResult[];
  source_judge?: JudgeId;
};
export type TestViewerState = {
  frameUrl: string;
  camera: string;
  cameras: string[];
  cameraLabels: Record<string, string>;
  chooseCamera: (name: string) => void;
  attempt: number;
  chooseAttempt: (attempt: number) => void;
  workers: TestWorker[];
  refinementOf?: string;
  status: string;
  fps: number;
  onPointerDown: PointerEventHandler<HTMLImageElement>;
  onPointerMove: PointerEventHandler<HTMLImageElement>;
  onPointerUp: PointerEventHandler<HTMLImageElement>;
  onWheel: (event: WheelEvent) => void;
};

const VERDICT: Record<ResultRow["verdict"], string> = {
  control: "● Control",
  helped: "▲ Helped",
  worse: "▼ Worse",
  no_change: "– No clear change",
  not_scored: "? Not scored",
};
const STATE_LABEL: Record<string, string> = {
  starting: "Starting",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  error: "Error",
  ended: "Stopped",
  stopped: "Stopped",
};
const show = (value: number | null | undefined) => (value == null ? "n/a" : `${value}`);
const signed = (value: number | null) => (value == null ? "n/a" : `${value > 0 ? "+" : ""}${value}`);
const testName = (row: { test: number; kind: string }) => `Test ${row.test}${row.kind === "control" ? " (control)" : ""}`;
const when = (seconds: number | null) =>
  seconds == null ? "" : new Date(seconds * 1000).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function Stat({ title, stats }: { title: string; stats: Stats }) {
  return (
    <div>
      <strong>{show(stats.average_score)}<small>/100</small></strong>
      <span>{title}</span>
      <span>{stats.successes} of {stats.attempts} succeeded</span>
    </div>
  );
}

function BeforeAfterChart({ rows }: { rows: ResultRow[] }) {
  const bar = (name: string, score: number | null, className: string) => (
    <div className="ba-bar-row">
      <span className="ba-series">{name}</span>
      <span className="ba-track">{score != null && <span className={`ba-bar ${className}`} style={{ width: `${Math.max(score, 1)}%` }} />}</span>
      <span className="ba-value">{show(score)}</span>
    </div>
  );
  return (
    <figure className="ba-chart" aria-label="Score before and after each test, from 0 to 100">
      <figcaption>
        <strong>Score before and after each change</strong>
        <span className="ba-legend"><span><i className="before" />Before</span><span><i className="after" />After</span></span>
      </figcaption>
      {rows.map((row) => (
        <div className="ba-row" tabIndex={0} key={row.attempt}>
          <div className="ba-label"><strong>{testName(row)}</strong><span>{row.change}</span></div>
          <div className="ba-bars">
            {bar("Before", row.before_score, "before")}
            {bar("After", row.after_score, "after")}
          </div>
          <span className={`ba-verdict ${row.verdict}`}>{VERDICT[row.verdict]}</span>
          <span className="ba-tip" role="tooltip">
            <strong>{testName(row)}</strong> from attempt {row.source_attempt}<br />
            Before {show(row.before_score)} · After {show(row.after_score)}<br />
            {row.kind === "control" ? "Change with no edit" : "Effect after control"}: {signed(row.effect)} points
          </span>
        </div>
      ))}
      <div className="ba-axis" aria-hidden="true"><span>0</span><span>50</span><span>100</span></div>
    </figure>
  );
}

function ResultView({ comparison }: { comparison: Comparison }) {
  return (
    <>
      <div className={`suggestion-answer ${comparison.best_attempt ? "good" : ""}`}>{comparison.recommendation}</div>
      <div className="stat-grid suggestion-stats">
        <Stat title="Before suggestions" stats={comparison.before} />
        <Stat title="After suggestions" stats={comparison.after} />
        <Stat title="Unchanged control" stats={comparison.control} />
      </div>
      <BeforeAfterChart rows={comparison.rows} />
      <div className="table-scroll">
        <table>
          <thead><tr><th>Test</th><th>One change</th><th>Suggestion tested</th><th>From</th><th>Before</th><th>After</th><th>Effect vs control</th><th>Result</th><th>Judge says</th></tr></thead>
          <tbody>{comparison.rows.map((row) => (
            <tr key={row.attempt}>
              <td>{testName(row)}</td><td>{row.change}{row.prompt && <small className="test-prompt">Prompt: {row.prompt}</small>}</td><td>{row.suggestion}</td><td>Attempt {row.source_attempt}</td>
              <td>{show(row.before_score)}</td><td>{show(row.after_score)}</td><td>{signed(row.effect)}</td><td>{VERDICT[row.verdict]}</td><td>{row.judge_verdict === "RUNNING" ? "Judging..." : row.judge_verdict ?? "n/a"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="hint">{comparison.note}</p>
    </>
  );
}

function TestViewer({ viewer }: { viewer: TestViewerState }) {
  const image = useRef<HTMLDivElement | null>(null);
  const { workers, attempt, chooseAttempt, onWheel } = viewer;

  useEffect(() => {
    if (workers.length && !workers.some((worker) => worker.attempt === attempt)) chooseAttempt(workers[0].attempt);
  }, [workers, attempt, chooseAttempt]);

  useEffect(() => {
    const element = image.current;
    if (!element) return;
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const shown = workers.find((worker) => worker.attempt === attempt);
  return (
    <div className="viewer test-viewer">
      <nav className="run-tabs" aria-label="Suggestion tests">
        <div className="run-tabs-title">Tests</div>
        {workers.map((worker) => (
          <button key={worker.attempt} className={`run-tab ${attempt === worker.attempt ? "active" : ""}`} aria-pressed={attempt === worker.attempt} onClick={() => chooseAttempt(worker.attempt)}>
            <span className="run-tab-name">{worker.name ?? `Test ${worker.attempt - 1}`}</span>
            {worker.env && <span className="run-tab-env">{worker.env}</span>}
            <span className={`run-tab-state ${worker.state}`}>{STATE_LABEL[worker.state] ?? worker.state}</span>
            <RunProgress seconds={worker.timer_s ?? 0} limit={worker.limit_s ?? 0} tone={runTone(worker.state)} />
          </button>
        ))}
      </nav>
      <div className="viewer-main">
        <div className="stream-bar">
          <div className="cameras">
            {viewer.cameras.map((name) => (
              <button key={name} className={viewer.camera === name ? "active" : ""} onClick={() => viewer.chooseCamera(name)}>{viewer.cameraLabels[name]}</button>
            ))}
          </div>
          <div className="live-status" role="status">
            <span className="dot on" />{viewer.status}<span className="muted">{viewer.fps} FPS</span>
          </div>
        </div>
        <div ref={image}>
          {viewer.frameUrl ? (
            <img
              className="main-view"
              alt={`${shown?.name ?? "Test"}, ${viewer.cameraLabels[viewer.camera]} camera`}
              src={viewer.frameUrl}
              draggable={false}
              onContextMenu={(event) => event.preventDefault()}
              onPointerDown={viewer.onPointerDown}
              onPointerMove={viewer.onPointerMove}
              onPointerUp={viewer.onPointerUp}
              onPointerCancel={viewer.onPointerUp}
            />
          ) : <div className="main-view" />}
        </div>
        <p className="hint">{shown?.env ? `${shown.name}: ${shown.env}. ` : ""}Ctrl + drag rotates the 3D view, and two fingers zoom.</p>
      </div>
    </div>
  );
}

export function SuggestionTests({ runId, locked, renderRun, viewer, judge, judges, onJudge }: { runId: string; locked: boolean; renderRun: (id: string) => ReactNode; viewer: TestViewerState; judge: JudgeId; judges: JudgeOption[]; onJudge: (judge: JudgeId) => void }) {
  const [data, setData] = useState<SuggestionState | null>(null);
  const [problem, setProblem] = useState("");
  const [starting, setStarting] = useState(false);
  const [openRun, setOpenRun] = useState("");
  const [chosen, setChosen] = useState<Set<number> | null>(null);
  const [round, setRound] = useState("");
  const allBox = useRef<HTMLInputElement | null>(null);
  const lastPlan = useRef("");
  const base = `/api/reports/runs/${runId}/suggestion-tests`;
  const url = `${base}?judge=${judge}`;

  const load = useCallback(async () => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Could not load suggestion tests (${response.status})`);
    setData(await response.json());
  }, [url]);

  useEffect(() => {
    load().catch((reason) => setProblem(reason.message));
  }, [load, locked]);

  const waiting = !!data?.running || !!data?.reason?.includes("analysing") || !!data?.results.some((item) => item.comparison.judging);
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => load().catch(() => undefined), data?.running ? 1000 : 2500);
    return () => window.clearTimeout(timer);
  }, [waiting, data, load]);

  const planKey = data?.ready ? data.tests.map((test) => `${test.test}:${test.kind}:${test.source_attempt}`).join("|") : "";
  useEffect(() => {
    if (!planKey) return;
    if (lastPlan.current && lastPlan.current !== planKey) setChosen(null); // a different judge can plan different tests
    lastPlan.current = planKey;
  }, [planKey]);

  const newest = data?.results[0]?.run_id ?? "";
  useEffect(() => {
    setRound("");
    setOpenRun("");
  }, [newest]);

  const tests = data?.tests ?? [];
  const running = !!data?.running;
  const isChosen = (number: number) => running || chosen === null || chosen.has(number);
  const picked = tests.filter((test) => isChosen(test.test));
  const everyChosen = tests.length > 0 && picked.length === tests.length;
  useEffect(() => {
    if (allBox.current) allBox.current.indeterminate = picked.length > 0 && !everyChosen;
  }, [picked.length, everyChosen]);

  const toggle = (number: number) =>
    setChosen((current) => {
      const next = new Set(current ?? tests.map((test) => test.test));
      if (next.has(number)) next.delete(number);
      else next.add(number);
      return next;
    });
  const toggleAll = () => setChosen(everyChosen ? new Set() : new Set(tests.map((test) => test.test)));

  const start = async () => {
    setStarting(true);
    setProblem("");
    try {
      const response = await fetch(base, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tests: picked.map((test) => test.test), judge }) });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `Could not start the tests (${response.status})`);
      }
      await load();
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    if (!data?.running) return;
    await fetch(`/api/tasks/${data.running.task_id}/cancel`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await load();
  };

  if (!data) return problem ? <div className="status error">{problem}</div> : <p className="hint">Loading tests...</p>;
  const cost = (picked.reduce((total, test) => total + test.max_seconds, 0) / 120) * data.cost_per_120s;
  const hasControl = picked.some((test) => test.kind === "control");
  const result = data.results.find((item) => item.run_id === round) ?? data.results[0];
  const showViewer = viewer.refinementOf === runId && viewer.workers.length > 0;
  return (
    <section className="suggestion-panel" aria-label="Suggestion testing">
      <div className="stream-bar">
        <h3>Test the suggestions</h3>
        {running ? (
          <button className="danger" onClick={() => void stop()}>Stop tests</button>
        ) : (
          <button className="primary test-run-button" disabled={!data.ready || locked || starting || !picked.length || judges.find((item) => item.id === judge)?.available === false} onClick={() => void start()}>
            {data.ready ? `Run ${picked.length} selected test${picked.length === 1 ? "" : "s"} · up to $${cost.toFixed(2)}` : "Run tests"}
          </button>
        )}
      </div>
      <JudgePicker judge={judge} judges={judges} onChange={onJudge} disabled={running} />
      {!running && data.source_judge && data.source_judge !== judge && <p className="hint test-warning">The original run used the {data.source_judge === "claude" ? "Claude" : "offline"} judge. These tests use this judge's prompt, so compare each change with the unchanged control.</p>}
      {problem && <div className="status error">{problem}</div>}
      {data.reason && !running && <p className="hint">{data.reason}</p>}
      {tests.length > 0 && (
        <>
          <div className="table-scroll">
            <table className="test-picker">
              <thead>
                <tr>
                  <th>
                    <label className="check-all">
                      <input ref={allBox} type="checkbox" aria-label={everyChosen ? "Deselect all tests" : "Select all tests"} checked={everyChosen} disabled={running} onChange={toggleAll} />
                      {everyChosen ? "Deselect all" : "Select all"}
                    </label>
                  </th>
                  <th>Test</th><th>One change</th><th>From</th><th>Time</th><th>Suggestion</th>
                </tr>
              </thead>
              <tbody>{tests.map((test) => (
                <tr key={test.test} className={isChosen(test.test) ? "chosen" : ""}>
                  <td><input type="checkbox" aria-label={`Run ${testName(test)}`} checked={isChosen(test.test)} disabled={running} onChange={() => toggle(test.test)} /></td>
                  <td>{testName(test)}</td><td>{test.change}<small className="test-prompt">Prompt: {test.prompt}</small></td><td>Attempt {test.source_attempt}</td><td>{test.max_seconds} s</td><td>{test.suggestion}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {!running && !picked.length && <p className="hint">Select at least one test to run.</p>}
          {!running && picked.length > 0 && !hasControl && <p className="hint test-warning">No unchanged control is selected, so a gain could be normal run-to-run variation.</p>}
          {!running && data.untested.length > 0 && <p className="hint">Not enough parallel runs to test attempt{data.untested.length === 1 ? "" : "s"} {data.untested.join(", ")}.</p>}
          {!running && <p className="hint">Each test starts from the failed attempt's saved scene and makes one change. These tests do not change the benchmark score.</p>}
        </>
      )}
      {showViewer && <TestViewer viewer={viewer} />}
      {result && (
        <>
          <div className="stream-bar result-head">
            <h3>Before and after</h3>
            {data.results.length > 1 && (
              <label className="round-picker">Test round
                <select aria-label="Test round" value={result.run_id} onChange={(event) => { setRound(event.target.value); setOpenRun(""); }}>
                  {data.results.map((item, index) => (
                    <option key={item.run_id} value={item.run_id}>Round {data.results.length - index} · {when(item.started_at)} · {item.comparison.rows.length} test{item.comparison.rows.length === 1 ? "" : "s"}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <ResultView comparison={result.comparison} />
          <button onClick={() => setOpenRun(openRun ? "" : result.run_id)}>{openRun ? "Hide test videos and reports" : "Open test videos and reports"}</button>
          {openRun && renderRun(openRun)}
        </>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";

// One Dream-1 request of a run, as GET /api/tasks/{id}/dream-log/{run} serves it (SortFlow/roundtrip_log.py).
type DreamLogEntry = {
  n: number;
  sent_s: number | null;
  sim_s: number | null;
  rows: number | null;
  timing: { rtt_ms?: number; serve_infer_ms?: number; gateway_ms?: number; network_ms?: number; request_bytes?: number };
  error: { type: string; code: string | null; reason: string | null; retryable: boolean | null; message: string } | null;
};
type Stats = { mean: number; median: number; max: number } | null;
type DreamLog = {
  entries: DreamLogEntry[];
  summary: { requests: number; errors: number; error_codes: string[]; rows: number; rtt_ms: Stats; serve_infer_ms: Stats; network_ms: Stats };
};

const POLL_MS = 2000;
const SLOW_MS = 1000; // a reply slower than this is marked
const SHOWN = 200; // newest requests kept on the page

function ms(value?: number | null) {
  return value == null ? "–" : `${Math.round(value)}`;
}

function problem(entry: DreamLogEntry) {
  if (entry.error) return [entry.error.code, entry.error.reason].filter(Boolean).join(" ") || entry.error.type;
  if ((entry.timing.rtt_ms ?? 0) > SLOW_MS) return "slow reply";
  if (entry.rows != null && entry.rows < 48) return `short chunk (${entry.rows} rows)`;
  return "";
}

// One line of the dream client's own log (~/.dream/logs/dream.log), as GET /api/tasks/{id}/dream-client-log serves it.
type ClientLine = {
  time_s: number;
  level: string;
  logger: string;
  message: string;
  kind: "pacing" | "session" | "warning" | "other";
  mode?: "sync" | "async";
  median_ms?: number;
  last_ms?: number;
  event?: string;
  reason?: string | null;
};
type ClientLog = { path: string; exists: boolean; entries: ClientLine[]; summary: { sessions: number; replaced: number; warnings: number; sync_switches: number } };

function clientNote(line: ClientLine) {
  if (line.kind === "pacing") return `${line.mode === "sync" ? "Replies slow: robot waits for each one" : "Replies fast enough to keep moving"} (median ${line.median_ms} ms, last ${line.last_ms} ms)`;
  if (line.kind === "session") return `Session ${line.event}${line.reason ? ` (${line.reason})` : ""}`;
  return line.message;
}

/** This run's own dream client log: its sessions opened and released, pacing switches and warnings. */
function DreamClientLog({ taskId, attempt, running }: { taskId: string; attempt: number; running: boolean }) {
  const [log, setLog] = useState<ClientLog | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const response = await fetch(`/api/tasks/${taskId}/dream-client-log/${attempt}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as ClientLog;
        if (alive) {
          setLog(data);
          setError("");
        }
      } catch (problem) {
        if (alive) setError(problem instanceof Error ? problem.message : String(problem));
      }
    };
    void load();
    const timer = running ? window.setInterval(load, POLL_MS) : undefined;
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
    };
  }, [taskId, attempt, running]);

  if (error) return <div className="status error spaced">Dream client log: {error}</div>;
  if (!log) return null;
  const s = log.summary;
  return (
    <div className="dream-client-log">
      <h3>
        Dream client log <span className="muted">this run only</span>
      </h3>
      {log.entries.length === 0 && <p className="hint">{running ? "Nothing logged yet for this run." : "No dream client log for this run."}</p>}
      {log.entries.length > 0 && (
        <>
          <div className="dream-log-summary">
            <span><b>{s.sessions}</b> session{s.sessions === 1 ? "" : "s"} opened</span>
            <span className={s.sync_switches ? "warn" : ""} title="Times the client saw slow replies (median over 700 ms)"><b>{s.sync_switches}</b> slow-reply switch{s.sync_switches === 1 ? "" : "es"}</span>
            <span className={s.replaced ? "bad" : ""} title="Dream-1 restarted a session mid-run"><b>{s.replaced}</b> session restart{s.replaced === 1 ? "" : "s"}</span>
            <span className={s.warnings ? "bad" : ""}><b>{s.warnings}</b> warning{s.warnings === 1 ? "" : "s"}</span>
          </div>
          <button className="dream-log-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide client log" : `Show client log (${log.entries.length} lines)`}
          </button>
          {open && (
            <div className="dream-log-table">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>What</th>
                  </tr>
                </thead>
                <tbody>
                  {[...log.entries].reverse().map((line, index) => (
                    <tr key={`${line.time_s}-${index}`} className={line.kind === "warning" || line.event === "replaced" ? "bad" : line.mode === "sync" ? "warn" : ""}>
                      <td>{new Date(line.time_s * 1000).toLocaleTimeString()}</td>
                      <td title={line.message}>{clientNote(line)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** A run's Dream-1 requests: what each cost in time, how many rows came back, and any refusal or error. */
export function DreamLogPanel({ taskId, attempt, runLabel, running }: { taskId: string; attempt: number; runLabel: string; running: boolean }) {
  const [log, setLog] = useState<DreamLog | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    let last = 0;
    let entries: DreamLogEntry[] = [];
    const load = async () => {
      try {
        const response = await fetch(`/api/tasks/${taskId}/dream-log/${attempt}?after=${last}`);
        if (!response.ok) throw new Error(response.status === 404 ? "No log for this run." : `HTTP ${response.status}`);
        const data = (await response.json()) as DreamLog;
        if (!alive) return;
        entries = [...entries, ...data.entries].slice(-SHOWN);
        last = entries.length ? entries[entries.length - 1].n : last;
        setLog({ entries, summary: data.summary });
        setError("");
      } catch (problem) {
        if (alive) setError(problem instanceof Error ? problem.message : String(problem));
      }
    };
    void load();
    const timer = running ? window.setInterval(load, POLL_MS) : undefined;
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
    };
  }, [taskId, attempt, running]);

  const s = log?.summary;
  const flagged = log?.entries.filter((entry) => problem(entry)).length ?? 0;
  return (
    <div className="panel dream-log">
      <h2>
        Dream-1 log <span className="muted">{runLabel}</span>
      </h2>
      {error && <div className="status error">{error}</div>}
      {s && s.requests === 0 && <p className="hint">{running ? "Waiting for the first Dream-1 request…" : "This run made no Dream-1 requests."}</p>}
      {s && s.requests > 0 && (
        <>
          <div className="dream-log-summary">
            <span><b>{s.requests}</b> requests</span>
            <span><b>{s.rows}</b> rows back</span>
            <span title="The model's own compute time, as Dream-1 reports it">model <b>{ms(s.serve_infer_ms?.mean)}</b> ms avg</span>
            <span title="Time spent on the network">network <b>{ms(s.network_ms?.mean)}</b> ms avg</span>
            <span title="The whole round trip; the robot holds still while it waits">total <b>{ms(s.rtt_ms?.mean)}</b> ms avg, <b>{ms(s.rtt_ms?.max)}</b> max</span>
            <span className={s.errors ? "bad" : ""}><b>{s.errors}</b> error{s.errors === 1 ? "" : "s"}{s.error_codes.length ? ` (${s.error_codes.join(", ")})` : ""}</span>
          </div>
          <button className="dream-log-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide requests" : `Show requests${flagged ? ` · ${flagged} flagged` : ""}`}
          </button>
          {open && (
            <div className="dream-log-table">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Sim time (s)</th>
                    <th>Total (ms)</th>
                    <th>Model (ms)</th>
                    <th>Network (ms)</th>
                    <th>Rows</th>
                    <th>Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {[...log.entries].reverse().map((entry) => {
                    const note = problem(entry);
                    return (
                      <tr key={entry.n} className={note ? (entry.error ? "bad" : "warn") : ""}>
                        <td>{entry.n}</td>
                        <td>{entry.sim_s == null ? "–" : entry.sim_s.toFixed(1)}</td>
                        <td>{ms(entry.timing.rtt_ms)}</td>
                        <td>{ms(entry.timing.serve_infer_ms)}</td>
                        <td>{ms(entry.timing.network_ms)}</td>
                        <td>{entry.rows ?? "–"}</td>
                        <td title={entry.error?.message}>{note}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <DreamClientLog taskId={taskId} attempt={attempt} running={running} />
    </div>
  );
}

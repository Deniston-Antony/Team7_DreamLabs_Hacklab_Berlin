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
    </div>
  );
}

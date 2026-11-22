export function clock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

export function runTone(state: string, passed?: boolean | null) {
  if (state === "starting" || state === "running") return "running";
  if (state === "succeeded" || passed === true) return "succeeded";
  if (state === "failed" || state === "error" || passed === false) return "failed";
  return "stopped";
}

export function RunProgress({ seconds, limit, tone }: { seconds: number; limit: number; tone: string }) {
  const share = limit > 0 ? Math.min(100, Math.max(0, (seconds / limit) * 100)) : 0;
  const percent = Math.floor(share);
  return (
    <span className={`run-progress ${tone}`}>
      <span className="run-progress-text">
        <span>{clock(seconds)} / {clock(limit)}</span>
        <span>{percent}%</span>
      </span>
      <span className="run-progress-track" role="progressbar" aria-label="Run time used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <span className="run-progress-fill" style={{ width: `${share}%` }} />
      </span>
    </span>
  );
}

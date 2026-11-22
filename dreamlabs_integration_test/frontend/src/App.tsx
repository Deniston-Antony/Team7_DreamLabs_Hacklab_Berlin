import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { ReportsCenter } from "./EvaluationPanels";
import type { EvaluationRef } from "./EvaluationPanels";

type SceneObject = {
  object_id: string;
  movable: boolean;
  position: [number, number, number];
  orientation_wxyz: number[];
};
type Option = { id: string; name: string; executable?: boolean };
type Status = { text: string; error: boolean };
type Task = {
  task_id: string;
  instruction: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | "TIMED_OUT" | "CANCELLED";
  max_seconds: number;
  elapsed_s: number;
  error: string | null;
  outcome: "SUCCESS" | "FAILURE" | "NOT_EVALUATED" | null;
  result_message: string | null;
  attempts: number;
  videos: string[];
  attempt_results?: AttemptResult[];
  attempts_succeeded?: number;
  attempts_note?: string;
  task_results?: TaskResult[];
  tasks_succeeded?: number;
  attempts_error?: string;
  waiting_for_attempts?: boolean;
  evaluation?: EvaluationRef | null;
  evaluation_error?: string | null;
};
type TaskResult = {
  task: number;
  instruction: string;
  attempts: number;
  succeeded: number;
  passed: boolean;
  results: AttemptResult[];
};
type AttemptResult = {
  attempt: number;
  task?: number;
  where: "live" | "hidden";
  success: boolean;
  sim_seconds: number | null;
  closest_cm?: number | null;
  error?: string | null;
  stopped?: boolean;
  env?: string | null;
};
type WorkerState = {
  attempt: number;
  task: number;
  where: "live" | "hidden";
  label: string;
  env?: string;
  state: "ready" | "starting" | "running" | "finished" | "succeeded" | "failed" | "error" | "ended" | "stopped";
};
type Live = {
  layout_saved?: boolean;
  attempt?: number;
  workers?: WorkerState[];
  phase: "idle" | "running" | "waiting_for_dream";
  scene_id: string;
  selected: string | null;
  task: Task | null;
  objects: SceneObject[];
  view_objects?: SceneObject[];
  view_selected?: string | null;
  view_editable?: boolean;
  robot: Record<string, number[]>;
  contacts: number;
};
type Sessions = {
  sessions: { session_id: string; name: string; busy: boolean }[];
  max_parallel: number;
  parallel_supported: boolean;
};
type Point = { camera: string; u: number; v: number };

const CAMERAS = ["top", "left", "right", "free"];
const CAMERA_LABEL: Record<string, string> = { top: "Overhead", left: "Left wrist", right: "Right wrist", free: "3D view" };
const RUN_STATE_LABEL: Record<WorkerState["state"], string> = {
  ready: "Ready",
  starting: "Starting",
  running: "Running",
  finished: "Finished",
  succeeded: "Succeeded",
  failed: "Failed",
  error: "Error",
  ended: "Stopped",
  stopped: "Stopped",
};
const MAX_TASK_SECONDS = 200;
const MAX_ATTEMPTS = 8;
const CREDITS_PER_ATTEMPT_120S = 0.06;
const FRAME_WIDTH = 640;
const FRAME_HEIGHT = 360;

const PHASE_LABEL: Record<Live["phase"], string> = {
  idle: "Idle",
  running: "Executing",
  waiting_for_dream: "Waiting for Dream-1",
};
const TASK_LABEL: Record<Task["status"], string> = {
  RUNNING: "Running",
  COMPLETED: "Succeeded",
  FAILED: "Error",
  TIMED_OUT: "Time limit reached",
  CANCELLED: "Stopped",
};

async function parse<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    throw new Error(data.reason ?? detail ?? `Request failed (${response.status})`);
  }
  return data;
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parse<T>(response);
}

function clock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

// "apple>bowl" or "stl_coffee_cup>plate" -> "Apple → Bowl", "Coffee cup → Plate"
function goalName(label: string) {
  const nice = (part: string) => {
    const words = part.replace(/^stl_/, "").replace(/_/g, " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
  };
  return label.includes(">") ? label.split(">").map(nice).join(" → ") : label;
}

function runName(attempt: number) {
  return attempt === 1 ? "Live run" : `Parallel run ${attempt}`;
}

function AttemptsPanel({ task }: { task: Task }) {
  const videos = useRef<HTMLDivElement | null>(null);
  if (task.waiting_for_attempts) {
    return <div className="panel">Live run finished. Waiting for {task.attempts - 1} parallel run{task.attempts > 2 ? "s" : ""} to complete…</div>;
  }
  if (task.status === "RUNNING") return null;
  const results = task.attempt_results ?? [];
  const clips = task.videos.filter((name) => name !== "grid");
  if (!results.length && !clips.length && !task.attempts_note && !task.attempts_error) return null;
  const url = (name: string) => `/api/tasks/${task.task_id}/video/${name}`;
  const playTogether = () => {
    videos.current?.querySelectorAll("video").forEach((video) => {
      video.currentTime = 0;
      void video.play();
    });
  };
  const summary = (r: AttemptResult) =>
    r.success
      ? `Succeeded${r.sim_seconds != null ? ` in ${r.sim_seconds} s` : ""}`
      : r.stopped
        ? "Stopped"
        : r.error
          ? `Failed: ${r.error}`
          : r.closest_cm != null
            ? `Not reached, closest ${r.closest_cm} cm`
            : "Not reached";
  const suite = task.task_results && task.task_results.length > 1 ? task.task_results : null;
  const goalOf = (r: AttemptResult) => (suite ? suite.find((t) => t.task === r.task)?.instruction : undefined);
  return (
    <div className="panel" aria-label="Run results">
      <div className="stream-bar">
        <h2>
          Run results
          {suite ? (
            <span className="muted"> {task.tasks_succeeded} of {suite.length} tasks succeeded</span>
          ) : (
            results.length > 0 && <span className="muted"> {task.attempts_succeeded} of {task.attempts} runs succeeded</span>
          )}
        </h2>
        {clips.length > 1 && <button onClick={playTogether}>Play all together</button>}
      </div>
      {suite && (
        <ul className="task-results">
          {suite.map((t) => (
            <li key={t.task}>
              {t.passed ? "✓" : "✗"} {t.instruction}: {t.succeeded} of {t.attempts} run{t.attempts > 1 ? "s" : ""} succeeded
            </li>
          ))}
        </ul>
      )}
      {task.attempts_note && <p className="hint">{task.attempts_note}</p>}
      {task.attempts_error && <p className="hint">Recording or parallel runs failed: {task.attempts_error}</p>}
      <div className="attempt-videos" ref={videos}>
        {clips.map((name) => {
          const number = Number(name.replace("attempt-", ""));
          const result = results.find((r) => r.attempt === number);
          const state = result ? (result.success ? "passed" : result.stopped ? "stopped" : "failed") : "";
          return (
            <figure key={name} className={state}>
              <video controls loop muted playsInline preload="metadata" src={url(name)} />
              <figcaption>
                <strong>{runName(number)}</strong>
                {result && goalOf(result) ? ` · ${goalOf(result)}` : ""}
                {result?.env ? ` · ${result.env}` : ""}
                {result ? `: ${summary(result)}` : ""}
              </figcaption>
            </figure>
          );
        })}
      </div>
      {results.filter((r) => !clips.includes(`attempt-${r.attempt}`)).map((r) => (
        <p className="hint" key={r.attempt}>{runName(r.attempt)} has no video. {summary(r)}</p>
      ))}
      {task.videos.includes("grid") && (
        <p className="hint"><a href={url("grid")} target="_blank" rel="noreferrer">Open all runs side by side</a></p>
      )}
    </div>
  );
}

export default function App() {
  const [scenes, setScenes] = useState<Option[]>([]);
  const [robots, setRobots] = useState<Option[]>([]);
  const [sessions, setSessions] = useState<Sessions | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [connected, setConnected] = useState(false);
  const [frameUrl, setFrameUrl] = useState("");
  const [fps, setFps] = useState(0);
  const [camera, setCamera] = useState("top");
  const [viewAttempt, setViewAttempt] = useState(1);
  const [dragging, setDragging] = useState(false);
  const [pose, setPose] = useState({ x: 0.05, y: 0, z: 0.8, yaw: 45 });
  const [uploadStatus, setUploadStatus] = useState<Status | null>(null);
  const [orbiting, setOrbiting] = useState(false);
  const [stlFile, setStlFile] = useState<File | null>(null);
  const [stlSize, setStlSize] = useState(8);
  const [uploadKey, setUploadKey] = useState(0);
  const [instruction, setInstruction] = useState("put apple into the bowl");
  const [maxSeconds, setMaxSeconds] = useState(MAX_TASK_SECONDS);
  const [attempts, setAttempts] = useState(1);
  const [extras, setExtras] = useState<{ text: string; attempts: number }[]>([]);
  const [lastRequest, setLastRequest] = useState<Record<string, unknown> | null>(null);
  const [envMode, setEnvMode] = useState<"same" | "shuffle" | "shuffle_style">("same");
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 100000));
  const [status, setStatus] = useState<Status>({ text: "Connecting to the local simulator…", error: false });
  const [busy, setBusy] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const cameraRef = useRef(camera);
  const attemptRef = useRef(1);
  const drag = useRef<{ active: boolean; sending: boolean; next: Point | null; target: string }>({ active: false, sending: false, next: null, target: "" });
  const orbit = useRef({ active: false, sending: false, x: 0, y: 0, dx: 0, dy: 0 });
  const zoom = useRef({ sending: false, delta: 0 });
  const viewRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let url = "";
    let frames = 0;
    const meter = window.setInterval(() => {
      setFps(frames);
      frames = 0;
    }, 1000);

    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws/live`);
      ws.binaryType = "blob";
      socket.current = ws;
      ws.onopen = () => {
        setConnected(true);
        ws.send(JSON.stringify({ camera: cameraRef.current, attempt: attemptRef.current }));
      };
      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          setLive(JSON.parse(event.data));
          return;
        }
        frames += 1;
        const next = URL.createObjectURL(event.data);
        if (url) URL.revokeObjectURL(url);
        url = next;
        setFrameUrl(next);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(meter);
      socket.current?.close();
      if (url) URL.revokeObjectURL(url);
    };
  }, []);

  const run = useCallback(async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setStatus({ text: label, error: false });
    } catch (error) {
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    run("Connected to the local simulator", async () => {
      const [sceneList, robotList, sessionList] = await Promise.all([
        api<{ scene_id: string; name: string }[]>("/api/scenes"),
        api<{ robot_id: string; name: string; executable: boolean }[]>("/api/robots"),
        api<Sessions>("/api/sessions"),
      ]);
      setScenes(sceneList.map((scene) => ({ id: scene.scene_id, name: scene.name })));
      setRobots(robotList.map((robot) => ({ id: robot.robot_id, name: robot.name, executable: robot.executable })));
      setSessions(sessionList);
    });
  }, [run]);

  const chooseAttempt = (attempt: number) => {
    attemptRef.current = attempt;
    setViewAttempt(attempt);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ attempt }));
  };

  const workers = live?.workers ?? [];
  useEffect(() => {
    // the dropdown's list changed (a new task, or none): go back to the live simulation if the choice is gone
    if (viewAttempt > 1 && !workers.some((worker) => worker.attempt === viewAttempt)) chooseAttempt(1);
  }, [workers.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const chooseCamera = (name: string) => {
    cameraRef.current = name;
    setCamera(name);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ camera: name }));
  };

  useEffect(() => {
    const element = viewRef.current;
    if (!element) return;
    const sendZoom = async () => {
      const state = zoom.current;
      if (state.sending) return;
      state.sending = true;
      try {
        while (state.delta !== 0) {
          const delta = Math.max(-2000, Math.min(2000, state.delta));
          state.delta = 0;
          await api("/api/view/zoom", { delta });
        }
      } catch (error) {
        setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
      } finally {
        state.sending = false;
      }
    };
    const onWheel = (event: WheelEvent) => {
      if (cameraRef.current !== "free" || attemptRef.current !== 1) return;
      event.preventDefault();
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : 1;
      zoom.current.delta += event.deltaY * unit * (event.ctrlKey ? 4 : 1);
      void sendZoom();
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  // Before a run, a parallel run's tab can be edited like the live scene: the table and controls act on that run.
  const editingRun = viewAttempt > 1 && (live?.view_editable ?? false);
  const target = editingRun ? `/api/environments/${viewAttempt}` : "";
  const objects = (editingRun ? live?.view_objects : live?.objects) ?? [];
  const selected = (editingRun ? live?.view_selected : live?.selected) ?? "";
  const selectedObject = objects.find((object) => object.object_id === selected);
  const selectedMovable = selectedObject?.movable ?? false;
  const selectedPosition = selectedObject?.position.join(",");
  const task = live?.task ?? null;
  const running = task?.status === "RUNNING";
  const totalAttempts = attempts + extras.reduce((sum, extra) => sum + extra.attempts, 0);
  const suiteReady = extras.every((extra) => extra.text.trim()) && totalAttempts <= MAX_ATTEMPTS;
  const variation = totalAttempts > 1 ? { environments: envMode, seed } : {};
  const request: Record<string, unknown> = extras.length
    ? { tasks: [{ instruction, attempts }, ...extras.map((e) => ({ instruction: e.text, attempts: e.attempts }))], max_seconds: maxSeconds, ...variation }
    : { instruction, max_seconds: maxSeconds, attempts, ...variation };
  const layoutSaved = live?.layout_saved ?? false;
  useEffect(() => {
    // Show the parallel runs' environments (shuffled positions, other styles) in the run tabs before the run.
    if (running || !connected) return;
    const count = totalAttempts - 1;
    const timer = window.setTimeout(() => {
      api("/api/environments", { count, mode: envMode, seed }).catch((error) =>
        setStatus({ text: error instanceof Error ? error.message : String(error), error: true }),
      );
    }, 400);
    return () => window.clearTimeout(timer);
  }, [envMode, seed, totalAttempts, connected]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetScene = () => {
    if (running && !window.confirm("Stop every run and reset the scene?")) return;
    void run(running ? "Stopped every run and reset the scene" : layoutSaved ? "Saved layout restored" : "Scene reset", () => api("/api/scene/reset", {}));
  };
  const restart = () => {
    if (!lastRequest) return;
    if (!window.confirm(running ? "Stop every run, reset the scene and run the same tasks again? This uses credits." : "Reset the scene and run the same tasks again? This uses credits.")) return;
    void run("Restarted from a reset scene", async () => {
      await api("/api/scene/reset", {});
      await api("/api/tasks/execute", lastRequest);
    });
  };
  const locked = busy || running || dragging;
  const position = { x: pose.x, y: pose.y, z: pose.z };
  const where = editingRun ? ` in ${runName(viewAttempt).toLowerCase()}` : "";
  const canDrag = (viewAttempt === 1 ? camera === "top" || camera === "free" : editingRun && camera === "top") && !running && !busy;

  useEffect(() => {
    if (!selectedPosition) return;
    const [x, y, z] = selectedPosition.split(",").map(Number);
    setPose((value) => ({ ...value, x, y, z }));
  }, [selected, selectedPosition]);

  const selectObject = (object: SceneObject) => {
    if (running || dragging) return;
    api(editingRun ? `${target}/select` : "/api/objects/select", { object_id: object.object_id }).catch((error) =>
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true }),
    );
  };

  const changeType = (object: SceneObject, movable: boolean) =>
    run(`${object.object_id} is now ${movable ? "movable" : "fixed"}`, () =>
      api("/api/objects/type", { object_id: object.object_id, movable }),
    );

  const framePoint = (event: PointerEvent<HTMLImageElement>): Point => {
    const box = event.currentTarget.getBoundingClientRect();
    return {
      camera,
      u: Math.min(FRAME_WIDTH, Math.max(0, ((event.clientX - box.left) / box.width) * FRAME_WIDTH)),
      v: Math.min(FRAME_HEIGHT, Math.max(0, ((event.clientY - box.top) / box.height) * FRAME_HEIGHT)),
    };
  };

  const sendDrag = async () => {
    const state = drag.current;
    if (state.sending) return;
    state.sending = true;
    try {
      while (state.next) {
        const point = state.next;
        state.next = null;
        await api(state.target ? `${state.target}/drag` : "/api/view/drag", point);
      }
    } catch (error) {
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      state.sending = false;
    }
  };

  const sendOrbit = async () => {
    const state = orbit.current;
    if (state.sending) return;
    state.sending = true;
    try {
      while (state.dx !== 0 || state.dy !== 0) {
        const delta = { dx: state.dx, dy: state.dy };
        state.dx = 0;
        state.dy = 0;
        await api("/api/view/orbit", delta);
      }
    } catch (error) {
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      state.sending = false;
    }
  };

  const startDrag = async (event: PointerEvent<HTMLImageElement>) => {
    if (event.ctrlKey && viewAttempt === 1) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      Object.assign(orbit.current, { active: true, x: event.clientX, y: event.clientY });
      setOrbiting(true);
      if (camera !== "free") chooseCamera("free");
      return;
    }
    if (!canDrag || event.button !== 0) return;
    const element = event.currentTarget;
    const pointerId = event.pointerId;
    const point = framePoint(event);
    event.preventDefault();
    try {
      drag.current.target = target; // the run being edited, fixed for this whole drag
      const picked = await api<{ object_id: string | null; movable: boolean }>(target ? `${target}/pick` : "/api/view/pick", point);
      if (!picked.object_id) {
        setStatus({ text: "Selection cleared.", error: false });
        return;
      }
      if (!picked.movable) {
        setStatus({ text: `Selected ${picked.object_id}. It is fixed, so it cannot be dragged.`, error: false });
        return;
      }
      element.setPointerCapture(pointerId);
      drag.current.active = true;
      setDragging(true);
      setStatus({ text: `Dragging ${picked.object_id}`, error: false });
    } catch (error) {
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
    }
  };

  const moveDrag = (event: PointerEvent<HTMLImageElement>) => {
    if (orbit.current.active) {
      const state = orbit.current;
      state.dx += event.clientX - state.x;
      state.dy += event.clientY - state.y;
      state.x = event.clientX;
      state.y = event.clientY;
      void sendOrbit();
      return;
    }
    if (!drag.current.active) return;
    drag.current.next = framePoint(event);
    void sendDrag();
  };

  const endDrag = async () => {
    if (orbit.current.active) {
      orbit.current.active = false;
      setOrbiting(false);
      return;
    }
    const state = drag.current;
    if (!state.active) return;
    state.active = false;
    while (state.sending) await new Promise((resolve) => window.setTimeout(resolve, 15));
    await sendDrag();
    try {
      const result = await api<{ object_id: string; restored: boolean }>(state.target ? `${state.target}/drop` : "/api/view/drop", {});
      setStatus(
        result.restored
          ? { text: `${result.object_id} would collide there, so it went back.`, error: true }
          : { text: `Placed ${result.object_id}`, error: false },
      );
    } catch (error) {
      setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setDragging(false);
    }
  };

  const uploadStl = () => {
    if (!stlFile) return;
    const file = stlFile;
    setUploadStatus({ text: `Adding ${file.name}...`, error: false });
    run(`Added ${file.name}`, async () => {
      const query = new URLSearchParams({ filename: file.name, size_cm: String(stlSize) });
      const response = await fetch(`/api/objects/upload?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      }).catch(() => {
        throw new Error("Could not reach the backend.");
      });
      let result: { object_id: string; original_triangles: number; triangles: number };
      try {
        result = await parse(response);
      } catch (error) {
        setUploadStatus({ text: error instanceof Error ? error.message : String(error), error: true });
        throw error;
      }
      const simplified = result.triangles < result.original_triangles
        ? ` Simplified from ${result.original_triangles.toLocaleString()} to ${result.triangles.toLocaleString()} triangles.`
        : "";
      setUploadStatus({ text: `Added ${result.object_id}.${simplified}`, error: false });
      setStlFile(null);
      setUploadKey((value) => value + 1);
    });
  };

  const field = (key: "x" | "y" | "z" | "yaw", step: string) => (
    <label>
      {key === "yaw" ? "rotate by (deg)" : `${key} (m)`}
      <input
        aria-label={key}
        type="number"
        step={step}
        value={pose[key]}
        disabled={running}
        onChange={(event) => setPose({ ...pose, [key]: Number(event.target.value) })}
      />
    </label>
  );

  return (
    <main>
      <h1>Dream-1 Simulation Console</h1>
      <p className="note">Plan, run and evaluate Dream-1 tasks in the local YAM simulator.</p>
      <div className="layout">
        <section>
          <div className="panel viewer">
            <nav className="run-tabs" aria-label="Runs">
              <div className="run-tabs-title">Runs</div>
              <label className="run-count">Runs for this task
                <input aria-label="Runs for this task" type="number" min={1} max={MAX_ATTEMPTS - (totalAttempts - attempts)} value={attempts} disabled={running} onChange={(event) => setAttempts(Math.min(MAX_ATTEMPTS - (totalAttempts - attempts), Math.max(1, Number(event.target.value) || 1)))} />
              </label>
              <label className="run-count" title="Applies to every run of every task">Time limit per run (s)
                <input aria-label="Time limit" type="number" min={5} max={MAX_TASK_SECONDS} value={maxSeconds} disabled={running} onChange={(event) => setMaxSeconds(Math.min(MAX_TASK_SECONDS, Math.max(5, Number(event.target.value) || 5)))} />
              </label>
              {totalAttempts > 1 && (
                <label className="run-count">Start scenes
                  <select aria-label="Parallel run environments" value={envMode} disabled={running} onChange={(event) => setEnvMode(event.target.value as typeof envMode)}>
                    <option value="same">Copies of the live scene</option>
                    <option value="shuffle">Shuffled positions</option>
                    <option value="shuffle_style">Shuffled + scene styles</option>
                  </select>
                </label>
              )}
              {(workers.length > 1 ? workers : [{ attempt: 1, task: 1, where: "live", label: "", state: running ? "running" : "finished" } as WorkerState]).map((worker) => (
                <button
                  key={worker.attempt}
                  className={`run-tab ${viewAttempt === worker.attempt ? "active" : ""}`}
                  aria-pressed={viewAttempt === worker.attempt}
                  onClick={() => chooseAttempt(worker.attempt)}
                >
                  <span className="run-tab-name">{runName(worker.attempt)}</span>
                  {worker.label && <span className="run-tab-goal">{goalName(worker.label)}</span>}
                  {worker.env && <span className="run-tab-env">{worker.env}</span>}
                  <span className={`run-tab-state ${worker.state}`}>
                    {workers.length > 1 ? RUN_STATE_LABEL[worker.state] : connected ? PHASE_LABEL[live?.phase ?? "idle"] : "Offline"}
                  </span>
                </button>
              ))}
            </nav>
            <div className="viewer-main">
            <div className="stream-bar">
              <div className="cameras">
                {CAMERAS.map((name) => (
                  <button key={name} className={camera === name ? "active" : ""} onClick={() => chooseCamera(name)}>
                    {CAMERA_LABEL[name]}
                  </button>
                ))}
              </div>
              <div className="live-status" role="status">
                <span className={`dot ${connected ? "on" : ""}`} />
                {connected ? PHASE_LABEL[live?.phase ?? "idle"] : "Reconnecting…"}
                <span className="muted">{connected ? `${fps} FPS` : ""}</span>
              </div>
            </div>
            <div ref={viewRef}>
            {frameUrl ? (
              <img
                className={`main-view ${canDrag ? "draggable" : ""} ${dragging || orbiting ? "dragging" : ""}`}
                alt={`${runName(viewAttempt)}, ${CAMERA_LABEL[camera]} camera`}
                src={frameUrl}
                draggable={false}
                onContextMenu={(event) => event.preventDefault()}
                onPointerDown={viewAttempt === 1 || editingRun ? startDrag : undefined}
                onPointerMove={viewAttempt === 1 || editingRun ? moveDrag : undefined}
                onPointerUp={viewAttempt === 1 || editingRun ? endDrag : undefined}
                onPointerCancel={viewAttempt === 1 || editingRun ? endDrag : undefined}
              />
            ) : (
              <div className="main-view" />
            )}
            </div>
            <p className="hint">
              {editingRun
                ? `Editing ${runName(viewAttempt).toLowerCase()}'s starting scene: drag objects in the overhead view, or use the object table and Move, Rotate, Shuffle. The live scene does not change.`
                : viewAttempt > 1
                ? `Viewing ${runName(viewAttempt).toLowerCase()}: camera view only (overhead and wrist cameras). The object table shows the live run.`
                : camera === "free"
                ? "Click an object to select it, drag to move it. Ctrl + drag to orbit, scroll to zoom."
                : camera === "top"
                  ? "Click an object to select it, drag to move it. Ctrl + drag to open the 3D view."
                  : "Ctrl + drag to open the 3D view. Use the overhead or 3D view to select and move objects."}
            </p>
            </div>
          </div>
          {task && <AttemptsPanel key={task.task_id} task={task} />}
          <ReportsCenter latestRunId={task?.evaluation ? task.task_id : ""} error={task?.evaluation_error} locked={running} />
          <div className="panel">
            <h2>Scene objects {editingRun ? <span className="muted">{runName(viewAttempt)}</span> : <span className="muted">{live?.contacts ?? 0} contacts</span>}</h2>
            <table>
              <thead>
                <tr><th>Object</th><th>Position (m)</th><th>Orientation (wxyz)</th><th>Type</th></tr>
              </thead>
              <tbody>
                {objects.map((object) => (
                  <tr
                    key={object.object_id}
                    data-selectable="true"
                    className={selected === object.object_id ? "selected" : ""}
                    onClick={() => selectObject(object)}
                  >
                    <td>{object.object_id}</td>
                    <td>{object.position.map((value) => value.toFixed(3)).join(", ")}</td>
                    <td>{object.orientation_wxyz.map((value) => value.toFixed(2)).join(", ")}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <select
                        aria-label={`${object.object_id} type`}
                        className="type-select"
                        value={object.movable ? "movable" : "fixed"}
                        disabled={locked || editingRun}
                        title={editingRun ? "Movable or fixed is set in the live scene and applies to every run" : undefined}
                        onChange={(event) => changeType(object, event.target.value === "movable")}
                      >
                        <option value="movable">Movable</option>
                        <option value="fixed">Fixed</option>
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <aside>
          <div className="panel">
            <h2>Simulations</h2>
            {sessions?.sessions.map((session) => (
              <div className="session" key={session.session_id}>
                <strong>{session.name}</strong>
                <span>{running ? "Running" : "Active"}</span>
              </div>
            ))}
            <button disabled title="Multiple simulations are planned">Add simulation (coming soon)</button>
            <p className="hint">One simulation runs at a time for now.</p>
          </div>
          <div className="panel">
            <h2>Setup</h2>
            <div className="row">
              <label style={{ width: "100%" }}>Scene
                <select aria-label="Scene" value={live?.scene_id ?? ""} disabled={locked} onChange={(event) => { const scene_id = event.target.value; run("Scene changed", () => api("/api/scene/select", { scene_id })); }}>
                  {scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
                </select>
              </label>
              <label style={{ width: "100%" }}>Robot
                <select aria-label="Robot" value="yam" disabled={locked} onChange={() => undefined}>
                  {robots.map((robot) => (
                    <option key={robot.id} value={robot.id} disabled={!robot.executable}>
                      {robot.name}{robot.executable ? "" : " (setup required)"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="panel">
            <h2>Object controls{editingRun ? ` · ${runName(viewAttempt)}` : ""} {selected && `: ${selected}`}</h2>
            <div className="row">{field("x", "0.01")}{field("y", "0.01")}{field("z", "0.01")}{field("yaw", "5")}</div>
            <div className="row">
              <button disabled={locked || editingRun} title={editingRun ? "Objects are added to the live scene; every run gets them" : undefined} onClick={() => run("Added a block", () => api("/api/objects/add", { type: "block", position }))}>Add block</button>
              <button disabled={locked || !selectedMovable} onClick={() => run(`Moved ${selected}${where}`, () => api(editingRun ? `${target}/move` : "/api/objects/move", { object_id: selected, position }))}>Move</button>
              <button disabled={locked || !selectedMovable} onClick={() => run(`Rotated ${selected}${where}`, () => api(editingRun ? `${target}/rotate` : "/api/objects/rotate", { object_id: selected, yaw_deg: pose.yaw }))}>Rotate</button>
              <button disabled={locked || !selected || editingRun} title={editingRun ? "Objects are removed from the live scene; every run loses them" : undefined} onClick={() => run(`Removed ${selected}`, () => api("/api/objects/remove", { object_id: selected }))}>Remove</button>
              <button disabled={locked} onClick={() => run(`Objects shuffled${where}`, () => api(editingRun ? `${target}/shuffle` : "/api/objects/shuffle", {}))}>{editingRun ? "Shuffle this run" : "Shuffle"}</button>
              <button
                disabled={busy || dragging}
                title={running ? "Stops the live run and every parallel run, then resets the scene" : layoutSaved ? "Puts back your saved layout, with both arms home" : "Puts every object and both arms back where they started"}
                onClick={resetScene}
              >
                Reset scene
              </button>
              <button disabled={locked} title="Reset scene will put back the scene as it is now" onClick={() => run("Layout saved: Reset scene now returns to it", () => api("/api/scene/layout", {}))}>
                Save layout
              </button>
              {layoutSaved && (
                <span className="hint layout-hint">
                  Reset returns to your saved layout.{" "}
                  <button className="link" disabled={busy} onClick={() => run("Saved layout forgotten: Reset scene returns to the default scene", () => fetch("/api/scene/layout", { method: "DELETE" }).then((r) => parse(r)))}>Use default instead</button>
                </span>
              )}
            </div>
            <div className="upload">
              <label>Mesh file (STL)
                <input key={uploadKey} aria-label="STL file" type="file" accept=".stl" disabled={locked} onChange={(event) => setStlFile(event.target.files?.[0] ?? null)} />
              </label>
              <label>Size (cm)
                <input aria-label="STL size" type="number" min={1} max={25} value={stlSize} disabled={locked} onChange={(event) => setStlSize(Number(event.target.value))} />
              </label>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button disabled={locked || !stlFile} onClick={uploadStl}>Import mesh</button>
            </div>
            {uploadStatus && <div className={`status ${uploadStatus.error ? "error" : ""}`}>{uploadStatus.text}</div>}
          </div>
          <div className="panel">
            <h2>Tasks</h2>
            <textarea aria-label="Task instruction" placeholder="e.g. put apple into the bowl" rows={2} value={instruction} disabled={running} onChange={(event) => setInstruction(event.target.value)} />
            <p className="hint">Runs for this task and the time limit (shared by every run) are in the Runs list next to the camera view.</p>
            {extras.map((extra, index) => (
              <div className="extra-task" key={index}>
                <input aria-label={`Task ${index + 2}`} placeholder="e.g. put orange into the bowl" value={extra.text} disabled={running} onChange={(event) => setExtras(extras.map((e, i) => (i === index ? { ...e, text: event.target.value } : e)))} />
                <input aria-label={`Runs for task ${index + 2}`} title="Runs for this task" type="number" min={1} max={MAX_ATTEMPTS} value={extra.attempts} disabled={running} onChange={(event) => setExtras(extras.map((e, i) => (i === index ? { ...e, attempts: Math.min(MAX_ATTEMPTS, Math.max(1, Number(event.target.value) || 1)) } : e)))} />
                <button disabled={running} onClick={() => setExtras(extras.filter((_, i) => i !== index))} aria-label={`Remove task ${index + 2}`} title="Remove task">×</button>
              </div>
            ))}
            <div className="row" style={{ marginTop: 8 }}>
              <button disabled={running || totalAttempts >= MAX_ATTEMPTS} onClick={() => setExtras([...extras, { text: "", attempts: 1 }])}>Add task</button>
            </div>
            <div className="hint">
              {extras.length + 1} task{extras.length ? "s" : ""} · {totalAttempts} parallel run{totalAttempts > 1 ? "s" : ""} (max {MAX_ATTEMPTS}) · up to ${((totalAttempts * maxSeconds) / 120 * CREDITS_PER_ATTEMPT_120S).toFixed(2)} in credits
            </div>
            {totalAttempts > 1 && (
              <div className="env-controls">
                {envMode !== "same" && (
                  <div className="row">
                    <label>Layout seed
                      <input aria-label="Layout seed" type="number" min={0} value={seed} disabled={running} onChange={(event) => setSeed(Math.max(0, Math.floor(Number(event.target.value) || 0)))} />
                    </label>
                    <button disabled={running} onClick={() => setSeed(Math.floor(Math.random() * 100000))}>New layouts</button>
                  </div>
                )}
                <p className="hint">Each parallel run starts from its own scene, shown in its Runs tab. Open a tab to move objects or shuffle that run only; Reset scene puts every run back.</p>
              </div>
            )}
            <div className="timer" aria-label="Task timer">
              {clock(task?.elapsed_s ?? 0)} <span>/ {clock(task?.max_seconds ?? maxSeconds)}</span>
            </div>
            <div className="row">
              {running ? (
                <button className="danger" onClick={() => run("Stop requested", () => api(`/api/tasks/${task!.task_id}/cancel`, {}))}>Stop</button>
              ) : (
                <button className="primary" disabled={locked || !connected || !instruction.trim() || !suiteReady} onClick={() => { setLastRequest(request); void run("Task started", () => api("/api/tasks/execute", request)); }}>
                  {extras.length ? `Run ${extras.length + 1} tasks` : "Run task"}
                </button>
              )}
              <button disabled={!lastRequest || busy || !connected} title="Stop, reset the scene and run the last tasks again" onClick={restart}>
                Restart
              </button>
            </div>
            {task && (
              <div className={`status ${task.outcome === "SUCCESS" ? "success" : task.status === "FAILED" || task.outcome === "FAILURE" ? "error" : ""}`}>
                <strong>{TASK_LABEL[task.status]}</strong>: {task.result_message ?? task.instruction}
                {task.error && <div>{task.error}</div>}
              </div>
            )}
            <div className={`status spaced ${status.error ? "error" : ""}`}>{status.text}</div>
          </div>
        </aside>
      </div>
    </main>
  );
}

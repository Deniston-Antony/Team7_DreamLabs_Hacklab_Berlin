import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";

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
  where: "live" | "hidden";
  success: boolean;
  sim_seconds: number | null;
  closest_cm?: number | null;
  error?: string | null;
};
type WorkerState = {
  attempt: number;
  task: number;
  where: "live" | "hidden";
  label: string;
  state: "starting" | "running" | "finished" | "succeeded" | "failed" | "error" | "ended";
};
type Live = {
  attempt?: number;
  workers?: WorkerState[];
  phase: "idle" | "running" | "waiting_for_dream";
  scene_id: string;
  selected: string | null;
  task: Task | null;
  objects: SceneObject[];
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
const CAMERA_LABEL: Record<string, string> = { top: "top", left: "left", right: "right", free: "3D view" };
const MAX_TASK_SECONDS = 200;
const MAX_ATTEMPTS = 8;
const CREDITS_PER_ATTEMPT_120S = 0.06;
const FRAME_WIDTH = 640;
const FRAME_HEIGHT = 360;

const PHASE_LABEL: Record<Live["phase"], string> = {
  idle: "Idle",
  running: "Robot moving",
  waiting_for_dream: "Dream-1 thinking",
};
const TASK_LABEL: Record<Task["status"], string> = {
  RUNNING: "Running",
  COMPLETED: "Success",
  FAILED: "Failed",
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

function AttemptsPanel({ task }: { task: Task }) {
  if (task.waiting_for_attempts) {
    return <div className="attempts">Live attempt finished. Waiting for the other {task.attempts - 1} attempt{task.attempts > 2 ? "s" : ""}...</div>;
  }
  if (task.status === "RUNNING") return null;
  const results = task.attempt_results ?? [];
  const play = task.videos.includes("grid") ? "grid" : task.videos[0];
  if (!results.length && !play && !task.attempts_note && !task.attempts_error) return null;
  return (
    <div className="attempts" aria-label="Attempts">
      {task.task_results && task.task_results.length > 1 && (
        <>
          <strong>{task.tasks_succeeded} of {task.task_results.length} tasks succeeded</strong>
          <ul>
            {task.task_results.map((t) => (
              <li key={t.task}>
                {t.passed ? "✓" : "✗"} {t.instruction}: {t.succeeded} of {t.attempts} attempt{t.attempts > 1 ? "s" : ""}
                {t.results.some((r) => r.error) && <span> ({t.results.find((r) => r.error)?.error})</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {results.length > 0 && !(task.task_results && task.task_results.length > 1) && (
        <>
          <strong>{task.attempts_succeeded} of {task.attempts} attempts succeeded</strong>
          <ul>
            {results.map((r) => (
              <li key={r.attempt}>
                Attempt {r.attempt} ({r.where}): {r.success ? `✓ ${r.sim_seconds ?? "?"} s` : r.error ? `✗ ${r.error}` : r.closest_cm != null ? `✗ closest ${r.closest_cm} cm` : "✗"}
              </li>
            ))}
          </ul>
        </>
      )}
      {task.attempts_note && <div>{task.attempts_note}</div>}
      {task.attempts_error && <div>Videos or attempts failed: {task.attempts_error}</div>}
      {play && <video controls loop src={`/api/tasks/${task.task_id}/video/${play}`} style={{ width: "100%" }} />}
      {task.videos.length > 0 && (
        <div>
          {task.videos.map((name) => (
            <a key={name} href={`/api/tasks/${task.task_id}/video/${name}`} target="_blank" rel="noreferrer" style={{ marginRight: 8 }}>{name}</a>
          ))}
        </div>
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
  const [status, setStatus] = useState<Status>({ text: "Connecting to the local simulator", error: false });
  const [busy, setBusy] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const cameraRef = useRef(camera);
  const attemptRef = useRef(1);
  const drag = useRef<{ active: boolean; sending: boolean; next: Point | null }>({ active: false, sending: false, next: null });
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
    run("Connected to the real local MuJoCo simulator", async () => {
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

  const objects = live?.objects ?? [];
  const selected = live?.selected ?? "";
  const selectedObject = objects.find((object) => object.object_id === selected);
  const selectedMovable = selectedObject?.movable ?? false;
  const selectedPosition = selectedObject?.position.join(",");
  const task = live?.task ?? null;
  const running = task?.status === "RUNNING";
  const totalAttempts = attempts + extras.reduce((sum, extra) => sum + extra.attempts, 0);
  const suiteReady = extras.every((extra) => extra.text.trim()) && totalAttempts <= MAX_ATTEMPTS;
  const locked = busy || running || dragging;
  const position = { x: pose.x, y: pose.y, z: pose.z };
  const canDrag = (camera === "top" || camera === "free") && !running && !busy;

  useEffect(() => {
    if (!selectedPosition) return;
    const [x, y, z] = selectedPosition.split(",").map(Number);
    setPose((value) => ({ ...value, x, y, z }));
  }, [selected, selectedPosition]);

  const selectObject = (object: SceneObject) => {
    if (running || dragging) return;
    api("/api/objects/select", { object_id: object.object_id }).catch((error) =>
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
        await api("/api/view/drag", point);
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
    if (event.ctrlKey) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      Object.assign(orbit.current, { active: true, x: event.clientX, y: event.clientY });
      setOrbiting(true);
      if (camera !== "free") chooseCamera("free");
      return;
    }
    if (!canDrag || event.button !== 0) return;
    const target = event.currentTarget;
    const pointerId = event.pointerId;
    const point = framePoint(event);
    event.preventDefault();
    try {
      const picked = await api<{ object_id: string | null; movable: boolean }>("/api/view/pick", point);
      if (!picked.object_id) {
        setStatus({ text: "Selection cleared.", error: false });
        return;
      }
      if (!picked.movable) {
        setStatus({ text: `Selected ${picked.object_id}. It is fixed, so it cannot be dragged.`, error: false });
        return;
      }
      target.setPointerCapture(pointerId);
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
      const result = await api<{ object_id: string; restored: boolean }>("/api/view/drop", {});
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
      <h1>Dream Labs Integration Test</h1>
      <p className="note">Live view of the real local Dream YAM Sim. The hosted Playground is not embedded or controlled.</p>
      <div className="layout">
        <section>
          <div className="panel">
            <div className="stream-bar">
              {workers.length > 1 && (
                <label className="attempt-pick">View
                  <select aria-label="View attempt" value={viewAttempt} onChange={(event) => chooseAttempt(Number(event.target.value))}>
                    {workers.map((worker) => (
                      <option key={worker.attempt} value={worker.attempt}>
                        {worker.attempt} · {worker.where} · {worker.label} · {worker.state}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="cameras">
                {CAMERAS.map((name) => (
                  <button key={name} className={camera === name ? "active" : ""} onClick={() => chooseCamera(name)}>
                    {CAMERA_LABEL[name]}
                  </button>
                ))}
              </div>
              <div className="live-status" role="status">
                <span className={`dot ${connected ? "on" : ""}`} />
                {connected ? PHASE_LABEL[live?.phase ?? "idle"] : "Reconnecting"}
                <span className="muted">{connected ? `${fps} FPS` : ""}</span>
              </div>
            </div>
            <div ref={viewRef}>
            {frameUrl ? (
              <img
                className={`main-view ${canDrag ? "draggable" : ""} ${dragging || orbiting ? "dragging" : ""}`}
                alt={`Live ${camera} simulator camera`}
                src={frameUrl}
                draggable={false}
                onContextMenu={(event) => event.preventDefault()}
                onPointerDown={viewAttempt === 1 ? startDrag : undefined}
                onPointerMove={viewAttempt === 1 ? moveDrag : undefined}
                onPointerUp={viewAttempt === 1 ? endDrag : undefined}
                onPointerCancel={viewAttempt === 1 ? endDrag : undefined}
              />
            ) : (
              <div className="main-view" />
            )}
            </div>
            <p className="hint">
              {viewAttempt > 1
                ? `Showing attempt ${viewAttempt} (a hidden worker): its picture only, with the top, left and right cameras. The objects and robot below are the live simulation's.`
                : camera === "free"
                ? "Click to select, drag to move. Ctrl + drag rotates the view. Two-finger scroll zooms."
                : camera === "top"
                  ? "Click to select, drag to move. Ctrl + drag opens the rotating 3D view."
                  : "Ctrl + drag opens the 3D view. Use top or 3D view to select and move objects."}
            </p>
          </div>
          <div className="panel">
            <h2>Live scene state from simulator <span className="muted">{live?.contacts ?? 0} contacts</span></h2>
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
                        disabled={locked}
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
            <h2>Object controls {selected && `: ${selected}`}</h2>
            <div className="row">{field("x", "0.01")}{field("y", "0.01")}{field("z", "0.01")}{field("yaw", "5")}</div>
            <div className="row">
              <button disabled={locked} onClick={() => run("Added a block", () => api("/api/objects/add", { type: "block", position }))}>Add block</button>
              <button disabled={locked || !selectedMovable} onClick={() => run(`Moved ${selected}`, () => api("/api/objects/move", { object_id: selected, position }))}>Move</button>
              <button disabled={locked || !selectedMovable} onClick={() => run(`Rotated ${selected}`, () => api("/api/objects/rotate", { object_id: selected, yaw_deg: pose.yaw }))}>Rotate</button>
              <button disabled={locked || !selected} onClick={() => run(`Removed ${selected}`, () => api("/api/objects/remove", { object_id: selected }))}>Remove</button>
              <button disabled={locked} onClick={() => run("Objects shuffled", () => api("/api/objects/shuffle", {}))}>Shuffel</button>
              <button disabled={locked} onClick={() => run("Scene reset", () => api("/api/scene/reset", {}))}>Reset</button>
            </div>
            <div className="upload">
              <label>STL file
                <input key={uploadKey} aria-label="STL file" type="file" accept=".stl" disabled={locked} onChange={(event) => setStlFile(event.target.files?.[0] ?? null)} />
              </label>
              <label>Size (cm)
                <input aria-label="STL size" type="number" min={1} max={25} value={stlSize} disabled={locked} onChange={(event) => setStlSize(Number(event.target.value))} />
              </label>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button disabled={locked || !stlFile} onClick={uploadStl}>Add STL object</button>
            </div>
            {uploadStatus && <div className={`status ${uploadStatus.error ? "error" : ""}`}>{uploadStatus.text}</div>}
          </div>
          <div className="panel">
            <h2>Task</h2>
            <textarea aria-label="Task instruction" rows={2} value={instruction} disabled={running} onChange={(event) => setInstruction(event.target.value)} />
            <label style={{ marginTop: 8 }}>Time limit (seconds)
              <input aria-label="Time limit" type="number" min={5} max={MAX_TASK_SECONDS} value={maxSeconds} disabled={running} onChange={(event) => setMaxSeconds(Number(event.target.value))} />
            </label>
            <label style={{ marginTop: 8 }}>Attempts at once (each uses credits)
              <input aria-label="Attempts" type="number" min={1} max={MAX_ATTEMPTS} value={attempts} disabled={running} onChange={(event) => setAttempts(Math.min(MAX_ATTEMPTS, Math.max(1, Number(event.target.value) || 1)))} />
            </label>
            {extras.map((extra, index) => (
              <div className="extra-task" key={index}>
                <input aria-label={`Task ${index + 2}`} placeholder="put <object> into the bowl" value={extra.text} disabled={running} onChange={(event) => setExtras(extras.map((e, i) => (i === index ? { ...e, text: event.target.value } : e)))} />
                <input aria-label={`Attempts for task ${index + 2}`} type="number" min={1} max={MAX_ATTEMPTS} value={extra.attempts} disabled={running} onChange={(event) => setExtras(extras.map((e, i) => (i === index ? { ...e, attempts: Math.min(MAX_ATTEMPTS, Math.max(1, Number(event.target.value) || 1)) } : e)))} />
                <button disabled={running} onClick={() => setExtras(extras.filter((_, i) => i !== index))} aria-label={`Remove task ${index + 2}`}>x</button>
              </div>
            ))}
            <div className="row" style={{ marginTop: 8 }}>
              <button disabled={running || totalAttempts >= MAX_ATTEMPTS} onClick={() => setExtras([...extras, { text: "", attempts: 1 }])}>+ add task</button>
            </div>
            <div className="hint">
              {extras.length + 1} task{extras.length ? "s" : ""}, {totalAttempts} attempt{totalAttempts > 1 ? "s" : ""} at once (the most is {MAX_ATTEMPTS}), at most about ${((totalAttempts * maxSeconds) / 120 * CREDITS_PER_ATTEMPT_120S).toFixed(2)}.
            </div>
            <div className="timer" aria-label="Task timer">
              {clock(task?.elapsed_s ?? 0)} <span>/ {clock(task?.max_seconds ?? maxSeconds)}</span>
            </div>
            <div className="row">
              {running ? (
                <button className="danger" onClick={() => run("Stop requested", () => api(`/api/tasks/${task!.task_id}/cancel`, {}))}>Stop task</button>
              ) : (
                <button className="primary" disabled={locked || !connected || !instruction.trim() || !suiteReady} onClick={() => run("Task started", () => api("/api/tasks/execute", extras.length ? { tasks: [{ instruction, attempts }, ...extras.map((e) => ({ instruction: e.text, attempts: e.attempts }))], max_seconds: maxSeconds } : { instruction, max_seconds: maxSeconds, attempts }))}>
                  Execute {extras.length ? `${extras.length + 1} tasks` : "task"} (uses credits)
                </button>
              )}
            </div>
            {task && (
              <div className={`status ${task.outcome === "SUCCESS" ? "success" : task.status === "FAILED" || task.outcome === "FAILURE" ? "error" : ""}`}>
                <strong>{TASK_LABEL[task.status]}</strong>: {task.result_message ?? task.instruction}
                {task.error && <div>{task.error}</div>}
              </div>
            )}
            {task && <AttemptsPanel task={task} />}
            <div className={`status spaced ${status.error ? "error" : ""}`}>{status.text}</div>
          </div>
        </aside>
      </div>
    </main>
  );
}

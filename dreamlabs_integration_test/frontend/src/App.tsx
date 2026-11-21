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
};
type Live = {
  phase: "idle" | "running" | "waiting_for_dream";
  scene_id: string;
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

export default function App() {
  const [scenes, setScenes] = useState<Option[]>([]);
  const [robots, setRobots] = useState<Option[]>([]);
  const [sessions, setSessions] = useState<Sessions | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [connected, setConnected] = useState(false);
  const [frameUrl, setFrameUrl] = useState("");
  const [fps, setFps] = useState(0);
  const [selected, setSelected] = useState("");
  const [camera, setCamera] = useState("top");
  const [dragging, setDragging] = useState(false);
  const [pose, setPose] = useState({ x: 0.05, y: 0, z: 0.8, yaw: 45 });
  const [uploadStatus, setUploadStatus] = useState<Status | null>(null);
  const [orbiting, setOrbiting] = useState(false);
  const [stlFile, setStlFile] = useState<File | null>(null);
  const [stlSize, setStlSize] = useState(8);
  const [uploadKey, setUploadKey] = useState(0);
  const [instruction, setInstruction] = useState("put apple into the bowl");
  const [maxSeconds, setMaxSeconds] = useState(MAX_TASK_SECONDS);
  const [status, setStatus] = useState<Status>({ text: "Connecting to the local simulator", error: false });
  const [busy, setBusy] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const cameraRef = useRef(camera);
  const drag = useRef<{ active: boolean; sending: boolean; next: Point | null }>({ active: false, sending: false, next: null });
  const orbit = useRef({ active: false, sending: false, x: 0, y: 0, dx: 0, dy: 0 });

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
        ws.send(JSON.stringify({ camera: cameraRef.current }));
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

  const chooseCamera = (name: string) => {
    cameraRef.current = name;
    setCamera(name);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ camera: name }));
  };

  const objects = live?.objects ?? [];
  const task = live?.task ?? null;
  const running = task?.status === "RUNNING";
  const locked = busy || running || dragging;
  const position = { x: pose.x, y: pose.y, z: pose.z };
  const canDrag = camera === "top" && !running && !busy;

  const selectObject = (object: SceneObject) => {
    if (!object.movable || running) return;
    setSelected(object.object_id);
    setPose((value) => ({ ...value, x: object.position[0], y: object.position[1], z: object.position[2] }));
  };

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
      const picked = await api<{ object_id: string | null }>("/api/view/pick", point);
      if (!picked.object_id) {
        setStatus({ text: "Click directly on a movable object to drag it.", error: false });
        return;
      }
      target.setPointerCapture(pointerId);
      drag.current.active = true;
      setDragging(true);
      setSelected(picked.object_id);
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
      setSelected(result.object_id);
      setStlFile(null);
      setUploadKey((value) => value + 1);
    });
  };

  const field = (key: "x" | "y" | "z" | "yaw", step: string) => (
    <label>
      {key === "yaw" ? "yaw (deg)" : `${key} (m)`}
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
            {frameUrl ? (
              <img
                className={`main-view ${canDrag ? "draggable" : ""} ${dragging || orbiting ? "dragging" : ""}`}
                alt={`Live ${camera} simulator camera`}
                src={frameUrl}
                draggable={false}
                onContextMenu={(event) => event.preventDefault()}
                onPointerDown={startDrag}
                onPointerMove={moveDrag}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              />
            ) : (
              <div className="main-view" />
            )}
            <p className="hint">
              {camera === "top" ? "Drag an object to move it. Hold Ctrl and drag to rotate the 3D view." : "Hold Ctrl and drag to rotate the 3D view. Use the top camera to move objects."}
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
                    data-movable={object.movable}
                    className={selected === object.object_id ? "selected" : ""}
                    onClick={() => selectObject(object)}
                  >
                    <td>{object.object_id}</td>
                    <td>{object.position.map((value) => value.toFixed(3)).join(", ")}</td>
                    <td>{object.orientation_wxyz.map((value) => value.toFixed(2)).join(", ")}</td>
                    <td>{object.movable ? "movable" : "fixed"}</td>
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
              <button disabled={locked || !selected} onClick={() => run(`Moved ${selected}`, () => api("/api/objects/move", { object_id: selected, position }))}>Move</button>
              <button disabled={locked || !selected} onClick={() => run(`Rotated ${selected}`, () => api("/api/objects/rotate", { object_id: selected, yaw_deg: pose.yaw }))}>Rotate</button>
              <button disabled={locked || !selected} onClick={() => run(`Removed ${selected}`, async () => { await api("/api/objects/remove", { object_id: selected }); setSelected(""); })}>Remove</button>
              <button disabled={locked} onClick={() => run("Objects shuffled", () => api("/api/objects/shuffle", {}))}>Shuffel</button>
              <button disabled={locked} onClick={() => run("Scene reset", async () => { await api("/api/scene/reset", {}); setSelected(""); })}>Reset</button>
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
            <div className="timer" aria-label="Task timer">
              {clock(task?.elapsed_s ?? 0)} <span>/ {clock(task?.max_seconds ?? maxSeconds)}</span>
            </div>
            <div className="row">
              {running ? (
                <button className="danger" onClick={() => run("Stop requested", () => api(`/api/tasks/${task!.task_id}/cancel`, {}))}>Stop task</button>
              ) : (
                <button className="primary" disabled={locked || !connected || !instruction.trim()} onClick={() => run("Task started", () => api("/api/tasks/execute", { instruction, max_seconds: maxSeconds }))}>
                  Execute task (uses credits)
                </button>
              )}
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

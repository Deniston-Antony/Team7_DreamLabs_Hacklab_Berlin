import { useCallback, useEffect, useRef, useState } from "react";

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
  started_at: number;
  error: string | null;
};
type Live = {
  phase: "idle" | "running" | "waiting_for_dream";
  scene_id: string;
  task: Task | null;
  objects: SceneObject[];
  robot: Record<string, number[]>;
  contacts: number;
};

const CAMERAS = ["top", "left", "right"] as const;
const MAX_TASK_SECONDS = 200;

const PHASE_LABEL: Record<Live["phase"], string> = {
  idle: "Idle",
  running: "Robot moving",
  waiting_for_dream: "Dream-1 thinking",
};
const TASK_LABEL: Record<Task["status"], string> = {
  RUNNING: "Running",
  COMPLETED: "Completed",
  FAILED: "Failed",
  TIMED_OUT: "Time limit reached. Outcome not evaluated.",
  CANCELLED: "Stopped",
};

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    throw new Error(data.reason ?? detail ?? `Request failed (${response.status})`);
  }
  return data;
}

export default function App() {
  const [scenes, setScenes] = useState<Option[]>([]);
  const [robots, setRobots] = useState<Option[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [connected, setConnected] = useState(false);
  const [frameUrls, setFrameUrls] = useState<string[]>(["", "", ""]);
  const [fps, setFps] = useState(0);
  const [selected, setSelected] = useState("");
  const [pose, setPose] = useState({ x: 0.05, y: 0, z: 0.8, yaw: 45 });
  const [instruction, setInstruction] = useState("put apple into the bowl");
  const [maxSeconds, setMaxSeconds] = useState(MAX_TASK_SECONDS);
  const [status, setStatus] = useState<Status>({ text: "Connecting to the local simulator", error: false });
  const [busy, setBusy] = useState(false);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry = 0;
    const urls = ["", "", ""];
    let frames = 0;
    const meter = window.setInterval(() => {
      setFps(frames);
      frames = 0;
    }, 1000);

    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws/live`);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          setLive(JSON.parse(event.data));
          return;
        }
        const bytes = new Uint8Array(event.data);
        const index = bytes[0];
        if (index === 0) frames += 1;
        const next = URL.createObjectURL(new Blob([bytes.subarray(1)], { type: "image/jpeg" }));
        if (urls[index]) URL.revokeObjectURL(urls[index]);
        urls[index] = next;
        setFrameUrls([...urls]);
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
      urls.forEach((url) => url && URL.revokeObjectURL(url));
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
      const [sceneList, robotList] = await Promise.all([
        api<{ scene_id: string; name: string }[]>("/api/scenes"),
        api<{ robot_id: string; name: string; executable: boolean }[]>("/api/robots"),
      ]);
      setScenes(sceneList.map((scene) => ({ id: scene.scene_id, name: scene.name })));
      setRobots(robotList.map((robot) => ({ id: robot.robot_id, name: robot.name, executable: robot.executable })));
    });
  }, [run]);

  const objects = live?.objects ?? [];
  const task = live?.task ?? null;
  const running = task?.status === "RUNNING";
  const locked = busy || running;
  const position = { x: pose.x, y: pose.y, z: pose.z };
  const selectObject = (object: SceneObject) => {
    if (!object.movable || running) return;
    setSelected(object.object_id);
    setPose((value) => ({ ...value, x: object.position[0], y: object.position[1], z: object.position[2] }));
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
      <div className="panel">
        <div className="stream-bar">
          <h2>Live cameras</h2>
          <div className="live-status" role="status">
            <span className={`dot ${connected ? "on" : ""}`} />
            {connected ? PHASE_LABEL[live?.phase ?? "idle"] : "Reconnecting"}
            <span className="muted">{connected ? `${fps} FPS` : ""}</span>
          </div>
        </div>
        <div className="camera-grid">
          {CAMERAS.map((name, index) => (
            <figure key={name}>
              {frameUrls[index] ? (
                <img className="camera-view" alt={`Live ${name} simulator camera`} src={frameUrls[index]} />
              ) : (
                <div className="camera-view" />
              )}
              <figcaption>{name}</figcaption>
            </figure>
          ))}
        </div>
      </div>
      <div className="layout">
        <section>
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
          </div>
          <div className="panel">
            <h2>Task</h2>
            <textarea aria-label="Task instruction" rows={2} value={instruction} disabled={running} onChange={(event) => setInstruction(event.target.value)} />
            <label style={{ marginTop: 8 }}>Time limit (seconds)
              <input aria-label="Time limit" type="number" min={5} max={MAX_TASK_SECONDS} value={maxSeconds} disabled={running} onChange={(event) => setMaxSeconds(Number(event.target.value))} />
            </label>
            <div className="row" style={{ marginTop: 8 }}>
              {running ? (
                <button className="danger" onClick={() => run("Stop requested", () => api(`/api/tasks/${task!.task_id}/cancel`, {}))}>Stop task</button>
              ) : (
                <button className="primary" disabled={busy || !connected || !instruction.trim()} onClick={() => run("Task started", () => api("/api/tasks/execute", { instruction, max_seconds: maxSeconds }))}>
                  Execute task (uses credits)
                </button>
              )}
            </div>
            {task && (
              <div className={`status ${task.status === "FAILED" ? "error" : ""}`}>
                <strong>{TASK_LABEL[task.status]}</strong>: {task.instruction}
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

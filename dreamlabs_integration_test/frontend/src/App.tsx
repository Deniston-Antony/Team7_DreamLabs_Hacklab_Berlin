import { useCallback, useEffect, useState } from "react";

type SceneObject = {
  object_id: string;
  movable: boolean;
  position: [number, number, number];
  orientation_wxyz: number[];
};
type Option = { id: string; name: string };
type Status = { text: string; error: boolean };

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
  const [objects, setObjects] = useState<SceneObject[]>([]);
  const [selected, setSelected] = useState("");
  const [camera, setCamera] = useState("top");
  const [frame, setFrame] = useState(0);
  const [pose, setPose] = useState({ x: 0.05, y: 0, z: 0.8, yaw: 45 });
  const [instruction, setInstruction] = useState("put apple into the bowl");
  const [status, setStatus] = useState<Status>({ text: "Connecting to the local simulator", error: false });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const data = await api<{ placed: SceneObject[] }>("/api/objects");
    setObjects(data.placed);
    setFrame((value) => value + 1);
  }, []);

  const run = useCallback(
    async (label: string, action: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await action();
        await refresh();
        setStatus({ text: label, error: false });
      } catch (error) {
        setStatus({ text: error instanceof Error ? error.message : String(error), error: true });
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    run("Connected to the real local MuJoCo simulator", async () => {
      const [sceneList, robotList] = await Promise.all([
        api<{ scene_id: string; name: string }[]>("/api/scenes"),
        api<{ robot_id: string; name: string }[]>("/api/robots"),
      ]);
      setScenes(sceneList.map((scene) => ({ id: scene.scene_id, name: scene.name })));
      setRobots(robotList.map((robot) => ({ id: robot.robot_id, name: robot.name })));
    });
  }, [run]);

  const position = { x: pose.x, y: pose.y, z: pose.z };
  const selectObject = (object: SceneObject) => {
    if (!object.movable) return;
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
        onChange={(event) => setPose({ ...pose, [key]: Number(event.target.value) })}
      />
    </label>
  );

  return (
    <main>
      <h1>Dream Labs Integration Test</h1>
      <p className="note">Real local Dream YAM Sim state. The hosted Playground is not embedded or controlled.</p>
      <div className="layout">
        <section>
          <div className="panel">
            <div className="cameras">
              {["top", "left", "right"].map((name) => (
                <button key={name} className={camera === name ? "active" : ""} onClick={() => setCamera(name)}>
                  {name}
                </button>
              ))}
            </div>
            <img className="main-view" alt={`${camera} simulator camera`} src={`/api/frame/${camera}?v=${frame}`} />
          </div>
          <div className="panel">
            <h2>Scene state from simulator</h2>
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
                <select>{scenes.map((scene) => <option key={scene.id}>{scene.name}</option>)}</select>
              </label>
              <label style={{ width: "100%" }}>Robot
                <select>{robots.map((robot) => <option key={robot.id}>{robot.name}</option>)}</select>
              </label>
            </div>
          </div>
          <div className="panel">
            <h2>Object controls {selected && `: ${selected}`}</h2>
            <div className="row">{field("x", "0.01")}{field("y", "0.01")}{field("z", "0.01")}{field("yaw", "5")}</div>
            <div className="row">
              <button disabled={busy} onClick={() => run("Added a block", () => api("/api/objects/add", { type: "block", position }))}>Add block</button>
              <button disabled={busy || !selected} onClick={() => run(`Moved ${selected}`, () => api("/api/objects/move", { object_id: selected, position }))}>Move</button>
              <button disabled={busy || !selected} onClick={() => run(`Rotated ${selected}`, () => api("/api/objects/rotate", { object_id: selected, yaw_deg: pose.yaw }))}>Rotate</button>
              <button disabled={busy || !selected} onClick={() => run(`Removed ${selected}`, async () => { await api("/api/objects/remove", { object_id: selected }); setSelected(""); })}>Remove</button>
              <button disabled={busy} onClick={() => run("Scene reset", async () => { await api("/api/scene/reset", {}); setSelected(""); })}>Reset</button>
            </div>
          </div>
          <div className="panel">
            <h2>Task</h2>
            <textarea aria-label="Task instruction" rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} />
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" disabled={busy || !instruction.trim()} onClick={() => run("Task started", () => api("/api/tasks/execute", { instruction }))}>Execute task</button>
            </div>
            <div role="status" className={`status ${status.error ? "error" : ""}`}>{status.text}</div>
          </div>
        </aside>
      </div>
    </main>
  );
}

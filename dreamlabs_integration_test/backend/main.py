import asyncio
import math
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import dream_examples
import mujoco
import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
import yam_env  # noqa: E402

SCENE_ID = "yam_tabletop"
ROBOT_ID = "yam"
CAMERAS = ("top", "left", "right")
X_RANGE = (-0.35, 0.35)
Y_RANGE = (-0.20, 0.25)
Z_RANGE = (0.76, 1.00)
STREAM_PERIOD_S = 0.1
JPEG_QUALITY = 70
WAITING_AFTER_S = 0.3
ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


class LiveFeed:
    def __init__(self):
        self.lock = threading.Lock()
        self.images = {}
        self.objects = []
        self.robot = {}
        self.contacts = 0
        self.sequence = 0
        self.last_step = 0.0
        self.jpegs = {}

    def publish(self, images, objects, robot, contacts):
        with self.lock:
            self.images = images
            self.objects = objects
            self.robot = robot
            self.contacts = contacts
            self.sequence += 1

    def jpeg(self, camera):
        with self.lock:
            sequence = self.sequence
            image = self.images[camera]
            cached = self.jpegs.get(camera)
        if cached and cached[0] == sequence:
            return cached[1]
        ok, encoded = cv2.imencode(
            ".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            raise RuntimeError("could not encode the camera frame")
        data = encoded.tobytes()
        with self.lock:
            self.jpegs[camera] = (sequence, data)
        return data


class LiveEnv(yam_env.YamEnv):
    on_images = None

    def images(self):
        result = super().images()
        if self.on_images is not None:
            self.on_images(result)
        return result


class SimAdapter:
    def __init__(self, feed):
        self.feed = feed
        self.env = LiveEnv()
        self.added = {}
        self.removed = set()
        self.last_publish = 0.0
        self.env.on_images = self._on_images
        self.publish()

    def _on_images(self, images):
        now = time.monotonic()
        self.feed.last_step = now
        if now - self.last_publish < STREAM_PERIOD_S:
            return
        self.last_publish = now
        state = self.env.state()
        self.feed.publish(
            images,
            self.objects(),
            {side: np.round(values, 4).tolist() for side, values in state.items()},
            int(self.env.data.ncon),
        )

    def publish(self):
        self.last_publish = 0.0
        self.env.images()

    def _joint_by_body(self):
        m = self.env.model
        return {
            m.body(m.jnt_bodyid[i]).name: m.joint(i).name
            for i in range(m.njnt)
            if m.jnt_type[i] == mujoco.mjtJoint.mjJNT_FREE
        }

    def _qpos(self, body):
        joint = self._joint_by_body().get(body)
        if joint is None:
            raise HTTPException(404, f"no movable object named {body!r}")
        address = self.env.model.joint(joint).qposadr[0]
        return joint, address

    def _set_pose(self, body, position=None, quat=None):
        m, d = self.env.model, self.env.data
        joint, address = self._qpos(body)
        if position is not None:
            d.qpos[address : address + 3] = position
        if quat is not None:
            d.qpos[address + 3 : address + 7] = quat
        velocity = m.joint(joint).dofadr[0]
        d.qvel[velocity : velocity + 6] = 0
        mujoco.mj_forward(m, d)

    def _rebuild(self, keep_poses=True):
        saved = {}
        if keep_poses:
            for body in self._joint_by_body():
                address = self._qpos(body)[1]
                saved[body] = self.env.data.qpos[address : address + 7].copy()
        spec = mujoco.MjSpec.from_string(yam_env.build_xml())
        for name in self.removed:
            spec.delete(spec.body(name))
        for name, position in self.added.items():
            body = spec.worldbody.add_body(name=name, pos=list(position))
            body.add_freejoint(name=f"{name}_free")
            body.add_geom(
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=[0.015, 0.015, 0.015],
                mass=0.02,
                rgba=[0.1, 0.35, 0.9, 1],
            )
        model = spec.compile()
        self.env.close()
        self.env.model = model
        self.env.data = mujoco.MjData(model)
        self.env.reset()
        current = self._joint_by_body()
        for body, pose in saved.items():
            if body in current:
                self._set_pose(body, pose[:3], pose[3:])

    def objects(self):
        d = self.env.data
        movable = self._joint_by_body()
        names = list(movable) + [n for n in ("bowl", "plate") if n not in movable]
        return [
            {
                "object_id": name,
                "movable": name in movable,
                "position": d.body(name).xpos.round(4).tolist(),
                "orientation_wxyz": d.body(name).xquat.round(4).tolist(),
                "capture_source": "simulator",
            }
            for name in names
        ]

    def add(self, position):
        index = 1
        while f"block_{index}" in self.added:
            index += 1
        name = f"block_{index}"
        self.added[name] = position
        self._rebuild()
        self._set_pose(name, position, [1, 0, 0, 0])
        return name

    def remove(self, name):
        self._qpos(name)
        if name in self.added:
            del self.added[name]
        else:
            self.removed.add(name)
        self._rebuild()

    def move(self, name, position):
        self._set_pose(name, position)

    def rotate(self, name, yaw_deg):
        half = math.radians(yaw_deg) / 2
        self._set_pose(name, quat=[math.cos(half), 0, 0, math.sin(half)])

    def reset(self):
        self.added.clear()
        self.removed.clear()
        self._rebuild(keep_poses=False)


class Position(BaseModel):
    x: float = Field(ge=X_RANGE[0], le=X_RANGE[1])
    y: float = Field(ge=Y_RANGE[0], le=Y_RANGE[1])
    z: float = Field(ge=Z_RANGE[0], le=Z_RANGE[1])

    def array(self):
        return np.array([self.x, self.y, self.z])


class SelectScene(BaseModel):
    scene_id: str


class AddObject(BaseModel):
    type: str
    position: Position


class ObjectRef(BaseModel):
    object_id: str


class MoveObject(ObjectRef):
    position: Position


class RotateObject(ObjectRef):
    yaw_deg: float = Field(ge=-360, le=360)


class ExecuteTask(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    max_seconds: int = Field(default=60, ge=5, le=120)


# MuJoCo rendering contexts are thread-bound, so all simulator work stays on one thread.
pool = ThreadPoolExecutor(max_workers=1)
feed = LiveFeed()
sim = pool.submit(SimAdapter, feed).result()
tasks = {}
active_task = None
active_stop = None
app = FastAPI(title="Dream Labs integration test")


async def on_sim(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)


async def change_scene(fn, *args):
    if active_task is not None:
        raise HTTPException(409, "The scene cannot change while a task is running.")

    def apply():
        result = fn(*args)
        sim.publish()
        return result

    return await on_sim(apply)


def unsupported(capability, reason):
    return JSONResponse(
        status_code=501,
        content={"error": "unsupported_capability", "capability": capability, "reason": reason},
    )


def run_task(task, stop):
    global active_task, active_stop
    try:
        dc.main(
            [f"--task={task['instruction']}", "--headless", "--quiet", "--seconds", str(task["max_seconds"])],
            shared_env=sim.env,
            stop_event=stop,
        )
        task["status"] = "CANCELLED" if stop.is_set() else "TIMED_OUT"
    except (Exception, SystemExit) as error:
        task["status"] = "FAILED"
        task["error"] = f"{type(error).__name__}: {error}"
    finally:
        task["finished_at"] = time.time()
        sim.publish()
        active_task = None
        active_stop = None


def live_phase():
    if active_task is None:
        return "idle"
    if time.monotonic() - feed.last_step > WAITING_AFTER_S:
        return "waiting_for_dream"
    return "running"


@app.get("/api/scenes")
def scenes():
    return [{"scene_id": SCENE_ID, "name": "Dream YAM Sim tabletop", "source": "local MuJoCo example"}]


@app.get("/api/robots")
def robots():
    return [{"robot_id": ROBOT_ID, "name": "YAM (two arms)", "source": "local MuJoCo example"}]


@app.get("/api/objects")
def objects():
    return {"placed": feed.objects, "catalog": ["block"]}


@app.post("/api/scene/select")
def select_scene(body: SelectScene):
    if body.scene_id != SCENE_ID:
        return unsupported("scene_select", "Hosted Playground rooms have no documented external API.")
    return {"scene_id": SCENE_ID}


@app.post("/api/scene/reset")
async def reset_scene():
    await change_scene(sim.reset)
    return {"placed": feed.objects}


@app.post("/api/objects/add")
async def add_object(body: AddObject):
    if body.type != "block":
        return unsupported("add_object", f"{body.type!r} is not in the local simulator catalog.")
    return {"object_id": await change_scene(sim.add, body.position.array())}


@app.post("/api/objects/remove")
async def remove_object(body: ObjectRef):
    await change_scene(sim.remove, body.object_id)
    return {"removed": body.object_id}


@app.post("/api/objects/move")
async def move_object(body: MoveObject):
    await change_scene(sim.move, body.object_id, body.position.array())
    return {"object_id": body.object_id}


@app.post("/api/objects/rotate")
async def rotate_object(body: RotateObject):
    await change_scene(sim.rotate, body.object_id, body.yaw_deg)
    return {"object_id": body.object_id}


@app.get("/api/frame/{camera}")
async def frame(camera: str):
    if camera not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    jpeg = await asyncio.to_thread(feed.jpeg, camera)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/tasks/execute", status_code=202)
async def execute_task(body: ExecuteTask):
    global active_task, active_stop
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(422, "The instruction cannot be empty.")
    if active_task is not None:
        raise HTTPException(409, "A task is already running.")
    task = {
        "task_id": uuid.uuid4().hex[:12],
        "instruction": instruction,
        "max_seconds": body.max_seconds,
        "status": "RUNNING",
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
    }
    tasks[task["task_id"]] = task
    active_task = task
    active_stop = threading.Event()
    pool.submit(run_task, task, active_stop)
    return task


@app.get("/api/tasks/{task_id}/status")
def task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "unknown task")
    return tasks[task_id]


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "unknown task")
    if active_task is None or active_task["task_id"] != task_id:
        raise HTTPException(409, "This task is not running.")
    active_stop.set()
    return tasks[task_id]


@app.websocket("/ws/live")
async def live(socket: WebSocket):
    if socket.headers.get("origin") not in ALLOWED_ORIGINS:
        await socket.close(code=1008)
        return
    await socket.accept()
    selected = {"camera": "top"}

    async def receive():
        while True:
            message = await socket.receive_json()
            if isinstance(message, dict) and message.get("camera") in CAMERAS:
                selected["camera"] = message["camera"]

    receiver = asyncio.create_task(receive())
    sent = (None, None)
    latest = None
    try:
        while not receiver.done():
            current = (feed.sequence, selected["camera"])
            task = active_task or latest
            latest = task
            await socket.send_json(
                {
                    "type": "state",
                    "sequence": feed.sequence,
                    "camera": selected["camera"],
                    "phase": live_phase(),
                    "task": task,
                    "objects": feed.objects,
                    "robot": feed.robot,
                    "contacts": feed.contacts,
                }
            )
            if current != sent:
                await socket.send_bytes(await asyncio.to_thread(feed.jpeg, current[1]))
                sent = current
            await asyncio.sleep(STREAM_PERIOD_S)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()

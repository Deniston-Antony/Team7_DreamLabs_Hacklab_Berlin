import asyncio
import math
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import dream_examples
import dream_ik
import mujoco
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
import yam_env  # noqa: E402

DEFAULT_SCENE = "yam_tabletop"
ROBOT_ID = "yam"
CAMERAS = ("top", "left", "right")
X_RANGE = (-0.35, 0.35)
Y_RANGE = (-0.20, 0.25)
Z_RANGE = (0.76, 1.00)
STREAM_PERIOD_S = 0.1
JPEG_QUALITY = 70
WAITING_AFTER_S = 0.3
ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
MAX_TASK_SECONDS = 200
MAX_STL_BYTES = 25 * 1024 * 1024
MAX_STL_TRIANGLES = 100_000
MAX_ADDED_OBJECTS = 10
TABLE_TOP_Z = 0.76
RENDER_WIDTH = 640
RENDER_HEIGHT = 480
RENDER_CROP_TOP = 60
STL_COLORS = ([0.85, 0.35, 0.25, 1], [0.2, 0.6, 0.45, 1], [0.3, 0.45, 0.85, 1], [0.85, 0.65, 0.2, 1], [0.6, 0.4, 0.75, 1])
SHUFFLE_X = (-0.27, 0.27)
SHUFFLE_Y = (-0.12, 0.17)
SHUFFLE_SPACING_M = 0.085
FIXED_CLEARANCE_M = {"bowl": 0.125, "plate": 0.115}
SCENES = {
    "yam_tabletop": {"name": "Default lab"},
    "kitchen": {"name": "Kitchen style", "table": [0.56, 0.48, 0.38], "floor": [0.30, 0.24, 0.19], "walls": [0.46, 0.54, 0.47], "light": [0.40, 0.38, 0.34]},
    "sunny_apartment": {"name": "Sunny apartment style", "table": [0.66, 0.54, 0.38], "floor": [0.52, 0.43, 0.32], "walls": [0.74, 0.70, 0.58], "light": [0.52, 0.49, 0.40]},
    "cottage": {"name": "Cottage style", "table": [0.48, 0.34, 0.22], "floor": [0.34, 0.27, 0.20], "walls": [0.62, 0.56, 0.47], "light": [0.44, 0.40, 0.34]},
    "walnut_study": {"name": "Walnut study style", "table": [0.30, 0.19, 0.12], "floor": [0.20, 0.14, 0.10], "walls": [0.34, 0.27, 0.22], "light": [0.46, 0.40, 0.34]},
    "evening_den": {"name": "Evening den style", "table": [0.24, 0.17, 0.13], "floor": [0.12, 0.11, 0.13], "walls": [0.18, 0.18, 0.24], "light": [0.30, 0.27, 0.34]},
}
ROBOTS_NEEDING_SETUP = (
    ("openyam", "OpenYAM", "openyam_left.urdf"),
    ("wxai", "Trossen WidowX AI", "wxai.urdf"),
    ("makermods_metal", "MakerMods Metal", "*metal*.urdf"),
    ("so101", "SO-101", "so101_left.urdf"),
    ("piper", "AgileX PiPER", "piper_dual_left.urdf"),
    ("ur5e", "Universal Robots UR5e", "ur5e_dual_left.urdf"),
    ("b601", "Seeed reBot B601", "b601_dm.urdf"),
    ("maker_arm", "MakerMods Maker Arm", "maker_arm_ik.urdf"),
)


def parse_stl(data, max_size_m):
    triangles = None
    if len(data) >= 84:
        count = int.from_bytes(data[80:84], "little")
        if 84 + 50 * count == len(data):
            if count > MAX_STL_TRIANGLES:
                raise ValueError(f"The STL has {count} triangles; the limit is {MAX_STL_TRIANGLES}.")
            record = np.dtype([("normal", "<f4", (3,)), ("points", "<f4", (9,)), ("attribute", "<u2")])
            triangles = np.frombuffer(data, record, count, 84)["points"].reshape(-1, 3, 3).astype(float)
    if triangles is None:
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("This is not a valid STL file.") from error
        if not text.lstrip().lower().startswith("solid"):
            raise ValueError("This is not a valid STL file.")
        number = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
        found = re.findall(rf"vertex\s+({number})\s+({number})\s+({number})", text)
        if len(found) % 3 or len(found) / 3 > MAX_STL_TRIANGLES:
            raise ValueError("The STL has an invalid or too large triangle list.")
        triangles = np.array(found, dtype=float).reshape(-1, 3, 3)
    if len(triangles) < 4 or not np.isfinite(triangles).all():
        raise ValueError("The STL has no usable 3D shape.")
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = np.asarray(inverse).reshape(-1, 3)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    extent = high - low
    if len(faces) < 4 or extent.max() <= 0:
        raise ValueError("The STL has no usable 3D shape.")
    scale = max_size_m / extent.max()
    if (extent * scale).min() < 0.001:
        raise ValueError("The STL is too flat to simulate as a solid object.")
    center = (low + high) / 2
    vertices = (vertices - [center[0], center[1], low[2]]) * scale
    return vertices, faces


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
        self.scene_id = DEFAULT_SCENE
        self.grab = None
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

    def _apply_scene(self, spec):
        style = SCENES[self.scene_id]
        if "table" not in style:
            return
        table = spec.geom("table")
        table.material = ""
        table.rgba = [*style["table"], 1]
        for geom in spec.worldbody.geoms:
            if geom.type == mujoco.mjtGeom.mjGEOM_PLANE:
                geom.rgba = [*style["floor"], 1]
        for light in spec.worldbody.lights:
            light.diffuse = style["light"]
        for position, size in (
            ([0, 1.7, 1.5], [3, 0.02, 1.5]),
            ([0, -1.9, 1.5], [3, 0.02, 1.5]),
            ([1.8, 0, 1.5], [0.02, 3, 1.5]),
            ([-1.8, 0, 1.5], [0.02, 3, 1.5]),
        ):
            spec.worldbody.add_geom(
                type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=position,
                size=size,
                rgba=[*style["walls"], 1],
                contype=0,
                conaffinity=0,
            )

    def _rebuild(self, keep_poses=True):
        saved = {}
        if keep_poses:
            for body in self._joint_by_body():
                address = self._qpos(body)[1]
                saved[body] = self.env.data.qpos[address : address + 7].copy()
        spec = mujoco.MjSpec.from_string(yam_env.build_xml())
        for name in self.removed:
            spec.delete(spec.body(name))
        for name, item in self.added.items():
            body = spec.worldbody.add_body(name=name, pos=list(item["position"]))
            body.add_freejoint(name=f"{name}_free")
            if item["vertices"] is None:
                body.add_geom(
                    type=mujoco.mjtGeom.mjGEOM_BOX,
                    size=[0.015, 0.015, 0.015],
                    mass=0.02,
                    rgba=[0.1, 0.35, 0.9, 1],
                )
                continue
            spec.add_mesh(
                name=f"{name}_mesh",
                uservert=item["vertices"].ravel().tolist(),
                userface=item["faces"].ravel().tolist(),
                inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_CONVEX,
            )
            body.add_geom(
                type=mujoco.mjtGeom.mjGEOM_MESH,
                meshname=f"{name}_mesh",
                mass=0.1,
                rgba=item["rgba"],
            )
        self._apply_scene(spec)
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
        if len(self.added) >= MAX_ADDED_OBJECTS:
            raise HTTPException(409, f"The scene already has {MAX_ADDED_OBJECTS} added objects.")
        self.added[name] = {"position": position, "vertices": None, "faces": None, "rgba": None}
        self._rebuild()
        self._set_pose(name, position, [1, 0, 0, 0])
        return name

    def add_stl(self, filename, vertices, faces):
        if len(self.added) >= MAX_ADDED_OBJECTS:
            raise HTTPException(409, f"The scene already has {MAX_ADDED_OBJECTS} added objects.")
        stem = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_")[:24] or "object"
        name = f"stl_{stem}"
        index = 2
        while name in self.added:
            name = f"stl_{stem}_{index}"
            index += 1
        self.added[name] = {
            "position": np.array([0, 0.3, TABLE_TOP_Z + 0.002]),
            "vertices": vertices,
            "faces": faces,
            "rgba": STL_COLORS[len(self.added) % len(STL_COLORS)],
        }
        try:
            self._rebuild()
        except Exception as error:
            del self.added[name]
            self._rebuild()
            raise HTTPException(422, f"MuJoCo could not load this STL: {error}") from error
        rng = np.random.default_rng()
        for _ in range(200):
            self._set_pose(name, [rng.uniform(*SHUFFLE_X), rng.uniform(*SHUFFLE_Y), TABLE_TOP_Z + 0.002], [1, 0, 0, 0])
            if self._objects_clear({name}):
                hold = self.env.state()
                for _ in range(10):
                    self.env.step(hold["left"], hold["right"])
                return name
        del self.added[name]
        self._rebuild()
        raise HTTPException(409, "There is no clear space on the table for this object. Try a smaller size.")

    def _ray(self, camera, u, v):
        m, d = self.env.model, self.env.data
        camera_id = m.camera(camera).id
        focal = (RENDER_HEIGHT / 2) / math.tan(math.radians(m.cam_fovy[camera_id]) / 2)
        local = np.array([(u - RENDER_WIDTH / 2) / focal, -(v + RENDER_CROP_TOP - RENDER_HEIGHT / 2) / focal, -1.0])
        direction = d.cam_xmat[camera_id].reshape(3, 3) @ local
        return d.cam_xpos[camera_id].copy(), direction / np.linalg.norm(direction)

    @staticmethod
    def _at_height(origin, direction, z):
        if direction[2] > -0.05:
            return None
        return origin + direction * ((z - origin[2]) / direction[2])

    def pick(self, camera, u, v):
        m, d = self.env.model, self.env.data
        movable = self._joint_by_body()
        origin, direction = self._ray(camera, u, v)
        hit = np.array([-1], dtype=np.int32)
        mujoco.mj_ray(m, d, origin, direction, None, 1, -1, hit)
        name = None
        if hit[0] >= 0:
            root = m.body(m.body_rootid[m.geom_bodyid[hit[0]]]).name
            name = root if root in movable else None
        if name is None:
            table_point = self._at_height(origin, direction, TABLE_TOP_Z)
            if table_point is not None and movable:
                nearest = min(movable, key=lambda n: np.linalg.norm(d.body(n).xpos[:2] - table_point[:2]))
                if np.linalg.norm(d.body(nearest).xpos[:2] - table_point[:2]) < 0.05:
                    name = nearest
        if name is None:
            self.grab = None
            return None
        address = self._qpos(name)[1]
        z = float(d.qpos[address + 2])
        point = self._at_height(origin, direction, z)
        if point is None:
            raise HTTPException(409, "Use the top camera to drag objects.")
        self.grab = {
            "object_id": name,
            "offset": d.qpos[address : address + 2] - point[:2],
            "start": d.qpos[address : address + 7].copy(),
            "z": z,
        }
        return name

    def drag(self, camera, u, v):
        if self.grab is None:
            raise HTTPException(409, "No object is being dragged.")
        origin, direction = self._ray(camera, u, v)
        point = self._at_height(origin, direction, self.grab["z"])
        if point is None:
            return
        x, y = point[:2] + self.grab["offset"]
        self._set_pose(
            self.grab["object_id"],
            [float(np.clip(x, *X_RANGE)), float(np.clip(y, *Y_RANGE)), self.grab["z"]],
        )

    def drop(self):
        if self.grab is None:
            return {"object_id": None, "restored": False}
        grab, self.grab = self.grab, None
        restored = not self._objects_clear({grab["object_id"]})
        if restored:
            self._set_pose(grab["object_id"], grab["start"][:3], grab["start"][3:])
        hold = self.env.state()
        for _ in range(15):
            self.env.step(hold["left"], hold["right"])
        return {"object_id": grab["object_id"], "restored": restored}

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

    def select_scene(self, scene_id):
        self.scene_id = scene_id
        self._rebuild()

    def _objects_clear(self, movable):
        m, d = self.env.model, self.env.data
        mujoco.mj_forward(m, d)
        for contact in d.contact[: d.ncon]:
            names = {m.body(m.geom_bodyid[contact.geom1]).name, m.body(m.geom_bodyid[contact.geom2]).name}
            if names & movable and "world" not in names:
                return False
        return True

    def shuffle(self):
        m, d = self.env.model, self.env.data
        bodies = list(self._joint_by_body())
        if not bodies:
            raise HTTPException(409, "There are no movable objects to shuffle.")
        rng = np.random.default_rng()
        saved_qpos, saved_qvel = d.qpos.copy(), d.qvel.copy()
        fixed = [(d.body(n).xpos[:2].copy(), r) for n, r in FIXED_CLEARANCE_M.items()]
        for _ in range(200):
            placed = []
            for _ in bodies:
                for _ in range(80):
                    point = np.array([rng.uniform(*SHUFFLE_X), rng.uniform(*SHUFFLE_Y)])
                    if all(np.linalg.norm(point - c) >= r for c, r in fixed) and all(
                        np.linalg.norm(point - other) >= SHUFFLE_SPACING_M for other in placed
                    ):
                        placed.append(point)
                        break
            if len(placed) != len(bodies):
                continue
            for body, point in zip(bodies, placed):
                address = self._qpos(body)[1]
                half = rng.uniform(0, np.pi)
                self._set_pose(
                    body,
                    [point[0], point[1], m.qpos0[address + 2]],
                    [np.cos(half), 0, 0, np.sin(half)],
                )
            if self._objects_clear(set(bodies)):
                hold = self.env.state()
                for _ in range(20):
                    self.env.step(hold["left"], hold["right"])
                return
        d.qpos[:], d.qvel[:] = saved_qpos, saved_qvel
        mujoco.mj_forward(m, d)
        raise HTTPException(409, "Could not find a clear layout. Press Reset and try again.")

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


class SelectRobot(BaseModel):
    robot_id: str


class AddObject(BaseModel):
    type: str
    position: Position


class ObjectRef(BaseModel):
    object_id: str


class MoveObject(ObjectRef):
    position: Position


class RotateObject(ObjectRef):
    yaw_deg: float = Field(ge=-360, le=360)


class ViewPoint(BaseModel):
    camera: str
    u: float = Field(ge=0, le=RENDER_WIDTH)
    v: float = Field(ge=0, le=RENDER_HEIGHT - 2 * RENDER_CROP_TOP)


class ExecuteTask(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    max_seconds: int = Field(default=MAX_TASK_SECONDS, ge=5, le=MAX_TASK_SECONDS)


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


def task_view(task):
    if task is None:
        return None
    finished = task["finished_at"] or time.time()
    return {**task, "elapsed_s": round(max(0.0, finished - task["started_at"]), 1)}


def live_phase():
    if active_task is None:
        return "idle"
    if time.monotonic() - feed.last_step > WAITING_AFTER_S:
        return "waiting_for_dream"
    return "running"


@app.get("/api/scenes")
def scenes():
    return [
        {"scene_id": scene_id, "name": style["name"], "source": "local MuJoCo scene style"}
        for scene_id, style in SCENES.items()
    ]


@app.get("/api/robots")
def robots():
    assets = Path(dream_ik.__file__).parent / "assets"
    extra = [
        {"robot_id": robot_id, "name": name, "executable": False, "source": "Dream IK model; needs simulator setup"}
        for robot_id, name, pattern in ROBOTS_NEEDING_SETUP
        if next(assets.glob(pattern), None) is not None
    ]
    return [{"robot_id": ROBOT_ID, "name": "YAM (two arms)", "executable": True, "source": "local MuJoCo example"}, *extra]


@app.get("/api/objects")
def objects():
    return {"placed": feed.objects, "catalog": ["block"]}


@app.post("/api/scene/select")
async def select_scene(body: SelectScene):
    if body.scene_id not in SCENES:
        return unsupported("scene_select", f"{body.scene_id!r} is not a local simulator scene.")
    await change_scene(sim.select_scene, body.scene_id)
    return {"scene_id": body.scene_id}


@app.post("/api/robot/select")
def select_robot(body: SelectRobot):
    if body.robot_id != ROBOT_ID:
        return unsupported(
            "robot_select",
            "This arm needs simulator actuators, cameras and a person-measured tool center point before it can run.",
        )
    return {"robot_id": ROBOT_ID}


@app.post("/api/scene/reset")
async def reset_scene():
    await change_scene(sim.reset)
    return {"placed": feed.objects}


@app.post("/api/objects/shuffle")
async def shuffle_objects():
    await change_scene(sim.shuffle)
    return {"placed": feed.objects}


@app.post("/api/objects/upload")
async def upload_stl(request: Request, filename: str = Query(min_length=1, max_length=200), size_cm: float = Query(default=8, ge=1, le=25)):
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Send the STL as application/octet-stream.")
    if not filename.lower().endswith(".stl"):
        raise HTTPException(422, "Only .stl files can be added.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_STL_BYTES:
            raise HTTPException(413, "The STL is larger than 25 MB.")
        chunks.append(chunk)
    try:
        vertices, faces = await asyncio.to_thread(parse_stl, b"".join(chunks), size_cm / 100)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"object_id": await change_scene(sim.add_stl, filename, vertices, faces)}


@app.post("/api/view/pick")
async def view_pick(body: ViewPoint):
    if body.camera not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    return {"object_id": await change_scene(sim.pick, body.camera, body.u, body.v)}


@app.post("/api/view/drag")
async def view_drag(body: ViewPoint):
    if body.camera not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    await change_scene(sim.drag, body.camera, body.u, body.v)
    return {"ok": True}


@app.post("/api/view/drop")
async def view_drop():
    return await change_scene(sim.drop)


@app.get("/api/sessions")
def sessions():
    return {
        "active_session": "simulation-1",
        "sessions": [{"session_id": "simulation-1", "name": "Simulation 1", "scene_id": sim.scene_id, "busy": active_task is not None}],
        "max_parallel": 1,
        "parallel_supported": False,
    }


@app.post("/api/sessions")
def create_session():
    return unsupported("parallel_simulations", "Multiple simulations are planned but not implemented yet.")


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
                    "scene_id": sim.scene_id,
                    "phase": live_phase(),
                    "task": task_view(task),
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

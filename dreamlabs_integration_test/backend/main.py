import asyncio
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import dream_examples
import mujoco
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import yam_env  # noqa: E402

SCENE_ID = "yam_tabletop"
ROBOT_ID = "yam"
CAMERAS = ("top", "left", "right")
X_RANGE = (-0.35, 0.35)
Y_RANGE = (-0.20, 0.25)
Z_RANGE = (0.76, 1.00)


class SimAdapter:
    def __init__(self):
        self.env = yam_env.YamEnv()
        self.added = {}
        self.removed = set()

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
        m, d = self.env.model, self.env.data
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

    def frame(self, camera):
        image = self.env.images()[camera]
        ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        if not ok:
            raise HTTPException(500, "could not encode the camera frame")
        return jpeg.tobytes()


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


# MuJoCo rendering contexts are thread-bound, so all simulator work stays on one thread.
pool = ThreadPoolExecutor(max_workers=1)
sim = pool.submit(SimAdapter).result()
app = FastAPI(title="Dream Labs integration test")


async def on_sim(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)


def unsupported(capability, reason):
    return JSONResponse(
        status_code=501,
        content={"error": "unsupported_capability", "capability": capability, "reason": reason},
    )


@app.get("/api/scenes")
def scenes():
    return [{"scene_id": SCENE_ID, "name": "Dream YAM Sim tabletop", "source": "local MuJoCo example"}]


@app.get("/api/robots")
def robots():
    return [{"robot_id": ROBOT_ID, "name": "YAM (two arms)", "source": "local MuJoCo example"}]


@app.get("/api/objects")
async def objects():
    return {"placed": await on_sim(sim.objects), "catalog": ["block"]}


@app.post("/api/scene/select")
def select_scene(body: SelectScene):
    if body.scene_id != SCENE_ID:
        return unsupported("scene_select", "Hosted Playground rooms have no documented external API.")
    return {"scene_id": SCENE_ID}


@app.post("/api/scene/reset")
async def reset_scene():
    await on_sim(sim.reset)
    return {"placed": await on_sim(sim.objects)}


@app.post("/api/objects/add")
async def add_object(body: AddObject):
    if body.type != "block":
        return unsupported("add_object", f"{body.type!r} is not in the local simulator catalog.")
    return {"object_id": await on_sim(sim.add, body.position.array())}


@app.post("/api/objects/remove")
async def remove_object(body: ObjectRef):
    await on_sim(sim.remove, body.object_id)
    return {"removed": body.object_id}


@app.post("/api/objects/move")
async def move_object(body: MoveObject):
    await on_sim(sim.move, body.object_id, body.position.array())
    return {"object_id": body.object_id}


@app.post("/api/objects/rotate")
async def rotate_object(body: RotateObject):
    await on_sim(sim.rotate, body.object_id, body.yaw_deg)
    return {"object_id": body.object_id}


@app.get("/api/frame/{camera}")
async def frame(camera: str):
    if camera not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    jpeg = await on_sim(sim.frame, camera)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/tasks/execute")
def execute_task(body: ExecuteTask):
    return unsupported("task_execute", "Live Dream-1 execution is off until a person approves a billed test.")


@app.get("/api/tasks/{task_id}/status")
def task_status(task_id: str):
    return unsupported("task_status", "No task can exist while live execution is off.")

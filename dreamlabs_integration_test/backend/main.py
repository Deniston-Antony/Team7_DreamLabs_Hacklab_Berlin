import asyncio
from collections import OrderedDict
import copy
import random
import math
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import cv2
import dream_examples
import dream_ik
import mujoco
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
import yam_env  # noqa: E402
import outcome  # noqa: E402
import capacity  # noqa: E402
import dream_client_log  # noqa: E402
import roundtrip_log  # noqa: E402  (SortFlow's; outcome puts SortFlow on the path)
import video_judge  # noqa: E402
import judges  # noqa: E402
import evaluation  # noqa: E402
import environments  # noqa: E402
import scene_saves  # noqa: E402
import suggestions  # noqa: E402

DEFAULT_SCENE = "yam_tabletop"
ROBOT_ID = "yam"
CAMERAS = ("top", "left", "right")
FREE_VIEW = "free"
VIEWS = (*CAMERAS, FREE_VIEW)
X_RANGE = (-0.35, 0.35)
Y_RANGE = (-0.20, 0.25)
Z_RANGE = (0.76, 1.00)
STREAM_PERIOD_S = 0.1
JPEG_QUALITY = 70
WAITING_AFTER_S = 0.3
ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
PUBLIC_ORIGIN = os.environ.get("DREAM_PUBLIC_ORIGIN", "").rstrip("/")  # the hosted address, behind its login
if PUBLIC_ORIGIN:
    ALLOWED_ORIGINS.add(PUBLIC_ORIGIN)
MAX_TASK_SECONDS = 200
DEFAULT_TASK_SECONDS = 120
SUGGESTION_COST_PER_120S = 0.07  # the website's displayed estimate
MAX_STL_BYTES = 60 * 1024 * 1024
MAX_STL_TRIANGLES = 1_500_000
SIM_MESH_TRIANGLES = 60_000
COLLISION_HULL_VERTICES = 64
ORBIT_DEG_PER_PIXEL = 0.35
ZOOM_RATE = 0.0025
ZOOM_RANGE = (0.35, 3.0)
SCENE_OBJECTS = ("apple", "banana", "orange", "water_bottle", "bowl", "plate")
HIGHLIGHT_RGB = (255, 120, 0)
GOAL_SETTLED_FRAMES = 30
GOAL_SPEED_M_S = 0.03
GOAL_TARGETS = {"bowl": 0.08, "plate": 0.07}
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


def simplify_mesh(vertices, faces):
    low = vertices.min(axis=0)
    span = (vertices.max(axis=0) - low).max()

    def cluster(resolution):
        cells = np.minimum(((vertices - low) / span * resolution).astype(np.int64), resolution)
        size = resolution + 1
        _, inverse = np.unique(cells[:, 0] + cells[:, 1] * size + cells[:, 2] * size * size, return_inverse=True)
        mapped = inverse[faces]
        keep = (mapped[:, 0] != mapped[:, 1]) & (mapped[:, 1] != mapped[:, 2]) & (mapped[:, 0] != mapped[:, 2])
        return inverse, mapped[keep]

    best = None
    lower, upper = 8, 1024
    while lower <= upper:
        resolution = (lower + upper) // 2
        inverse, mapped = cluster(resolution)
        if len(mapped) <= SIM_MESH_TRIANGLES:
            best = (inverse, mapped)
            lower = resolution + 1
        else:
            upper = resolution - 1
    if best is None:
        raise ValueError("The STL could not be simplified for simulation.")
    inverse, mapped = best
    counts = np.bincount(inverse)
    merged = np.column_stack([np.bincount(inverse, vertices[:, axis]) / counts for axis in range(3)])
    _, unique_faces = np.unique(np.sort(mapped, axis=1), axis=0, return_index=True)
    mapped = mapped[np.sort(unique_faces)]
    used, compact = np.unique(mapped, return_inverse=True)
    return merged[used], np.asarray(compact).reshape(-1, 3)


def parse_goal(instruction, movable):
    match = re.search(r"\b(?:put|place|move|drop|set)\b(.*?)\b(?:into|inside|onto|in|on)\b(.*)", instruction.lower())
    if not match:
        return None
    target_words = re.findall(r"[a-z]+", match.group(2))
    target = next((word for word in target_words if word in GOAL_TARGETS), None)
    if target is None:
        return None
    subject = set(re.findall(r"[a-z]+", match.group(1)))
    found = []
    for body in movable:
        words = {word for word in re.findall(r"[a-z]+", body) if word != "stl"}
        if body == "water_bottle":
            words = {"bottle"}
        if words & subject:
            found.append(body)
    return {"object": found[0], "target": target} if len(found) == 1 else None


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
    original = len(faces)
    if original > SIM_MESH_TRIANGLES:
        vertices, faces = simplify_mesh(vertices, faces)
        low, high = vertices.min(axis=0), vertices.max(axis=0)
    center = (low + high) / 2
    vertices = (vertices - [center[0], center[1], low[2]]) * scale
    return vertices, faces, original


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
        self.selected = None
        self.watched_view = "top"
        self.type_overrides = {}
        self.goal = None
        self.capture = None
        self.clock = None
        self.view_lock = threading.Lock()
        self.view_version = 0
        self.watched_attempt = 1
        self.free_camera = mujoco.MjvCamera()
        self.free_camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.free_camera.lookat[:] = [0, -0.02, 0.78]
        self.free_camera.distance = 1.25
        self.free_camera.azimuth = -90
        self.free_camera.elevation = -35
        self.last_publish = 0.0
        self.env.on_images = self._on_images
        self.publish()

    def _on_images(self, images):
        now = time.monotonic()
        self.feed.last_step = now
        self._check_goal()
        if self.clock is not None:
            self.clock.tick()
        if self.capture is not None:
            self.capture.on_step(images)
        if now - self.last_publish < STREAM_PERIOD_S:
            return
        self.last_publish = now
        state = self.env.state()
        with self.view_lock:
            self.env.renderer.update_scene(self.env.data, camera=self.free_camera)
        free = self.env.renderer.render()[RENDER_CROP_TOP : RENDER_HEIGHT - RENDER_CROP_TOP].copy()
        views = {**images, FREE_VIEW: free}
        if self.selected is not None and active_task is None:
            view = self.watched_view
            views[view] = self._outline(views[view], self.free_camera if view == FREE_VIEW else view)
        self.feed.publish(
            views,
            self.objects(),
            {side: np.round(values, 4).tolist() for side, values in state.items()},
            int(self.env.data.ncon),
        )

    def _outline(self, image, camera):
        m, d = self.env.model, self.env.data
        renderer = self.env.renderer
        root = m.body(self.selected).id
        geoms = np.flatnonzero(m.body_rootid[m.geom_bodyid] == root)
        renderer.enable_segmentation_rendering()
        try:
            renderer.update_scene(d, camera=camera)
            segmentation = renderer.render()[RENDER_CROP_TOP : RENDER_HEIGHT - RENDER_CROP_TOP]
        finally:
            renderer.disable_segmentation_rendering()
        mask = (segmentation[..., 1] == mujoco.mjtObj.mjOBJ_GEOM) & np.isin(segmentation[..., 0], geoms)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        outlined = image.copy()
        cv2.drawContours(outlined, contours, -1, HIGHLIGHT_RGB, 2, cv2.LINE_AA)
        return outlined

    def managed(self):
        return [name for name in SCENE_OBJECTS if name not in self.removed] + list(self.added)

    def select(self, name):
        if name is not None and name not in self.managed():
            raise HTTPException(404, f"no object named {name!r}")
        self.selected = name

    def set_movable(self, name, movable):
        if name not in self.managed():
            raise HTTPException(404, f"no object named {name!r}")
        self.type_overrides[name] = movable
        self.grab = None
        self._rebuild()

    def zoom(self, delta):
        with self.view_lock:
            distance = self.free_camera.distance * math.exp(delta * ZOOM_RATE)
            self.free_camera.distance = float(np.clip(distance, *ZOOM_RANGE))
            self.view_version += 1

    def free_view_params(self):
        with self.view_lock:
            return {
                "lookat": [float(value) for value in self.free_camera.lookat],
                "distance": float(self.free_camera.distance),
                "azimuth": float(self.free_camera.azimuth),
                "elevation": float(self.free_camera.elevation),
            }

    def orbit(self, dx, dy):
        with self.view_lock:
            self.free_camera.azimuth = (self.free_camera.azimuth - dx * ORBIT_DEG_PER_PIXEL) % 360
            self.free_camera.elevation = float(np.clip(self.free_camera.elevation - dy * ORBIT_DEG_PER_PIXEL, -85, -5))
            self.view_version += 1

    def goal_reached(self, name, target):
        m, d = self.env.model, self.env.data
        joint = self._joint_by_body().get(name)
        if joint is None:
            return False
        offset = np.linalg.norm(d.body(name).xpos[:2] - d.body(target).xpos[:2])
        dof = m.joint(joint).dofadr[0]
        if offset >= GOAL_TARGETS[target] or np.linalg.norm(d.qvel[dof : dof + 3]) >= GOAL_SPEED_M_S:
            return False
        touches_target = False
        for contact in d.contact[: d.ncon]:
            names = {m.body(m.geom_bodyid[contact.geom1]).name, m.body(m.geom_bodyid[contact.geom2]).name}
            if name not in names:
                continue
            other = next(iter(names - {name}), "")
            if other.startswith(("left_", "right_")):
                return False
            touches_target = touches_target or other == target
        return touches_target

    def _check_goal(self):
        goal = self.goal
        if goal is None or goal["met"]:
            return
        goal["frames"] = goal["frames"] + 1 if self.goal_reached(goal["object"], goal["target"]) else 0
        if goal["frames"] >= GOAL_SETTLED_FRAMES:
            goal["met"] = True
            goal["stop"].set()

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
            if body in self.managed():
                raise HTTPException(409, f"{body} is fixed. Set its type to Movable first.")
            raise HTTPException(404, f"no object named {body!r}")
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

    def _apply_scene(self, spec, scene_id=None):
        style = SCENES[scene_id or self.scene_id]
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

    def _compile(self, saved, scene_id=None):
        """Compile the scene: the Dream YAM scene with this adapter's added, removed and re-typed objects.
        @param saved Poses by body name, used to place objects that are fixed.
        @param scene_id The scene style; None uses the current one. Another style gives the same objects and
            joints, so a simulation state can be moved between the two models.
        @return The compiled MjModel.
        """
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
                maxhullvert=COLLISION_HULL_VERTICES,
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
        for name, movable in self.type_overrides.items():
            body = spec.body(name)
            if body is None:
                continue
            joints = list(body.joints)
            if movable and not joints:
                body.add_freejoint(name=f"{name}_free")
            elif not movable:
                for joint in joints:
                    spec.delete(joint)
                if name in saved:
                    body.pos = saved[name][:3]
                    body.quat = saved[name][3:]
        self._apply_scene(spec, scene_id)
        return spec.compile()

    def _rebuild(self, keep_poses=True, poses=None):
        saved = dict(poses or {})  # poses to put objects back to, such as a saved layout's
        if keep_poses:
            for body in self.managed():
                if mujoco.mj_name2id(self.env.model, mujoco.mjtObj.mjOBJ_BODY, body) >= 0:
                    data = self.env.data.body(body)
                    saved[body] = np.r_[data.xpos, data.xquat]
        model = self._compile(saved)
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
        names = self.managed()
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
                self.selected = name
                return name
        del self.added[name]
        self._rebuild()
        raise HTTPException(409, "There is no clear space on the table for this object. Try a smaller size.")

    def _ray(self, camera, u, v):
        m, d = self.env.model, self.env.data
        if camera == FREE_VIEW:
            with self.view_lock:
                azimuth = math.radians(self.free_camera.azimuth)
                elevation = math.radians(self.free_camera.elevation)
                lookat = self.free_camera.lookat.copy()
                distance = self.free_camera.distance
            forward = np.array(
                [math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth), math.sin(elevation)]
            )
            right = np.cross(forward, [0, 0, 1])
            right /= np.linalg.norm(right)
            up = np.cross(right, forward)
            focal = (RENDER_HEIGHT / 2) / math.tan(math.radians(m.vis.global_.fovy) / 2)
            direction = (
                forward
                + right * (u - RENDER_WIDTH / 2) / focal
                + up * (RENDER_HEIGHT / 2 - v - RENDER_CROP_TOP) / focal
            )
            return lookat - distance * forward, direction / np.linalg.norm(direction)
        return environments.camera_ray(m, d, camera, u, v)

    @staticmethod
    def _at_height(origin, direction, z):
        if direction[2] > -0.05:
            return None
        return origin + direction * ((z - origin[2]) / direction[2])

    def pick(self, camera, u, v):
        m, d = self.env.model, self.env.data
        managed = self.managed()
        movable = self._joint_by_body()
        origin, direction = self._ray(camera, u, v)
        hit = np.array([-1], dtype=np.int32)
        mujoco.mj_ray(m, d, origin, direction, None, 1, -1, hit)
        name = None
        if hit[0] >= 0:
            root = m.body(m.body_rootid[m.geom_bodyid[hit[0]]]).name
            name = root if root in managed else None
        if name is None and hit[0] >= 0 and m.body_rootid[m.geom_bodyid[hit[0]]] == 0:
            table_point = self._at_height(origin, direction, TABLE_TOP_Z)
            if table_point is not None and managed:
                nearest = min(managed, key=lambda n: np.linalg.norm(d.body(n).xpos[:2] - table_point[:2]))
                if np.linalg.norm(d.body(nearest).xpos[:2] - table_point[:2]) < 0.035:
                    name = nearest
        self.grab = None
        self.selected = name
        if name is None:
            return {"object_id": None, "movable": False}
        if name not in movable:
            return {"object_id": name, "movable": False}
        address = self._qpos(name)[1]
        z = float(d.qpos[address + 2])
        point = self._at_height(origin, direction, z)
        if point is None:
            raise HTTPException(409, "Raise the camera angle to drag this object.")
        self.grab = {
            "object_id": name,
            "offset": d.qpos[address : address + 2] - point[:2],
            "start": d.qpos[address : address + 7].copy(),
            "z": z,
        }
        return {"object_id": name, "movable": True}

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
        for _ in range(4):
            self.env.step(hold["left"], hold["right"])
        return {"object_id": grab["object_id"], "restored": restored}

    def remove(self, name):
        if name not in self.managed():
            raise HTTPException(404, f"no object named {name!r}")
        if name in self.added:
            del self.added[name]
        else:
            self.removed.add(name)
        self.type_overrides.pop(name, None)
        self.grab = None
        if self.selected == name:
            self.selected = None
        self._rebuild()

    def move(self, name, position):
        self._set_pose(name, position)

    def rotate(self, name, yaw_deg):
        address = self._qpos(name)[1]
        half = math.radians(yaw_deg) / 2
        turn = np.array([math.cos(half), 0, 0, math.sin(half)])
        rotated = np.zeros(4)
        mujoco.mju_mulQuat(rotated, turn, self.env.data.qpos[address + 3 : address + 7].copy())
        mujoco.mju_normalize4(rotated)
        self._set_pose(name, quat=rotated)

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
        fixed = [
            (d.body(n).xpos[:2].copy(), FIXED_CLEARANCE_M.get(n, 0.09)) for n in self.managed() if n not in bodies
        ]
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
        self.type_overrides.clear()
        self.grab = None
        self.selected = None
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


class ObjectType(ObjectRef):
    movable: bool


class SelectObject(BaseModel):
    object_id: str | None = None


class ZoomView(BaseModel):
    delta: float = Field(ge=-2000, le=2000)


class OrbitView(BaseModel):
    dx: float = Field(ge=-4000, le=4000)
    dy: float = Field(ge=-4000, le=4000)


class ViewPoint(BaseModel):
    camera: str
    u: float = Field(ge=0, le=RENDER_WIDTH)
    v: float = Field(ge=0, le=RENDER_HEIGHT - 2 * RENDER_CROP_TOP)


JudgeName = Literal["offline", "claude"]


class SubTask(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    attempts: int = Field(default=1, ge=1, le=outcome.MAX_ATTEMPTS)


class ExecuteTask(BaseModel):
    # One task as `instruction` (with `attempts`), or several at once as `tasks`, each with its own attempts.
    instruction: str | None = Field(default=None, min_length=1, max_length=500)
    max_seconds: int = Field(default=DEFAULT_TASK_SECONDS, ge=5, le=MAX_TASK_SECONDS)
    attempts: int = Field(default=1, ge=1, le=outcome.MAX_ATTEMPTS)
    tasks: list[SubTask] | None = Field(default=None, min_length=1, max_length=outcome.MAX_ATTEMPTS)
    # What the parallel runs start from: a copy of the live scene, shuffled positions, or shuffled + other styles.
    environments: str = Field(default="same", pattern="^(same|shuffle|shuffle_style)$")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    judge: JudgeName | None = None  # None keeps the instruction exactly as written, for API callers


class SuggestionChoice(BaseModel):
    tests: list[int] = Field(min_length=1, max_length=8)
    judge: JudgeName = "offline"


class JudgePreview(BaseModel):
    judge: JudgeName
    instructions: list[str] = Field(min_length=1, max_length=outcome.MAX_ATTEMPTS)


class PrepareEnvironments(BaseModel):
    count: int = Field(ge=0, le=outcome.MAX_ATTEMPTS - 1)
    mode: str = Field(default="shuffle", pattern="^(same|shuffle|shuffle_style)$")
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


# MuJoCo rendering contexts are thread-bound, so all simulator work stays on one thread.
pool = ThreadPoolExecutor(max_workers=1)
feed = LiveFeed()
sim = pool.submit(SimAdapter, feed).result()
tasks = {}
active_task = None
active_stop = None
active_clock = None
active_dream_log = None  # the live run's Dream-1 log (roundtrip_log.RoundTripLog) while a task runs
environments.free_view = sim.free_view_params
view_pool = ThreadPoolExecutor(max_workers=1)  # renders finished parallel runs while the simulator thread is busy
final_scenes = OrderedDict()
final_frames = OrderedDict()
grid_lock = threading.Lock()
app = FastAPI(title="Dream Labs integration test")
evaluations = evaluation.EvaluationStore(
    Path(os.environ.get("DREAM_EVALUATION_DATA", Path(__file__).resolve().parent / "evaluation_data"))
)
app.include_router(evaluation.create_router(evaluations))
vision_checks = evaluation.VisionQueue(evaluations, Path(__file__).resolve().parent)
_open_policy = dc.open_policy


def open_counted_policy(config, key):
    client = _open_policy(config, key)
    if active_clock is not None:
        client = capacity.CapacityClient(client, active_clock)
    if sim.capture is not None:
        sim.capture.watch_model_calls(client)
    if active_dream_log is not None:
        active_dream_log.watch(client)
    return client


dc.open_policy = open_counted_policy


async def on_sim(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)


def environment_rules(movable, present):
    """What editing a parallel environment needs to know about the live scene's objects."""
    return {
        "movable": list(movable),
        "managed": list(present),
        "clearance": dict(FIXED_CLEARANCE_M),
        "shuffle_x": SHUFFLE_X,
        "shuffle_y": SHUFFLE_Y,
        "spacing": SHUFFLE_SPACING_M,
        "drag_x": X_RANGE,
        "drag_y": Y_RANGE,
        "table_z": TABLE_TOP_Z,
    }


def prepare_environments(count, mode, seed, with_previews=False, keep=None):
    """Build the starting scenes of `count` parallel runs from the live scene; runs on the simulator thread.
    @param count How many parallel runs.
    @param mode `same` (a copy of the live scene), `shuffle` (new object positions) or `shuffle_style` (new
        positions and another scene style each).
    @param seed Seed for the layouts; the same scene, mode and seed give the same environments.
    @param with_previews Also render each environment's three cameras for the run tabs.
    @param keep Environments to keep instead of building them again, by run number (environments the user edited).
        One is only kept while it still has the live scene's objects.
    @return A list of environments.Environment for runs 2, 3, ...
    """
    movable = list(sim._joint_by_body())
    present = [n for n in sim.managed() if mujoco.mj_name2id(sim.env.model, mujoco.mjtObj.mjOBJ_BODY, n) >= 0]
    fixed = [(sim.env.data.body(n).xpos[:2].copy(), FIXED_CLEARANCE_M.get(n, 0.09)) for n in present if n not in movable]
    saved = {n: np.r_[sim.env.data.body(n).xpos, sim.env.data.body(n).xquat] for n in present}
    styles = list(SCENES)
    models = {sim.scene_id: sim.env.model}
    rules = environment_rules(movable, present)
    envs = []
    for index in range(count):
        kept = (keep or {}).get(index + 2)
        if kept is not None and kept.model.nq == sim.env.model.nq and kept.rules.get("managed") == rules["managed"]:
            kept.rules = {**rules, "restyled": kept.rules.get("restyled", True)}  # Movable/Fixed may have changed
            envs.append(kept)
            continue
        style = styles[(styles.index(sim.scene_id) + 1 + index) % len(styles)] if mode == "shuffle_style" else sim.scene_id
        if style not in models:
            models[style] = sim._compile(saved, style)
        model = models[style]
        if model.nq != sim.env.model.nq:  # a style must not change the objects; fall back to the live scene's
            style, model = sim.scene_id, sim.env.model
        data = mujoco.MjData(model)
        data.qpos[:], data.qvel[:], data.ctrl[:] = sim.env.data.qpos, sim.env.data.qvel, sim.env.data.ctrl
        mujoco.mj_forward(model, data)
        shuffled = mode != "same" and environments.shuffle_layout(
            model, data, movable, fixed, np.random.default_rng([seed, index]), SHUFFLE_X, SHUFFLE_Y, SHUFFLE_SPACING_M
        )
        environment = environments.Environment(
            index + 2,
            model,
            data,
            style,
            SCENES[style]["name"],
            seed,
            shuffled,
            {**rules, "restyled": style != sim.scene_id},
        )
        if with_previews:
            environment.previews = environments.render_previews(model, data)
        envs.append(environment)
    return envs


def edited_environments():
    """The prepared environments the user changed by hand, by run number."""
    shown = outcome.previews
    return {e.attempt: e for e in shown["envs"] if e.edited} if shown else {}


def run_environments(task):
    """The parallel runs' starting scenes: the prepared ones the run tabs show (with the user's edits) when they
    match the request, otherwise new ones; None runs every parallel run from a copy of the live scene."""
    needed = max(entry["attempts"] for entry in task["plan"]) - 1  # the n-th run of every task shares scene n
    if needed < 1:
        return None
    shown = outcome.previews
    mode = task.get("environments", "same")
    if shown and shown["count"] >= needed and (shown["mode"] == mode or any(e.edited for e in shown["envs"])):
        return shown["envs"][:needed]
    if mode == "same":
        return None
    return prepare_environments(needed, mode, task["seed"])


def live_state():
    """The live scene as a save keeps it: its objects and the full simulator state."""
    return {
        "scene_id": sim.scene_id,
        "added": sim.added,
        "removed": sim.removed,
        "type_overrides": sim.type_overrides,
        "poses": capture_layout()["poses"],
        "qpos": sim.env.data.qpos.copy(),
        "qvel": sim.env.data.qvel.copy(),
        "ctrl": sim.env.data.ctrl.copy(),
    }


def save_scene(task, envs):
    """Save the starting scene of every run of a task, before the robot moves; a failure never stops the run."""
    try:
        saved = scene_saves.write(
            task["task_id"], task, live_state(), envs or [], task.get("environments", "same"), task.get("seed")
        )
        task["scene_save"] = saved["save_id"]
    except Exception as error:
        task["scene_save_error"] = f"The starting scene could not be saved: {type(error).__name__}: {error}"


def load_scene(save_id):
    """Put the live scene and every parallel run back exactly as a save has them; runs on the simulator thread.
    @return The save's summary, so the page can set Total runs, the tasks and Start scenes to match.
    """
    description, arrays = scene_saves.read(save_id)
    if description["scene_id"] not in SCENES:
        raise scene_saves.SaveError(f"The saved scene style {description['scene_id']!r} no longer exists.", 422)
    sim.scene_id = description["scene_id"]
    sim.added.clear()
    sim.added.update(scene_saves.added_objects(description, arrays))
    sim.removed.clear()
    sim.removed.update(description["removed"])
    sim.type_overrides.clear()
    sim.type_overrides.update(description["type_overrides"])
    sim.grab = None
    sim.selected = None
    sim._rebuild(keep_poses=False, poses={name: np.array(pose) for name, pose in description["poses"].items()})
    model, data = sim.env.model, sim.env.data
    if model.nq != arrays["live_qpos"].size or model.nu != arrays["live_ctrl"].size:
        raise scene_saves.SaveError("The saved scene does not match the simulator's objects any more.", 422)
    data.qpos[:], data.qvel[:], data.ctrl[:] = arrays["live_qpos"], arrays["live_qvel"], arrays["live_ctrl"]
    mujoco.mj_forward(model, data)
    movable = list(sim._joint_by_body())
    present = [n for n in sim.managed() if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, n) >= 0]
    rules = environment_rules(movable, present)
    poses = {n: np.r_[data.body(n).xpos, data.body(n).xquat] for n in present}
    models = {sim.scene_id: model}
    envs = []
    for run in description["runs"]:
        style = run["style"] if run["style"] in SCENES else sim.scene_id
        if style not in models:
            models[style] = sim._compile(poses, style)
        run_model = models[style]
        prefix = f"run{run['attempt']}"
        if run_model.nq != arrays[f"{prefix}_qpos"].size:
            raise scene_saves.SaveError(f"Parallel run {run['attempt']}'s saved scene does not match its objects.", 422)
        run_data = mujoco.MjData(run_model)
        run_data.qpos[:], run_data.qvel[:] = arrays[f"{prefix}_qpos"], arrays[f"{prefix}_qvel"]
        run_data.ctrl[:] = arrays[f"{prefix}_ctrl"]
        environment = environments.Environment(
            run["attempt"],
            run_model,
            run_data,
            style,
            SCENES[style]["name"],
            run["seed"],
            run["shuffled"],
            {**rules, "restyled": style != sim.scene_id},
        )
        environment.edited = run["edited"]
        environment.previews = environments.render_previews(run_model, run_data)
        envs.append(environment)
    outcome.clear_runs()
    if envs:
        outcome.set_previews(envs, description["mode"], description["seed"])
    else:
        outcome.clear_previews()
    sim.publish()
    return scene_saves.summary(description)


def refresh_previews(keep_edits=True):
    """Rebuild the prepared environments from the scene as it is now; runs on the simulator thread.
    @param keep_edits Keep the environments the user edited by hand; False rebuilds every one (Reset scene).
    """
    shown = outcome.previews
    if shown:
        keep = edited_environments() if keep_edits else None
        envs = prepare_environments(shown["count"], shown["mode"], shown["seed"], True, keep)
        outcome.set_previews(envs, shown["mode"], shown["seed"])


def preview_environment(attempt):
    """The prepared environment of a parallel run, for editing it; 404 when that run has none."""
    for environment in (outcome.previews or {}).get("envs", []):
        if environment.attempt == attempt:
            return environment
    raise HTTPException(404, f"Parallel run {attempt} has no environment to edit. Set Runs to {attempt} or more.")


async def edit_environment(attempt, method, *args):
    """Run one edit of a parallel run's environment on the simulator thread; refused while a task runs."""
    if active_task is not None:
        raise HTTPException(409, "Parallel runs cannot be changed while a task is running.")

    def apply():
        environment = preview_environment(attempt)
        outcome.clear_runs()  # the run tabs show the environments being edited, not the last run's pictures
        try:
            return getattr(environment, method)(*args)
        except environments.EditError as error:
            raise HTTPException(error.status, str(error)) from error

    return await on_sim(apply)


async def change_scene(fn, *args, refresh=True):
    if active_task is not None:
        raise HTTPException(409, "The scene cannot change while a task is running.")

    def apply():
        result = fn(*args)
        sim.publish()
        if refresh:  # the parallel environments follow the live scene they are made from (not on every drag step)
            refresh_previews()
        return result

    return await on_sim(apply)


def unsupported(capability, reason):
    return JSONResponse(
        status_code=501,
        content={"error": "unsupported_capability", "capability": capability, "reason": reason},
    )


def task_definition(task):
    parsed = evaluation.parse_instruction(task["instruction"], sim.managed())
    return evaluation.TaskDefinition(
        task_id=task["task_id"],
        batch_id=task["batch_id"],
        scene_id=sim.scene_id,
        robot_id=ROBOT_ID,
        instruction=task["instruction"],
        criteria=parsed.criteria,
        protected_objects=parsed.protected,
        unresolved=parsed.unresolved,
        max_duration_seconds=task["max_seconds"],
        started_at=task["started_at"],
        judge=task.get("judge"),
        robot_prompt=next((entry.get("prompt") for entry in task.get("plan", []) if entry["instruction"] == task["instruction"]), None),
        run_id=task["task_id"],
        scene_number=1,
        scene_label="Scene 1: live layout",
    )


def definition_for(task, first, number):
    """The scoring definition of task `number` (1-based) of a request's plan; `first` is task 1's."""
    plan = task.get("plan") or []
    if number <= 1 or number > len(plan) or plan[number - 1]["instruction"] == task["instruction"]:
        return first
    return task_definition({**task, "instruction": plan[number - 1]["instruction"]})


def hidden_evidence(task, number, record, folder):
    path = folder / f"attempt-{number}.evidence.json"
    attempt_id = f"{task['task_id']}-a{number}"
    try:
        proof = evaluation.Evidence.model_validate_json(path.read_text())
        proof.task_id = attempt_id
        if proof.execution_status == "CANCELLED" and not task.get("cancel_requested"):
            proof.execution_status = "TIMED_OUT"
    except (OSError, ValueError):
        status = "FAILED"
        errors = [record["error"]] if record.get("error") else ["The hidden attempt did not return simulator evidence."]
        proof = evaluation.Evidence(task_id=attempt_id, execution_status=status, errors=errors)
    return proof


def evaluate_run(task, definition, capture, result):
    videos = result.get("videos", [])
    clips = [name for name in videos if name != "grid"]
    live = capture.finish(
        task["task_id"],
        result.get("status", "FAILED"),
        [result["error"]] if result.get("error") else [],
        [name for name in clips if name == "attempt-1"],
        result.get("attempt_results", []),
    )
    live.run_videos = list(videos)
    evaluations.submit(definition, live)
    folder = outcome.VIDEOS / task["task_id"]
    for record in result.get("attempt_results", []):
        number = record["attempt"]
        if number == 1:
            continue
        proof = hidden_evidence(task, number, record, folder)
        proof.videos = [name for name in clips if name == f"attempt-{number}"]
        proof.run_videos = list(videos)
        own = definition_for(task, definition, record.get("task", 1))  # each run is scored against its own task
        attempt = own.model_copy(
            update={
                "task_id": proof.task_id,
                "attempt": number,
                "where": "hidden",
                "scene_id": record.get("env") or own.scene_id,
                "scene_number": record.get("scene") or number,
                "scene_label": f"Scene {record.get('scene') or number}: {record.get('env') or 'live layout'}",
            }
        )
        evaluations.submit(attempt, proof)
    if task.get("judge") != "claude":
        vision_checks.submit(task["task_id"], folder)
    run = evaluations.run_report(task["task_id"])["run"]
    return {
        "outcome": run["outcome"],
        "score": run["final_score"],
        "coverage": 100 * run["attempts_scored"] / run["attempts_total"],
        "batch_id": run["batch_id"],
        "run_id": run["run_id"],
        "report_url": f"/api/reports/runs/{run['run_id']}",
    }


def plan_goals(plan):
    """The goal of each task in a plan, or None for one the checker cannot judge.
    @param plan List of {"instruction", "attempts"}.
    @return One goal per entry: {"object", "target"} or None.
    """
    movable = list(sim._joint_by_body())
    goals = []
    for entry in plan:
        goal = parse_goal(entry["instruction"], movable)
        goals.append(goal if goal and goal["target"] in sim.managed() else None)
    return goals


def run_task(task, stop):
    global active_task, active_stop, active_clock, active_dream_log
    result = {}
    goals = plan_goals(task["plan"])
    goal = goals[0]
    if goal:
        sim.goal = {**goal, "frames": 0, "met": False, "stop": stop}
    clock = capacity.AttemptClock(
        task["max_seconds"], stop, lambda: task.update(time_limit_reached=True)
    )
    active_clock = clock
    sim.clock = clock
    active_dream_log = roundtrip_log.RoundTripLog(outcome.dream_log_path(outcome.VIDEOS / task["task_id"], 1), sim.env)
    client_log = dream_client_log.capture(outcome.client_log_path(outcome.VIDEOS / task["task_id"], 1))
    session = None
    definition = None
    capture = None
    try:
        definition = task_definition(task)
        capture = evaluation.EvidenceCapture(
            sim.env,
            sim.managed(),
            (dc.FRAMES - 1) * dc.FRAME_EVERY_STEPS + 1,
            outcome.VIDEOS / task["task_id"] / "attempt-1.frames",
        )
        sim.capture = capture
    except Exception as error:  # scoring must never stop the robot run
        result["evaluation_error"] = f"Evaluation could not start: {type(error).__name__}: {error}"
    try:
        envs = run_environments(task)
        save_scene(task, envs)
        session = outcome.Session(task, sim.env, goals, envs, sim.managed(), clock)
        try:
            dc.main(
                [f"--task={task['plan'][0].get('prompt') or task['instruction']}", "--headless", "--quiet", "--seconds", str(capacity.wall_limit(task["max_seconds"]))],
                shared_env=sim.env,
                stop_event=stop,
            )
        except capacity.TimeLimitReached:  # time ran out waiting for a seat: an ordinary timeout
            pass
        if goal and sim.goal["met"]:
            result["status"] = "COMPLETED"
            result["outcome"] = "SUCCESS"
            result["result_message"] = f"{goal['object'].replace('_', ' ')} is in the {goal['target']}."
        elif stop.is_set() and not task.get("time_limit_reached"):
            result["status"] = "CANCELLED"
            result["result_message"] = "ended by the user."
        elif goal:
            result["status"] = "TIMED_OUT"
            result["outcome"] = "FAILURE"
            result["result_message"] = f"{goal['object'].replace('_', ' ')} is not in the {goal['target']}."
        else:
            result["status"] = "TIMED_OUT"
            result["outcome"] = "NOT_EVALUATED"
            result["result_message"] = "this instruction cannot be checked automatically."
    except (Exception, SystemExit) as error:
        if task.get("cancel_requested"):
            result["status"] = "CANCELLED"
            result["result_message"] = "ended by the user."
        else:
            result["status"] = "FAILED"
            result["error"] = f"{type(error).__name__}: {error}"
    finally:
        sim.clock = None
        clock.close()
        result["robot_seconds"] = round(clock.moved(), 1)
        result["decision_seconds"] = round(clock.decided(), 1)
        result["wait_seconds"] = round(clock.waited(), 1)
        result["timer_seconds"] = round(clock.used(), 1)
        task["timer_seconds"] = result["timer_seconds"]
        if capture is not None:
            capture.decision_seconds = round(clock.decided(), 2)
            capture.wait_seconds = round(clock.waited(), 2)
        sim.goal = None
        sim.capture = None
        try:
            if session is not None:
                session.finish(result)
                result["run_seconds"] = round(session.progress(), 1)
        except Exception as error:  # a video or hidden-attempt problem must not leave the task stuck as running
            result["attempts_error"] = f"{type(error).__name__}: {error}"
        task["waiting_for_attempts"] = False
        if capture is not None:
            try:
                result["evaluation"] = evaluate_run(task, definition, capture, result)
            except Exception as error:  # a report problem must not leave the task stuck as running
                result["evaluation_error"] = f"Evaluation failed: {type(error).__name__}: {error}"
        finished_at = time.time()
        sim.publish()
        active_task = None
        active_stop = None
        active_clock = None
        active_dream_log.close()
        active_dream_log = None
        dream_client_log.release(client_log)
        task.update(result, finished_at=finished_at)
        if task.get("judge") == "claude":  # judged in the background while the robot is free for the next task
            judge_videos(task)


def judge_videos(task, only=None):
    """Send a finished run's videos to the video judge and save each verdict in its attempt's report."""
    try:
        ids = {record[0].attempt: record[0].task_id for record in evaluations.run_records(task["task_id"])}
        if only is not None:
            ids = {number: value for number, value in ids.items() if number in only}
        for task_id in ids.values():
            evaluations.attach_video_judge(task_id, {"verdict": "RUNNING"})

        def save(number, verdict):
            if number in ids:
                evaluations.attach_video_judge(ids[number], verdict)

        def finish(state):
            judged = {int(item["video"].split("-")[1]) for item in state.get("attempts", [])}
            for number, task_id in ids.items():
                if number not in judged:
                    evaluations.attach_video_judge(task_id, {"verdict": "SKIPPED", "reason": state.get("reason") or "this attempt has no saved video"})

        video_judge.submit(task, outcome.VIDEOS, on_attempt=save, on_done=finish, only=only)
        if task["video_judge"]["status"] == "SKIPPED":
            finish(task["video_judge"])
    except Exception as error:  # a judging problem must never affect the robot run or its score
        task["video_judge"] = {"status": "ERROR", "reason": f"{type(error).__name__}: {error}", "attempts": []}


def video_judgements(limit=5):
    """The newest finished tasks' video verdicts, so they stay on screen while the robot runs the next task."""
    judged = [t for t in tasks.values() if t.get("video_judge")]
    judged.sort(key=lambda t: t.get("finished_at") or 0, reverse=True)
    return [{"task_id": t["task_id"], "instruction": t["instruction"], **t["video_judge"]} for t in judged[:limit]]


def task_view(task):
    if task is None:
        return None
    finished = task["finished_at"] or time.time()
    clock = active_clock if task is active_task else None
    session = outcome.current if outcome.current is not None and outcome.current.task is task else None
    if clock is not None:
        elapsed = session.progress() if session is not None else clock.used()
    elif task is active_task and session is not None:
        elapsed = session.progress()
    elif task.get("run_seconds") is not None:
        elapsed = task["run_seconds"]
    elif task.get("timer_seconds") is not None:
        elapsed = task["timer_seconds"]
    else:
        elapsed = min(max(0.0, finished - task["started_at"]), task["max_seconds"])
    own = round(clock.used(), 1) if clock is not None else task.get("timer_seconds")
    return {**task, "elapsed_s": round(elapsed, 1), "run_timer_s": own, "waiting_for_capacity": bool(clock and clock.waiting)}


def live_phase():
    if active_task is None:
        return "idle"
    if active_task.get("finalizing"):
        return "saving"
    if active_task.get("holding"):
        return "holding"
    if active_task.get("waiting_for_attempts"):
        return "waiting_for_runs"
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


RESET_STOP_TIMEOUT_S = 30  # how long Reset waits for a running task and its parallel runs to stop
saved_layout = None  # the scene Reset puts back when the user saved one; None means the default scene


def capture_layout():
    """Everything Reset should put back: added and removed objects, object types, and every object's pose."""
    poses = {}
    for body in sim.managed():
        if mujoco.mj_name2id(sim.env.model, mujoco.mjtObj.mjOBJ_BODY, body) >= 0:
            data = sim.env.data.body(body)
            poses[body] = np.r_[data.xpos, data.xquat]
    return {
        "added": copy.deepcopy(sim.added),
        "removed": set(sim.removed),
        "type_overrides": dict(sim.type_overrides),
        "poses": poses,
    }


def restore_layout(layout):
    """Rebuild the scene as a saved layout describes it, with both arms back home."""
    sim.added.clear()
    sim.added.update(copy.deepcopy(layout["added"]))
    sim.removed.clear()
    sim.removed.update(layout["removed"])
    sim.type_overrides.clear()
    sim.type_overrides.update(layout["type_overrides"])
    sim.grab = None
    sim.selected = None
    sim._rebuild(keep_poses=False, poses=layout["poses"])


async def stop_running_task():
    """Stop the running task, live and parallel runs alike, and wait until it has ended.
    @return True when no task is running any more, False when it did not stop within RESET_STOP_TIMEOUT_S.
    """
    task, stop = active_task, active_stop
    if task is None:
        return True
    task["cancel_requested"] = True  # ends the parallel runs, also while the task waits for them
    if stop is not None:
        stop.set()  # ends the live run once its pending Dream-1 response returns
    deadline = time.monotonic() + RESET_STOP_TIMEOUT_S
    while active_task is task and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    return active_task is not task


@app.post("/api/scene/reset")
async def reset_scene():
    # Reset also works while a task runs: it stops the live run and every parallel run first.
    stopped_task = active_task is not None
    if not await stop_running_task():
        raise HTTPException(409, "The running task did not stop in time; press Reset scene again.")
    if saved_layout is not None:
        await change_scene(restore_layout, saved_layout, refresh=False)
    else:
        await change_scene(sim.reset, refresh=False)
    await on_sim(refresh_previews, False)  # every parallel run goes back to the reset scene, edits too
    outcome.clear_runs()  # the finished parallel runs' last pictures no longer match the reset scene
    return {"placed": feed.objects, "stopped_task": stopped_task, "layout": saved_layout is not None}


@app.post("/api/scene/layout")
async def save_layout():
    """Remember the current scene; Reset scene then puts it back instead of the default scene."""
    global saved_layout
    if active_task is not None:
        raise HTTPException(409, "The layout cannot be saved while a task is running.")
    saved_layout = await on_sim(capture_layout)
    return {"saved": True, "objects": len(saved_layout["poses"])}


@app.post("/api/environments")
async def prepare_parallel_environments(body: PrepareEnvironments):
    """Prepare and preview the parallel runs' environments; the run tabs show them until the next run."""
    if active_task is not None:
        raise HTTPException(409, "Environments cannot be prepared while a task is running.")
    if body.count == 0:
        outcome.clear_previews()
        return {"environments": []}
    shown = outcome.previews
    same = shown and shown["mode"] == body.mode and shown["seed"] == body.seed
    if same and shown["count"] == body.count:  # nothing changed (the page reconnected, say): keep every edit
        return {"environments": [e.describe() for e in shown["envs"]]}
    keep = {e.attempt: e for e in shown["envs"]} if same else None  # more or fewer runs: keep the ones there were
    envs = await on_sim(prepare_environments, body.count, body.mode, body.seed, True, keep)
    outcome.clear_runs()
    outcome.set_previews(envs, body.mode, body.seed)
    return {"environments": [e.describe() for e in envs]}


@app.get("/api/environments/{attempt}/objects")
def environment_objects(attempt: int):
    environment = preview_environment(attempt)
    return {"objects": environment.objects(), "selected": environment.selected}


@app.post("/api/environments/{attempt}/pick")
async def environment_pick(attempt: int, body: ViewPoint):
    if body.camera not in environments.VIEWS:
        raise HTTPException(404, "unknown camera")
    return await edit_environment(attempt, "pick", body.camera, body.u, body.v)


@app.post("/api/environments/{attempt}/drag")
async def environment_drag(attempt: int, body: ViewPoint):
    if body.camera not in environments.VIEWS:
        raise HTTPException(404, "unknown camera")
    await edit_environment(attempt, "drag", body.camera, body.u, body.v)
    return {"ok": True}


@app.post("/api/environments/{attempt}/drop")
async def environment_drop(attempt: int):
    return await edit_environment(attempt, "drop")


@app.post("/api/environments/{attempt}/select")
async def environment_select(attempt: int, body: SelectObject):
    await edit_environment(attempt, "select", body.object_id)
    return {"object_id": body.object_id}


@app.post("/api/environments/{attempt}/move")
async def environment_move(attempt: int, body: MoveObject):
    await edit_environment(attempt, "move", body.object_id, body.position.array())
    return {"object_id": body.object_id}


@app.post("/api/environments/{attempt}/rotate")
async def environment_rotate(attempt: int, body: RotateObject):
    await edit_environment(attempt, "rotate", body.object_id, body.yaw_deg)
    return {"object_id": body.object_id}


@app.post("/api/environments/{attempt}/shuffle")
async def environment_shuffle(attempt: int):
    await edit_environment(attempt, "shuffle")
    return {"environment": preview_environment(attempt).describe()}


@app.get("/api/scene-saves")
def scene_save_list():
    """Every run's saved starting scenes, newest first."""
    return {"saves": scene_saves.list_saves()}


@app.post("/api/scene-saves/{save_id}/load")
async def scene_save_load(save_id: str):
    """Put back the live scene and every parallel run exactly as they were when that run started."""
    if active_task is not None:
        raise HTTPException(409, "A saved scene cannot be loaded while a task is running.")

    def apply():
        try:
            return load_scene(save_id)
        except scene_saves.SaveError as error:
            raise HTTPException(error.status, str(error)) from error

    return {"loaded": await on_sim(apply)}


@app.delete("/api/scene/layout")
def forget_layout():
    """Forget the saved layout; Reset scene goes back to the default scene."""
    global saved_layout
    saved_layout = None
    return {"saved": False}


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
            raise HTTPException(413, "The STL is larger than 60 MB.")
        chunks.append(chunk)
    try:
        vertices, faces, original = await asyncio.to_thread(parse_stl, b"".join(chunks), size_cm / 100)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    object_id = await change_scene(sim.add_stl, filename, vertices, faces)
    return {"object_id": object_id, "original_triangles": original, "triangles": len(faces)}


async def view_changed():
    outcome.publish_view(sim.watched_attempt, sim.watched_view, sim.free_view_params())
    if active_task is None:
        await on_sim(sim.publish)


def render_final_scene(model_path, state_path):
    """A finished parallel run's final scene from the 3D camera; runs on view_pool."""
    key = str(state_path)
    if key not in final_scenes:
        model = mujoco.MjModel.from_binary_path(str(model_path))
        data = mujoco.MjData(model)
        with np.load(state_path) as state:
            data.qpos[:], data.qvel[:], data.ctrl[:] = state["qpos"], state["qvel"], state["ctrl"]
        mujoco.mj_forward(model, data)
        final_scenes[key] = (model, data)
        while len(final_scenes) > outcome.MAX_ATTEMPTS:
            final_scenes.popitem(last=False)
    final_scenes.move_to_end(key)
    return environments.render_previews(*final_scenes[key], (FREE_VIEW,))[FREE_VIEW]


async def parallel_frame(attempt, camera):
    """The newest picture of a parallel run, including its 3D view before, during and after the run."""
    if camera == FREE_VIEW:
        final = outcome.final_scene(attempt)
        if final is not None:
            key = ("final", str(final[1]), sim.view_version)
            if key not in final_frames:
                final_frames[key] = await asyncio.get_running_loop().run_in_executor(view_pool, render_final_scene, *final)
                while len(final_frames) > 8:
                    final_frames.popitem(last=False)
            return key, final_frames[key]
        if active_task is None and not outcome.has_workers():
            environment = next((e for e in (outcome.previews or {}).get("envs", []) if e.attempt == attempt), None)
            if environment is not None:
                key = ("preview-free", attempt, environment.version, sim.view_version)
                if environment.free_key != key:
                    await on_sim(environment.render_free)
                    environment.free_key = key
                return key, environment.previews.get(FREE_VIEW)
    return outcome.worker_frame(attempt, camera)


@app.post("/api/view/orbit")
async def view_orbit(body: OrbitView):
    sim.orbit(body.dx, body.dy)
    await view_changed()
    return {"ok": True}


@app.post("/api/view/zoom")
async def view_zoom(body: ZoomView):
    sim.zoom(body.delta)
    await view_changed()
    return {"ok": True}


@app.post("/api/view/pick")
async def view_pick(body: ViewPoint):
    if body.camera not in VIEWS:
        raise HTTPException(404, "unknown camera")
    return await change_scene(sim.pick, body.camera, body.u, body.v, refresh=False)


@app.post("/api/objects/select")
async def select_object(body: SelectObject):
    await change_scene(sim.select, body.object_id, refresh=False)
    return {"object_id": body.object_id}


@app.post("/api/objects/type")
async def set_object_type(body: ObjectType):
    await change_scene(sim.set_movable, body.object_id, body.movable)
    return {"object_id": body.object_id, "movable": body.movable}


@app.post("/api/view/drag")
async def view_drag(body: ViewPoint):
    if body.camera not in VIEWS:
        raise HTTPException(404, "unknown camera")
    await change_scene(sim.drag, body.camera, body.u, body.v, refresh=False)
    return {"ok": True}


@app.post("/api/view/drop")
async def view_drop():
    return await change_scene(sim.drop)


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
    if camera not in VIEWS:
        raise HTTPException(404, "unknown camera")
    jpeg = await asyncio.to_thread(feed.jpeg, camera)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/tasks/execute", status_code=202)
async def execute_task(body: ExecuteTask):
    global active_task, active_stop
    if body.tasks and body.instruction is not None:
        raise HTTPException(422, "Send either an instruction or a list of tasks, not both.")
    if body.tasks:
        plan = [{"instruction": t.instruction.strip(), "attempts": t.attempts} for t in body.tasks]
    else:
        plan = [{"instruction": (body.instruction or "").strip(), "attempts": body.attempts}]
    for number, entry in enumerate(plan, 1):
        if not entry["instruction"]:
            raise HTTPException(422, f"Task {number} is empty." if len(plan) > 1 else "The instruction cannot be empty.")
    total = sum(entry["attempts"] for entry in plan)
    if total > outcome.MAX_ATTEMPTS:
        raise HTTPException(422, f"{total} attempts in all; the limit is {outcome.MAX_ATTEMPTS}.")
    if active_task is not None:
        raise HTTPException(409, "A task is already running.")
    if body.judge == "claude" and not video_judge.has_credentials():
        raise HTTPException(409, "The Claude judge needs ANTHROPIC_API_KEY in the backend terminal.")
    goals = await on_sim(plan_goals, plan)
    if active_task is not None:
        raise HTTPException(409, "A task is already running.")
    for entry, goal in zip(plan, goals):
        entry["prompt"] = judges.task_prompt(entry["instruction"], goal, body.judge) if body.judge else entry["instruction"]
    if len(plan) > 1:  # a suite judges every task, so each must be one the checker understands
        unchecked = [n for n, goal in enumerate(goals, 1) if goal is None]
        if unchecked:
            lines = "; ".join(f"task {n}: {plan[n - 1]['instruction']!r}" for n in unchecked)
            raise HTTPException(422, f"Cannot check {lines}. Use the form: put <object> into/on the <bowl or plate>.")
    task = {
        "task_id": uuid.uuid4().hex[:12],
        "instruction": plan[0]["instruction"],
        "plan": plan,
        "max_seconds": body.max_seconds,
        "status": "RUNNING",
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "outcome": None,
        "result_message": None,
        "attempts": total,
        "videos": [],
        "batch_id": evaluations.current_batch,
        "evaluation": None,
        "evaluation_error": None,
        "environments": body.environments,
        "seed": body.seed if body.seed is not None else random.randrange(2**31),
        "judge": body.judge,
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
    tasks[task_id]["cancel_requested"] = True
    active_stop.set()
    return tasks[task_id]


def build_combined_video(run_id, path):
    """Create the combined video of an older run the first time it is opened."""
    with grid_lock:
        if path.is_file():
            return
        entries = []
        for attempt, result, label in evaluations.run_clips(run_id):
            clip = path.parent / f"attempt-{attempt}.mp4"
            if clip.is_file() and outcome.recording.readable(clip):
                entries.append((clip, label, result == "SUCCESS"))
        if len(entries) < 2:
            return
        temporary = path.with_suffix(".building.mp4")
        if outcome.recording.grid(entries, temporary):
            os.replace(temporary, path)


@app.post("/api/reports/runs/{run_id}/analyze-videos")
def analyze_run_videos(run_id: str):
    """Send a saved run's unjudged videos to the judge that run used."""
    if not evaluation.store.SAFE_ID.match(run_id) or not evaluations.has_run(run_id):
        raise HTTPException(404, "No report exists for this run.")
    records = evaluations.run_records(run_id)
    if saved_judge(records) == "offline":
        return {"queued": vision_checks.submit(run_id, outcome.VIDEOS / run_id, only_missing=True)}
    if any((evidence.video_judge or {}).get("verdict") == "RUNNING" for _, evidence, _ in records):
        return {"queued": 0}
    waiting = {
        item.attempt
        for item, evidence, _ in records
        if evidence.videos and (evidence.video_judge or {}).get("verdict") not in ("PASS", "FAIL", "UNSURE")
    }
    if not waiting:
        return {"queued": 0}
    saved = {
        "task_id": run_id,
        "instruction": records[0][0].instruction,
        "status": "COMPLETED",
        "finished_at": time.time(),
        "plan": [{"instruction": item.instruction, "prompt": item.robot_prompt, "attempts": 1} for item, _, _ in records],
        "attempt_results": [
            {"attempt": item.attempt, "task": index, "success": None if result.outcome == "NOT_EVALUABLE" else result.outcome == "SUCCESS"}
            for index, (item, _, result) in enumerate(records, 1)
        ],
    }
    judge_videos(saved, waiting)
    return {"queued": 0 if saved["video_judge"]["status"] == "SKIPPED" else len(waiting)}


def suggestion_rules():
    return {"x": SHUFFLE_X, "y": SHUFFLE_Y, "spacing": SHUFFLE_SPACING_M, "clearance": dict(FIXED_CLEARANCE_M)}


def saved_judge(records):
    """The judge a saved run used; runs from before the choice existed are read from their evidence."""
    named = next((record[0].judge for record in records if record[0].judge), None)
    return named or ("claude" if any(record[1].video_judge for record in records) else "offline")


@app.get("/api/judges")
def list_judges():
    offline_ready = os.environ.get("DREAM_VISION") != "off" and vision_checks.python.exists()
    claude_ready = video_judge.has_credentials()
    reasons = {
        "offline": None if offline_ready else "Runs and scoring still work. Install the offline vision model to analyse videos.",
        "claude": None if claude_ready else "Set ANTHROPIC_API_KEY in the backend terminal.",
    }
    ready = {"offline": offline_ready, "claude": claude_ready}
    return {"judges": [{"id": key, **value, "available": ready[key], "reason": reasons[key]} for key, value in judges.JUDGES.items()]}


@app.post("/api/judges/preview")
async def preview_judge_prompts(body: JudgePreview):
    if active_task is not None:
        raise HTTPException(409, "A task is running.")
    plan = [{"instruction": text.strip(), "attempts": 1} for text in body.instructions]
    goals = await on_sim(plan_goals, plan)
    return {"prompts": [judges.task_prompt(entry["instruction"], goal, body.judge) if entry["instruction"] else "" for entry, goal in zip(plan, goals)]}


def plan_suggestions(run_id, judge="offline"):
    """A finished run's one-change tests for a judge, or why they cannot start."""
    records = evaluations.run_records(run_id)
    reason = evaluation.refinement.blocked_reason(records)
    if reason:
        return {"tests": [], "untested": [], "problem": reason}
    return suggestions.build_tests(outcome.VIDEOS / run_id, records, suggestion_rules(), judge)


def run_suggestions(task, stop, tests):
    """Run a plan's tests in parallel and score each against the attempt it came from."""
    global active_task, active_stop
    result = {}
    session = None
    try:
        session = outcome.SuggestionSession(task, sim.env, tests)
        session.finish(result)
        result["run_seconds"] = round(session.progress(), 1)
        rows = result.get("attempt_results", [])
        if task.get("cancel_requested"):
            result["status"], result["result_message"] = "CANCELLED", "suggestion tests stopped, so no comparison was saved."
        elif rows and all(row.get("error") for row in rows):
            result["status"], result["error"] = "FAILED", rows[0]["error"]
        else:
            result["status"] = "COMPLETED"
            result["result_message"] = f"{result.get('attempts_succeeded', 0)} of {len(rows)} tests succeeded. The comparison is in the benchmark report."
            result["evaluation"] = evaluate_suggestions(task, tests, result)
    except Exception as error:
        result["status"], result["error"] = "FAILED", f"{type(error).__name__}: {error}"
        if session is not None and session.workers is not None:
            session.workers.stop()
    finally:
        result.setdefault("videos", [])
        task["waiting_for_attempts"] = False
        finished_at = time.time()
        active_task = None
        active_stop = None
        task.update(result, finished_at=finished_at)
        if result.get("status") == "COMPLETED" and task.get("judge") == "claude":
            judge_videos(task)


def evaluate_suggestions(task, tests, result):
    videos = result.get("videos", [])
    clips = [name for name in videos if name != "grid"]
    folder = outcome.VIDEOS / task["task_id"]
    sources = {record[0].attempt: record[0] for record in evaluations.run_records(task["refinement_of"])}
    for row in result.get("attempt_results", []):
        number = row["attempt"]
        test = tests[number - 2]
        proof = hidden_evidence(task, number, row, folder)
        proof.videos = [name for name in clips if name == f"attempt-{number}"]
        proof.run_videos = list(videos)
        definition = sources[test["source_attempt"]].model_copy(
            update={
                "task_id": proof.task_id,
                "run_id": task["task_id"],
                "attempt": number,
                "where": "hidden",
                "instruction": test["instruction"],
                "max_duration_seconds": test["max_seconds"],
                "started_at": task["started_at"],
                "scene_number": test["test"],
                "scene_label": f"Test {test['test']}: {test['change']}",
                "refinement_of": task["refinement_of"],
                "source_attempt": test["source_attempt"],
                "test_kind": test["kind"],
                "test_change": test["change"],
                "test_suggestion": test["suggestion"],
                "judge": task.get("judge"),
                "robot_prompt": test["prompt"],
            }
        )
        evaluations.submit(definition, proof)
    if task.get("judge") != "claude":
        vision_checks.submit(task["task_id"], folder)
    run = evaluations.run_report(task["task_id"])["run"]
    return {
        "outcome": run["outcome"],
        "score": run["final_score"],
        "coverage": 100 * run["attempts_scored"] / run["attempts_total"],
        "batch_id": run["batch_id"],
        "run_id": run["run_id"],
        "report_url": f"/api/reports/runs/{run['run_id']}",
    }


@app.get("/api/reports/runs/{run_id}/suggestion-tests")
async def suggestion_tests(run_id: str, judge: JudgeName = "offline"):
    if not evaluation.store.SAFE_ID.match(run_id) or not evaluations.has_run(run_id):
        raise HTTPException(404, "No report exists for this run.")
    results = evaluations.suggestion_results(run_id)
    source_judge = saved_judge(evaluations.run_records(run_id))
    if active_task is not None:
        mine = active_task.get("refinement_of") == run_id
        running = {"task_id": active_task["task_id"], "phase": live_phase()} if mine else None
        reason = "Suggestion tests are running." if mine else "Wait for the current run to finish."
        return {"ready": False, "reason": reason, "tests": active_task.get("tests", []) if mine else [], "untested": [], "cost_per_120s": SUGGESTION_COST_PER_120S, "running": running, "results": results, "source_judge": source_judge}
    plan = await asyncio.to_thread(plan_suggestions, run_id, judge)
    tests = [suggestions.public(test) for test in plan["tests"]]
    return {"ready": bool(tests), "reason": plan["problem"], "tests": tests, "untested": plan["untested"], "cost_per_120s": SUGGESTION_COST_PER_120S, "running": None, "results": results, "source_judge": source_judge}


@app.post("/api/reports/runs/{run_id}/suggestion-tests", status_code=202)
async def start_suggestion_tests(run_id: str, body: SuggestionChoice):
    global active_task, active_stop
    if not evaluation.store.SAFE_ID.match(run_id) or not evaluations.has_run(run_id):
        raise HTTPException(404, "No report exists for this run.")
    if active_task is not None:
        raise HTTPException(409, "A task is already running.")
    if body.judge == "claude" and not video_judge.has_credentials():
        raise HTTPException(409, "The Claude judge needs ANTHROPIC_API_KEY in the backend terminal.")
    plan = await asyncio.to_thread(plan_suggestions, run_id, body.judge)
    if plan["problem"] or not plan["tests"]:
        raise HTTPException(409, plan["problem"] or "There is nothing to test.")
    if active_task is not None:
        raise HTTPException(409, "A task is already running.")
    wanted = set(body.tests)
    if not wanted <= {test["test"] for test in plan["tests"]}:
        raise HTTPException(422, "One of the selected tests is no longer available. Reload the page and choose again.")
    tests = [test for test in plan["tests"] if test["test"] in wanted]
    source = evaluations.run_records(run_id)[0][0]
    public = [suggestions.public(test) for test in tests]
    task = {
        "task_id": uuid.uuid4().hex[:12],
        "instruction": f"Suggestion tests for {source.instruction}",
        "plan": [{"instruction": test["instruction"], "prompt": test["prompt"], "attempts": 1} for test in tests],
        "max_seconds": max(test["max_seconds"] for test in tests),
        "status": "RUNNING",
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "outcome": None,
        "result_message": None,
        "attempts": len(tests),
        "videos": [],
        "batch_id": source.batch_id,
        "evaluation": None,
        "evaluation_error": None,
        "environments": "same",
        "seed": 0,
        "suggestion_test": True,
        "judge": body.judge,
        "refinement_of": run_id,
        "tests": public,
    }
    tasks[task["task_id"]] = task
    active_task = task
    active_stop = threading.Event()
    pool.submit(run_suggestions, task, active_stop, tests)
    return task


@app.get("/api/tasks/{task_id}/video/{clip}")
def task_video(task_id: str, clip: str):
    path = outcome.VIDEOS / task_id / f"{clip}.mp4"
    known = task_id in tasks or evaluations.has_run(task_id)
    if known and clip == "grid" and not path.is_file():
        build_combined_video(task_id, path)
    if not known or not outcome.CLIP.match(clip) or not path.is_file():
        raise HTTPException(404, "no such video")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/tasks/{task_id}/dream-log/{attempt}")
def task_dream_log(task_id: str, attempt: int, full: bool = False, after: int = 0):
    """Run `attempt`'s Dream-1 log: one entry per request (timing, rows, errors) and a summary.

    `after` leaves out the entries numbered up to it, so a page following a running run fetches only new ones;
    `full` adds each request's state and returned chunk.
    """
    known = task_id in tasks or evaluations.has_run(task_id)
    if not known or not 1 <= attempt <= outcome.MAX_ATTEMPTS:
        raise HTTPException(404, "no such run")
    log = roundtrip_log.read(outcome.dream_log_path(outcome.VIDEOS / task_id, attempt), full=full)
    log["entries"] = [entry for entry in log["entries"] if entry.get("n", 0) > after]
    return log


@app.get("/api/tasks/{task_id}/dream-client-log/{attempt}")
def task_dream_client_log(task_id: str, attempt: int, limit: int = Query(300, ge=1, le=2000)):
    """Run `attempt`'s own dream client log: its sessions, pacing switches and warnings, no other run's."""
    known = task_id in tasks or evaluations.has_run(task_id)
    if not known or not 1 <= attempt <= outcome.MAX_ATTEMPTS:
        raise HTTPException(404, "no such run")
    return dream_client_log.read(file=outcome.client_log_path(outcome.VIDEOS / task_id, attempt), limit=limit)


@app.get("/api/tasks/{task_id}/frames/{attempt}/{camera}")
def task_frame(task_id: str, attempt: int, camera: str):
    path = outcome.VIDEOS / task_id / f"attempt-{attempt}.frames" / f"{camera}.jpg"
    if not evaluations.has_run(task_id) or not 1 <= attempt <= outcome.MAX_ATTEMPTS or camera not in CAMERAS or not path.is_file():
        raise HTTPException(404, "no such frame")
    return FileResponse(path, media_type="image/jpeg")


@app.websocket("/ws/live")
async def live(socket: WebSocket):
    if socket.headers.get("origin") not in ALLOWED_ORIGINS:
        await socket.close(code=1008)
        return
    await socket.accept()
    selected = {"camera": "top", "attempt": 1}

    async def receive():
        while True:
            message = await socket.receive_json()
            if isinstance(message, dict) and message.get("attempt") in range(1, outcome.MAX_ATTEMPTS + 1):
                selected["attempt"] = message["attempt"]  # 1 is the live simulation; more are hidden workers
                sim.watched_attempt = message["attempt"]
                outcome.publish_view(sim.watched_attempt, sim.watched_view, sim.free_view_params())
            if isinstance(message, dict) and message.get("camera") in VIEWS:
                selected["camera"] = message["camera"]
                sim.watched_view = message["camera"]
                outcome.publish_view(sim.watched_attempt, sim.watched_view, sim.free_view_params())
                if active_task is None:
                    await on_sim(sim.publish)

    receiver = asyncio.create_task(receive())
    sent = (None, None, None)
    latest = None
    try:
        while not receiver.done():
            attempt = selected["attempt"]
            seq, frame = await parallel_frame(attempt, selected["camera"]) if attempt > 1 else (None, None)
            current = (seq if attempt > 1 else feed.sequence, selected["camera"], attempt)
            task = active_task or latest
            latest = task
            await socket.send_json(
                {
                    "type": "state",
                    "sequence": feed.sequence,
                    "camera": selected["camera"],
                    "attempt": attempt,
                    "workers": outcome.attempt_states(),
                    "layout_saved": saved_layout is not None,
                    "scene_id": sim.scene_id,
                    "selected": sim.selected,
                    "phase": live_phase(),
                    "task": task_view(task),
                    "video_judgements": video_judgements(),
                    "objects": feed.objects,
                    **outcome.viewed_objects(attempt),
                    "robot": feed.robot,
                    "contacts": feed.contacts,
                }
            )
            if current != sent and attempt > 1:
                if frame is not None:  # a hidden worker's newest picture; nothing is sent before its first frame
                    await socket.send_bytes(frame)
                    sent = current
            elif current != sent:
                await socket.send_bytes(await asyncio.to_thread(feed.jpeg, current[1]))
                sent = current
            await asyncio.sleep(STREAM_PERIOD_S)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()


WEBSITE = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if PUBLIC_ORIGIN and WEBSITE.is_dir():  # hosted: one address serves the built website and the API
    app.mount("/", StaticFiles(directory=WEBSITE, html=True), name="website")

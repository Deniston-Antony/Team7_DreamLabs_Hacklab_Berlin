import asyncio
import copy
import math
import os
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
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
import yam_env  # noqa: E402
import outcome  # noqa: E402
import evaluation  # noqa: E402

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
MAX_TASK_SECONDS = 200
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
        self.view_lock = threading.Lock()
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
        if self.capture is not None:
            self.capture.on_step()
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

    def orbit(self, dx, dy):
        with self.view_lock:
            self.free_camera.azimuth = (self.free_camera.azimuth - dx * ORBIT_DEG_PER_PIXEL) % 360
            self.free_camera.elevation = float(np.clip(self.free_camera.elevation - dy * ORBIT_DEG_PER_PIXEL, -85, -5))

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

    def _rebuild(self, keep_poses=True, poses=None):
        saved = dict(poses or {})  # poses to put objects back to, such as a saved layout's
        if keep_poses:
            for body in self.managed():
                if mujoco.mj_name2id(self.env.model, mujoco.mjtObj.mjOBJ_BODY, body) >= 0:
                    data = self.env.data.body(body)
                    saved[body] = np.r_[data.xpos, data.xquat]
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


class SubTask(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    attempts: int = Field(default=1, ge=1, le=outcome.MAX_ATTEMPTS)


class ExecuteTask(BaseModel):
    # One task as `instruction` (with `attempts`), or several at once as `tasks`, each with its own attempts.
    instruction: str | None = Field(default=None, min_length=1, max_length=500)
    max_seconds: int = Field(default=MAX_TASK_SECONDS, ge=5, le=MAX_TASK_SECONDS)
    attempts: int = Field(default=1, ge=1, le=outcome.MAX_ATTEMPTS)
    tasks: list[SubTask] | None = Field(default=None, min_length=1, max_length=outcome.MAX_ATTEMPTS)


# MuJoCo rendering contexts are thread-bound, so all simulator work stays on one thread.
pool = ThreadPoolExecutor(max_workers=1)
feed = LiveFeed()
sim = pool.submit(SimAdapter, feed).result()
tasks = {}
active_task = None
active_stop = None
app = FastAPI(title="Dream Labs integration test")
evaluations = evaluation.EvaluationStore(
    Path(os.environ.get("DREAM_EVALUATION_DATA", Path(__file__).resolve().parent / "evaluation_data"))
)
app.include_router(evaluation.create_router(evaluations))
_open_policy = dc.open_policy


def open_counted_policy(config, key):
    client = _open_policy(config, key)
    if sim.capture is not None:
        sim.capture.watch_model_calls(client)
    return client


dc.open_policy = open_counted_policy


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
        run_id=task["task_id"],
    )


def hidden_evidence(task, number, record, folder):
    path = folder / f"attempt-{number}.evidence.json"
    attempt_id = f"{task['task_id']}-a{number}"
    try:
        proof = evaluation.Evidence.model_validate_json(path.read_text())
        proof.task_id = attempt_id
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
        attempt = definition.model_copy(update={"task_id": proof.task_id, "attempt": number, "where": "hidden"})
        evaluations.submit(attempt, proof)
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
    global active_task, active_stop
    result = {}
    goals = plan_goals(task["plan"])
    goal = goals[0]
    if goal:
        sim.goal = {**goal, "frames": 0, "met": False, "stop": stop}
    session = None
    definition = None
    capture = None
    try:
        definition = task_definition(task)
        capture = evaluation.EvidenceCapture(
            sim.env, sim.managed(), (dc.FRAMES - 1) * dc.FRAME_EVERY_STEPS + 1
        )
        sim.capture = capture
    except Exception as error:  # scoring must never stop the robot run
        result["evaluation_error"] = f"Evaluation could not start: {type(error).__name__}: {error}"
    try:
        session = outcome.Session(task, sim.env, goals, sim.managed())
        dc.main(
            [f"--task={task['instruction']}", "--headless", "--quiet", "--seconds", str(task["max_seconds"])],
            shared_env=sim.env,
            stop_event=stop,
        )
        if goal and sim.goal["met"]:
            result["status"] = "COMPLETED"
            result["outcome"] = "SUCCESS"
            result["result_message"] = f"{goal['object'].replace('_', ' ')} is in the {goal['target']}."
        elif stop.is_set():
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
        result["status"] = "FAILED"
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        sim.goal = None
        sim.capture = None
        try:
            if session is not None:
                session.finish(result)
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
        task.update(result, finished_at=finished_at)


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
        await change_scene(restore_layout, saved_layout)
    else:
        await change_scene(sim.reset)
    return {"placed": feed.objects, "stopped_task": stopped_task, "layout": saved_layout is not None}


@app.post("/api/scene/layout")
async def save_layout():
    """Remember the current scene; Reset scene then puts it back instead of the default scene."""
    global saved_layout
    if active_task is not None:
        raise HTTPException(409, "The layout cannot be saved while a task is running.")
    saved_layout = await on_sim(capture_layout)
    return {"saved": True, "objects": len(saved_layout["poses"])}


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


@app.post("/api/view/orbit")
async def view_orbit(body: OrbitView):
    sim.orbit(body.dx, body.dy)
    if active_task is None:
        await on_sim(sim.publish)
    return {"ok": True}


@app.post("/api/view/zoom")
async def view_zoom(body: ZoomView):
    sim.zoom(body.delta)
    if active_task is None:
        await on_sim(sim.publish)
    return {"ok": True}


@app.post("/api/view/pick")
async def view_pick(body: ViewPoint):
    if body.camera not in VIEWS:
        raise HTTPException(404, "unknown camera")
    return await change_scene(sim.pick, body.camera, body.u, body.v)


@app.post("/api/objects/select")
async def select_object(body: SelectObject):
    await change_scene(sim.select, body.object_id)
    return {"object_id": body.object_id}


@app.post("/api/objects/type")
async def set_object_type(body: ObjectType):
    await change_scene(sim.set_movable, body.object_id, body.movable)
    return {"object_id": body.object_id, "movable": body.movable}


@app.post("/api/view/drag")
async def view_drag(body: ViewPoint):
    if body.camera not in VIEWS:
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
    if len(plan) > 1:  # a suite judges every task, so each must be one the checker understands
        unchecked = [n for n, goal in enumerate(await on_sim(plan_goals, plan), 1) if goal is None]
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


@app.get("/api/tasks/{task_id}/video/{clip}")
def task_video(task_id: str, clip: str):
    path = outcome.VIDEOS / task_id / f"{clip}.mp4"
    known = task_id in tasks or evaluations.has_run(task_id)
    if not known or not outcome.CLIP.match(clip) or not path.is_file():
        raise HTTPException(404, "no such video")
    return FileResponse(path, media_type="video/mp4")


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
            if isinstance(message, dict) and message.get("camera") in VIEWS:
                selected["camera"] = message["camera"]
                sim.watched_view = message["camera"]
                if active_task is None:
                    await on_sim(sim.publish)

    receiver = asyncio.create_task(receive())
    sent = (None, None, None)
    latest = None
    try:
        while not receiver.done():
            attempt = selected["attempt"]
            seq, frame = outcome.worker_frame(attempt, selected["camera"]) if attempt > 1 else (None, None)
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
                    "objects": feed.objects,
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

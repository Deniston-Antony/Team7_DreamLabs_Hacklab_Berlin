"""Varied starting scenes for parallel runs: shuffled object positions, and other scene styles.

Each parallel run can start from its own environment instead of an exact copy of the live scene. An environment is
the live scene's compiled model (or the same objects compiled in another scene style) plus a starting state in
which the movable objects are shuffled to a new collision-free layout. The live run is never changed.
A seed makes every environment reproducible: the same scene, mode and seed give the same layouts and styles.

Before a run, each environment can be edited on its own as the live scene is: pick and drag an object in the overhead
view, move or rotate it, or shuffle it again. Edits change only that environment's starting state, never the live
scene, and an edited environment keeps its layout when the live scene changes. All of this runs on the backend's
simulator thread.
"""

import io
import math
from collections import OrderedDict

import mujoco
import numpy as np
from PIL import Image

MODES = ("same", "shuffle", "shuffle_style")  # copy of the live scene; shuffled positions; shuffled + other styles
CAMERAS = ("top", "left", "right")
RENDER_WIDTH, RENDER_HEIGHT, RENDER_CROP_TOP = 640, 480, 60  # as yam_env.YamEnv.images renders
SETTLE_STEPS = 20  # control steps (25 physics steps each) to let shuffled objects come to rest
DROP_SETTLE_STEPS = 4  # control steps after a drop, as the live scene does
PHYSICS_PER_CONTROL = 25
JPEG_QUALITY = 70
MAX_RENDERERS = 6  # cached offscreen renderers, one per compiled model; the oldest is closed first
_renderers = OrderedDict()  # id(model) -> (model, mujoco.Renderer)


class EditError(Exception):
    """An edit that cannot be done, such as moving a fixed object; `status` is the HTTP status to answer with."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def camera_ray(model, data, camera, u, v):
    """The ray through pixel (u, v) of a fixed camera's cropped image, as yam_env renders it.
    @return (origin, unit direction).
    """
    camera_id = model.camera(camera).id
    focal = (RENDER_HEIGHT / 2) / math.tan(math.radians(model.cam_fovy[camera_id]) / 2)
    local = np.array([(u - RENDER_WIDTH / 2) / focal, -(v + RENDER_CROP_TOP - RENDER_HEIGHT / 2) / focal, -1.0])
    direction = data.cam_xmat[camera_id].reshape(3, 3) @ local
    return data.cam_xpos[camera_id].copy(), direction / np.linalg.norm(direction)


def at_height(origin, direction, z):
    """Where a ray crosses height z, or None when it points too flat to hit it."""
    if direction[2] > -0.05:
        return None
    return origin + direction * ((z - origin[2]) / direction[2])


class Environment:
    """One parallel run's starting scene."""

    def __init__(self, attempt, model, data, style_id, style_name, seed, shuffled, rules=None):
        """Keep a model and its own copy of the starting state.
        @param attempt The run number this environment is for (2 or more; 1 is the live run).
        @param model The compiled MjModel (the live one, or the same objects in another style).
        @param data An MjData holding the starting state; its qpos, qvel and ctrl are copied.
        @param style_id The scene style's id, such as "kitchen".
        @param style_name Its display name, such as "Kitchen style".
        @param seed The seed the layout came from.
        @param shuffled Whether the objects were moved to a new layout.
        @param rules What editing needs to know about the scene, all optional: `movable` and `managed` (object names),
            `clearance` (fixed object -> metres kept free around it), `shuffle_x`, `shuffle_y`, `spacing`, `drag_x`,
            `drag_y` (ranges in metres) and `table_z`.
        """
        self.attempt, self.model, self.style_id, self.style_name = attempt, model, style_id, style_name
        self.seed, self.shuffled, self.rules = seed, shuffled, rules or {}
        self.data = mujoco.MjData(model)
        self.data.qpos[:], self.data.qvel[:], self.data.ctrl[:] = data.qpos, data.qvel, data.ctrl
        mujoco.mj_forward(model, self.data)
        self.previews = {}  # camera name -> JPEG of the starting scene
        self.edited = False  # True once the user changed this environment by hand
        self.version = 0  # bumped on every change, so the page knows to fetch a new picture
        self.selected = None
        self.grab = None
        self.snapshot = self.objects()  # the object table, read by the page's thread while edits run on the sim's

    @property
    def qpos(self):
        return self.data.qpos

    @property
    def qvel(self):
        return self.data.qvel

    @property
    def ctrl(self):
        return self.data.ctrl

    @property
    def label(self):
        """A short description for the run tabs: "Kitchen style, shuffled"."""
        if self.edited:
            return f"{self.style_name}, edited"
        if not self.shuffled and not self.rules.get("restyled", True):
            return "Copy of the live scene"
        return f"{self.style_name}{', shuffled' if self.shuffled else ''}"

    def describe(self):
        """The fields the page shows."""
        return {
            "attempt": self.attempt,
            "style": self.style_id,
            "style_name": self.style_name,
            "shuffled": self.shuffled,
            "seed": self.seed,
            "edited": self.edited,
            "label": self.label,
        }

    def objects(self):
        """The scene objects as the live object table lists them."""
        movable = set(self.rules.get("movable", ()))
        return [
            {
                "object_id": name,
                "movable": name in movable,
                "position": self.data.body(name).xpos.round(4).tolist(),
                "orientation_wxyz": self.data.body(name).xquat.round(4).tolist(),
                "capture_source": "simulator",
            }
            for name in self.rules.get("managed", ())
            if mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
        ]

    def _joint(self, name):
        """The (qpos, dof) addresses of a movable object; EditError for a fixed or unknown one."""
        if name not in self.rules.get("managed", ()):
            raise EditError(f"no object named {name!r}", 404)
        address = _free_joint(self.model, name) if name in self.rules.get("movable", ()) else None
        if address is None:
            raise EditError(f"{name} is fixed. Set its type to Movable first.")
        return address

    def _set_pose(self, name, position=None, quat=None):
        qpos_at, dof_at = self._joint(name)
        if position is not None:
            self.data.qpos[qpos_at : qpos_at + 3] = position
        if quat is not None:
            self.data.qpos[qpos_at + 3 : qpos_at + 7] = quat
        self.data.qvel[dof_at : dof_at + 6] = 0
        mujoco.mj_forward(self.model, self.data)

    def _settle(self, steps):
        for _ in range(steps):  # the arms hold their position through data.ctrl
            mujoco.mj_step(self.model, self.data, nstep=PHYSICS_PER_CONTROL)
        mujoco.mj_forward(self.model, self.data)

    def _changed(self, cameras=CAMERAS):
        """Mark the environment as edited by hand and render the given cameras again."""
        self.edited = True
        self.previews.update(render_previews(self.model, self.data, cameras))
        self.snapshot = self.objects()
        self.version += 1

    def pick(self, camera, u, v):
        """Select the object under pixel (u, v) of a camera and, when it is movable, start dragging it.
        @return {"object_id", "movable"}; object_id is None when nothing was hit.
        """
        managed = self.rules.get("managed", ())
        origin, direction = camera_ray(self.model, self.data, camera, u, v)
        hit = np.array([-1], dtype=np.int32)
        mujoco.mj_ray(self.model, self.data, origin, direction, None, 1, -1, hit)
        name = None
        if hit[0] >= 0:
            root = self.model.body_rootid[self.model.geom_bodyid[hit[0]]]
            name = self.model.body(root).name if self.model.body(root).name in managed else None
            if name is None and root == 0:  # the table: take an object lying right there, as the live view does
                point = at_height(origin, direction, self.rules.get("table_z", 0.76))
                present = [n for n in managed if mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, n) >= 0]
                if point is not None and present:
                    nearest = min(present, key=lambda n: np.linalg.norm(self.data.body(n).xpos[:2] - point[:2]))
                    if np.linalg.norm(self.data.body(nearest).xpos[:2] - point[:2]) < 0.035:
                        name = nearest
        self.grab, self.selected = None, name
        if name is None:
            return {"object_id": None, "movable": False}
        if name not in self.rules.get("movable", ()):
            return {"object_id": name, "movable": False}
        qpos_at = self._joint(name)[0]
        z = float(self.data.qpos[qpos_at + 2])
        point = at_height(origin, direction, z)
        if point is None:
            raise EditError("Raise the camera angle to drag this object.")
        self.grab = {
            "object_id": name,
            "offset": self.data.qpos[qpos_at : qpos_at + 2] - point[:2],
            "start": self.data.qpos[qpos_at : qpos_at + 7].copy(),
            "z": z,
            "camera": camera,
        }
        return {"object_id": name, "movable": True}

    def drag(self, camera, u, v):
        """Move the grabbed object under pixel (u, v), keeping its height."""
        if self.grab is None:
            raise EditError("No object is being dragged.")
        origin, direction = camera_ray(self.model, self.data, camera, u, v)
        point = at_height(origin, direction, self.grab["z"])
        if point is None:
            return
        x, y = point[:2] + self.grab["offset"]
        x_range, y_range = self.rules.get("drag_x", (-0.35, 0.35)), self.rules.get("drag_y", (-0.20, 0.25))
        self._set_pose(self.grab["object_id"], [float(np.clip(x, *x_range)), float(np.clip(y, *y_range)), self.grab["z"]])
        self._changed((camera,))  # only the camera being dragged on, so dragging stays smooth

    def drop(self):
        """Let go of the grabbed object; it goes back when it would overlap another object.
        @return {"object_id", "restored"}.
        """
        if self.grab is None:
            return {"object_id": None, "restored": False}
        grab, self.grab = self.grab, None
        name = grab["object_id"]
        restored = not _clear(self.model, self.data, {name})
        if restored:
            self._set_pose(name, grab["start"][:3], grab["start"][3:])
        self._settle(DROP_SETTLE_STEPS)
        self._changed()
        return {"object_id": name, "restored": restored}

    def move(self, name, position):
        """Put an object at a position, in metres."""
        self._set_pose(name, position)
        self.selected = name
        self._changed()

    def rotate(self, name, yaw_deg):
        """Turn an object about the vertical axis."""
        qpos_at = self._joint(name)[0]
        half = math.radians(yaw_deg) / 2
        rotated = np.zeros(4)
        mujoco.mju_mulQuat(rotated, np.array([math.cos(half), 0, 0, math.sin(half)]), self.data.qpos[qpos_at + 3 : qpos_at + 7].copy())
        mujoco.mju_normalize4(rotated)
        self._set_pose(name, quat=rotated)
        self.selected = name
        self._changed()

    def select(self, name):
        """Select an object without moving it (None clears the selection)."""
        if name is not None and name not in self.rules.get("managed", ()):
            raise EditError(f"no object named {name!r}", 404)
        self.selected = name

    def shuffle(self, rng=None):
        """Move this environment's movable objects to a new random layout."""
        movable = [n for n in self.rules.get("movable", ()) if _free_joint(self.model, n) is not None]
        if not movable:
            raise EditError("There are no movable objects to shuffle.")
        clearance = self.rules.get("clearance", {})
        fixed = [
            (self.data.body(n).xpos[:2].copy(), clearance.get(n, 0.09))
            for n in self.rules.get("managed", ())
            if n not in movable and mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, n) >= 0
        ]
        found = shuffle_layout(
            self.model,
            self.data,
            movable,
            fixed,
            rng or np.random.default_rng(),
            self.rules.get("shuffle_x", (-0.27, 0.27)),
            self.rules.get("shuffle_y", (-0.12, 0.17)),
            self.rules.get("spacing", 0.085),
        )
        if not found:
            raise EditError("No free layout was found; try again.")
        self.shuffled, self.grab = True, None
        self._changed()

    def save(self, folder):
        """Write the model and starting state where a worker can load them (parallel_bench --scene-model/-state).
        @param folder Folder to write into.
        @return (model path, state path).
        """
        model_path = folder / f"env-{self.attempt}.mjb"
        state_path = folder / f"env-{self.attempt}.npz"
        mujoco.mj_saveModel(self.model, str(model_path))
        np.savez(state_path, qpos=self.qpos, qvel=self.qvel, ctrl=self.ctrl)
        return model_path, state_path


def _free_joint(model, body):
    """The free joint's qpos and dof addresses of a body, or None when it has no free joint."""
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if body_id < 0 or model.body_jntnum[body_id] != 1:
        return None
    joint = model.body_jntadr[body_id]
    if model.jnt_type[joint] != mujoco.mjtJoint.mjJNT_FREE:
        return None
    return model.jnt_qposadr[joint], model.jnt_dofadr[joint]


def _clear(model, data, movable):
    """Whether no movable object touches anything but the world (the table): the backend's shuffle rule."""
    mujoco.mj_forward(model, data)
    for contact in data.contact[: data.ncon]:
        names = {model.body(model.geom_bodyid[contact.geom1]).name, model.body(model.geom_bodyid[contact.geom2]).name}
        if names & movable and "world" not in names:
            return False
    return True


def shuffle_layout(model, data, movable, fixed, rng, x_range, y_range, spacing):
    """Move the movable objects to a random collision-free layout, as the backend's Shuffle button does.
    @param model The MjModel.
    @param data The MjData to change; restored when no layout is found.
    @param movable Names of the bodies to move (each has a free joint).
    @param fixed (xy, clearance) of each object that stays where it is, such as the bowl.
    @param rng A numpy Generator.
    @param x_range, y_range Where objects may be placed, in metres.
    @param spacing The least distance between two placed objects, in metres.
    @return True when a layout was found and settled, False when the scene was left as it was.
    """
    joints = {name: _free_joint(model, name) for name in movable}
    joints = {name: address for name, address in joints.items() if address is not None}
    if not joints:
        return False
    saved_qpos, saved_qvel = data.qpos.copy(), data.qvel.copy()
    for _ in range(200):
        placed = []
        for _ in joints:
            for _ in range(80):
                point = np.array([rng.uniform(*x_range), rng.uniform(*y_range)])
                if all(np.linalg.norm(point - c) >= r for c, r in fixed) and all(
                    np.linalg.norm(point - other) >= spacing for other in placed
                ):
                    placed.append(point)
                    break
        if len(placed) != len(joints):
            continue
        for (name, (qpos_at, dof_at)), point in zip(joints.items(), placed):
            half = rng.uniform(0, np.pi)
            data.qpos[qpos_at : qpos_at + 3] = [point[0], point[1], model.qpos0[qpos_at + 2]]
            data.qpos[qpos_at + 3 : qpos_at + 7] = [np.cos(half), 0, 0, np.sin(half)]
            data.qvel[dof_at : dof_at + 6] = 0
        if _clear(model, data, set(joints)):
            for _ in range(SETTLE_STEPS):  # the arms hold their position through data.ctrl
                mujoco.mj_step(model, data, nstep=25)
            mujoco.mj_forward(model, data)
            return True
    data.qpos[:], data.qvel[:] = saved_qpos, saved_qvel
    mujoco.mj_forward(model, data)
    return False


def _renderer(model):
    """A cached offscreen renderer for a model; creating one per picture would make dragging slow."""
    key = id(model)
    if key in _renderers and _renderers[key][0] is model:
        _renderers.move_to_end(key)
        return _renderers[key][1]
    if key in _renderers:  # a model that was freed and whose id was reused
        _renderers.pop(key)[1].close()
    while len(_renderers) >= MAX_RENDERERS:
        _renderers.popitem(last=False)[1][1].close()
    renderer = mujoco.Renderer(model, height=RENDER_HEIGHT, width=RENDER_WIDTH)
    _renderers[key] = (model, renderer)
    return renderer


def render_previews(model, data, cameras=CAMERAS):
    """JPEGs of cameras for a state, cropped as the live view is.
    @param cameras Which cameras; all three by default.
    @return Camera name -> JPEG bytes.
    """
    renderer = _renderer(model)
    previews = {}
    for camera in cameras:
        renderer.update_scene(data, camera=camera)
        image = renderer.render()[RENDER_CROP_TOP : RENDER_HEIGHT - RENDER_CROP_TOP]
        buffer = io.BytesIO()
        Image.fromarray(image).save(buffer, format="JPEG", quality=JPEG_QUALITY)
        previews[camera] = buffer.getvalue()
    return previews

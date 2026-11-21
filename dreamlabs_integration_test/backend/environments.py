"""Varied starting scenes for parallel runs: shuffled object positions, and other scene styles.

Each parallel run can start from its own environment instead of an exact copy of the live scene. An environment is
the live scene's compiled model (or the same objects compiled in another scene style) plus a starting state in
which the movable objects are shuffled to a new collision-free layout. The live run is never changed.
A seed makes every environment reproducible: the same scene, mode and seed give the same layouts and styles.
"""

import io

import mujoco
import numpy as np
from PIL import Image

MODES = ("same", "shuffle", "shuffle_style")  # copy of the live scene; shuffled positions; shuffled + other styles
CAMERAS = ("top", "left", "right")
RENDER_WIDTH, RENDER_HEIGHT, RENDER_CROP_TOP = 640, 480, 60  # as yam_env.YamEnv.images renders
SETTLE_STEPS = 20  # control steps (25 physics steps each) to let shuffled objects come to rest
JPEG_QUALITY = 70


class Environment:
    """One parallel run's starting scene."""

    def __init__(self, attempt, model, data, style_id, style_name, seed, shuffled):
        """Keep a model and a copy of its starting state.
        @param attempt The run number this environment is for (2 or more; 1 is the live run).
        @param model The compiled MjModel (the live one, or the same objects in another style).
        @param data An MjData holding the starting state; its qpos, qvel and ctrl are copied.
        @param style_id The scene style's id, such as "kitchen".
        @param style_name Its display name, such as "Kitchen style".
        @param seed The seed the layout came from.
        @param shuffled Whether the objects were moved to a new layout.
        """
        self.attempt, self.model, self.style_id, self.style_name = attempt, model, style_id, style_name
        self.seed, self.shuffled = seed, shuffled
        self.qpos, self.qvel, self.ctrl = data.qpos.copy(), data.qvel.copy(), data.ctrl.copy()
        self.previews = {}  # camera name -> JPEG of the starting scene

    @property
    def label(self):
        """A short description for the run tabs: "Kitchen style, shuffled"."""
        return f"{self.style_name}{', shuffled' if self.shuffled else ''}"

    def describe(self):
        """The fields the page shows."""
        return {
            "attempt": self.attempt,
            "style": self.style_id,
            "style_name": self.style_name,
            "shuffled": self.shuffled,
            "seed": self.seed,
            "label": self.label,
        }

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


def render_previews(model, data):
    """JPEGs of the three cameras for a starting state, cropped as the live view is.
    @return Camera name -> JPEG bytes.
    """
    renderer = mujoco.Renderer(model, height=RENDER_HEIGHT, width=RENDER_WIDTH)
    try:
        previews = {}
        for camera in CAMERAS:
            renderer.update_scene(data, camera=camera)
            image = renderer.render()[RENDER_CROP_TOP : RENDER_HEIGHT - RENDER_CROP_TOP]
            buffer = io.BytesIO()
            Image.fromarray(image).save(buffer, format="JPEG", quality=JPEG_QUALITY)
            previews[camera] = buffer.getvalue()
        return previews
    finally:
        renderer.close()

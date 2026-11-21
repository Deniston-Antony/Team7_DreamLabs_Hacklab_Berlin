import time

import mujoco
import numpy as np

from .models import ContactEvent, Environment, Evidence, ObjectState, Snapshot

SETTLE_STEPS = 250
TRAJECTORY_EVERY_STEPS = 8
CONTACT_GAP_S = 0.2
CONTROL_STEP_S = 0.05
CORNERS = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=float)


class EvidenceCapture:
    def __init__(self, env, object_ids, startup_steps):
        self.env = env
        self.object_ids = list(object_ids)
        self.startup_steps = startup_steps
        self.steps = 0
        self.model_calls = None
        self.started = time.monotonic()
        self.bodies = {name: env.model.body(name).id for name in self.object_ids}
        self.names = {body_id: name for name, body_id in self.bodies.items()}
        self.table_geom = mujoco.mj_name2id(env.model, mujoco.mjtObj.mjOBJ_GEOM, "table")
        self.open_contacts: dict[tuple[str, str], ContactEvent] = {}
        self.closed_contacts: list[ContactEvent] = []
        self.trajectory = {name: [] for name in self.object_ids}
        self.initial, _ = self._settled("initial")
        self.max_heights = {item.object_id: item.position[2] for item in self.initial.objects}

    def watch_model_calls(self, client):
        if not hasattr(client, "on_round_trip"):
            return
        self.model_calls = self.model_calls or 0
        client.on_round_trip(self._count_model_call)

    def _count_model_call(self, round_trip):
        if getattr(round_trip, "error", None) is None:
            self.model_calls += 1

    def _label(self, geom):
        m = self.env.model
        root = int(m.body_rootid[m.geom_bodyid[geom]])
        if root in self.names:
            return self.names[root]
        if geom == self.table_geom:
            return "table"
        body = m.body(m.geom_bodyid[geom]).name
        if body.endswith("_world"):
            return None
        for side in ("left", "right"):
            if body.startswith(f"{side}_"):
                return f"{side}_arm"
        return None

    def _pairs(self, data):
        pairs = set()
        for contact in data.contact[: data.ncon]:
            a, b = self._label(contact.geom1), self._label(contact.geom2)
            if a is None or b is None or a == b:
                continue
            if "table" in (a, b) and not (a.endswith("_arm") or b.endswith("_arm")):
                continue
            pairs.add(tuple(sorted((a, b))))
        return pairs

    def on_step(self):
        d = self.env.data
        self.steps += 1
        now = float(d.time)
        for name, body_id in self.bodies.items():
            z = float(d.xpos[body_id][2])
            if z > self.max_heights[name]:
                self.max_heights[name] = round(z, 4)
        current = self._pairs(d)
        for pair in current:
            event = self.open_contacts.get(pair)
            if event is None:
                self.open_contacts[pair] = ContactEvent(a=pair[0], b=pair[1], start_s=round(now, 3), end_s=round(now, 3))
            else:
                event.end_s = round(now, 3)
                event.samples += 1
        for pair, event in list(self.open_contacts.items()):
            if pair not in current and now - event.end_s > CONTACT_GAP_S:
                self.closed_contacts.append(self.open_contacts.pop(pair))
        if self.steps % TRAJECTORY_EVERY_STEPS == 0:
            for name, body_id in self.bodies.items():
                self.trajectory[name].append([round(now, 3), *np.round(d.xpos[body_id], 4).tolist()])

    def _aabb(self, data, body_id):
        m = self.env.model
        geoms = np.flatnonzero(m.body_rootid[m.geom_bodyid] == body_id)
        if len(geoms) == 0:
            return None, None
        points = []
        for geom in geoms:
            box = m.geom_aabb[geom]
            local = box[:3] + CORNERS * box[3:]
            points.append(data.geom_xpos[geom] + local @ data.geom_xmat[geom].reshape(3, 3).T)
        joined = np.vstack(points)
        return np.round(joined.min(axis=0), 4).tolist(), np.round(joined.max(axis=0), 4).tolist()

    def _snapshot(self, data, stage):
        m = self.env.model
        objects = []
        for name, body_id in self.bodies.items():
            joint = next((j for j in range(m.njnt) if m.jnt_bodyid[j] == body_id and m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE), None)
            speed = 0.0
            if joint is not None:
                dof = m.jnt_dofadr[joint]
                speed = float(np.linalg.norm(data.qvel[dof : dof + 3]))
            low, high = self._aabb(data, body_id)
            objects.append(
                ObjectState(
                    object_id=name,
                    position=np.round(data.xpos[body_id], 4).tolist(),
                    orientation_wxyz=np.round(data.xquat[body_id], 5).tolist(),
                    movable=joint is not None,
                    aabb_min=low,
                    aabb_max=high,
                    speed_m_s=round(speed, 4),
                )
            )
        environment = Environment()
        if self.table_geom >= 0:
            center, size = m.geom_pos[self.table_geom], m.geom_size[self.table_geom]
            environment = Environment(
                table_bounds=[
                    float(center[0] - size[0]),
                    float(center[0] + size[0]),
                    float(center[1] - size[1]),
                    float(center[1] + size[1]),
                ],
                table_z=float(center[2] + size[2]),
            )
        robot = {
            side: np.round([data.joint(f"{side}_joint{i}").qpos[0] for i in range(1, 7)], 4).tolist()
            for side in ("left", "right")
        }
        return Snapshot(stage=stage, sim_time=round(float(data.time), 3), objects=objects, robot_state=robot, environment=environment)

    def _settled(self, stage):
        m = self.env.model
        scratch = mujoco.MjData(m)
        mujoco.mj_copyData(scratch, m, self.env.data)
        mujoco.mj_step(m, scratch, nstep=SETTLE_STEPS)
        mujoco.mj_forward(m, scratch)
        return self._snapshot(scratch, stage), [list(pair) for pair in sorted(self._pairs(scratch))]

    def finish(self, task_id, execution_status, errors, videos, hidden_attempts):
        final, final_contacts = self._settled("final")
        contacts = sorted([*self.closed_contacts, *self.open_contacts.values()], key=lambda item: (item.start_s, item.a, item.b))
        actions = max(self.steps - self.startup_steps, 0)
        return Evidence(
            task_id=task_id,
            execution_status=execution_status,
            initial_snapshot=self.initial,
            final_snapshot=final,
            contacts=contacts,
            final_contacts=final_contacts,
            trajectory=self.trajectory,
            max_heights=self.max_heights,
            duration_seconds=round(actions * CONTROL_STEP_S, 2),
            wall_seconds=round(time.monotonic() - self.started, 2),
            action_count=actions,
            model_calls=self.model_calls,
            errors=errors,
            videos=videos,
            hidden_attempts=hidden_attempts,
        )

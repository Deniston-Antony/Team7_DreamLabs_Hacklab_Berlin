import json
import sys
import threading
import time
from pathlib import Path

import dream_examples
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
from yam_env import YamEnv  # noqa: E402

TASK = "put apple into the bowl"
MAX_SECONDS = 120
BOWL_RADIUS_M = 0.08
SETTLED_SPEED_M_S = 0.02
SETTLED_FOR_S = 2.0
MOVABLE = ("apple", "banana", "orange", "water_bottle")


def touching(env, body_a, prefixes):
    m, d = env.model, env.data
    for contact in d.contact[: d.ncon]:
        names = {m.body(m.geom_bodyid[contact.geom1]).name, m.body(m.geom_bodyid[contact.geom2]).name}
        if body_a in names and any(other.startswith(prefixes) for other in names - {body_a}):
            return True
    return False


def apple_in_bowl(env):
    d = env.data
    offset = np.linalg.norm(d.body("apple").xpos[:2] - d.body("bowl").xpos[:2])
    speed = np.linalg.norm(d.body("apple").cvel[3:])
    held = touching(env, "apple", ("left", "right"))
    return bool(offset < BOWL_RADIUS_M and speed < SETTLED_SPEED_M_S and not held and touching(env, "apple", ("bowl",)))


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    env = YamEnv()
    initial = {name: env.data.body(name).xpos.copy() for name in MOVABLE}
    initial_arm = env.state()
    stop = threading.Event()
    done = threading.Event()

    def watch():
        since = None
        while not done.wait(0.1):
            if apple_in_bowl(env):
                since = since or time.monotonic()
                if time.monotonic() - since >= SETTLED_FOR_S:
                    stop.set()
                    return
            else:
                since = None

    threading.Thread(target=watch, daemon=True).start()
    started = time.monotonic()
    try:
        dc.main(["--task", TASK, "--headless", "--quiet", "--seconds", str(MAX_SECONDS)], shared_env=env, stop_event=stop)
    finally:
        done.set()
    for _ in range(20):
        hold = env.state()
        env.step(hold["left"], hold["right"])
    final = {name: env.data.body(name).xpos.copy() for name in MOVABLE}
    arm_motion = max(np.abs(env.state()[s][:6] - initial_arm[s][:6]).max() for s in ("left", "right"))
    Image.fromarray(env.images()["top"]).save(out / "final_top.jpg", quality=90)
    result = {
        "task": TASK,
        "elapsed_s": round(time.monotonic() - started, 1),
        "stopped_early_on_success": stop.is_set(),
        "apple_in_bowl": apple_in_bowl(env),
        "apple_bowl_xy_offset_m": round(float(np.linalg.norm(final["apple"][:2] - env.data.body("bowl").xpos[:2])), 4),
        "apple_final_position": final["apple"].round(4).tolist(),
        "object_displacement_m": {n: round(float(np.linalg.norm(final[n] - initial[n])), 4) for n in MOVABLE},
        "max_joint_change_rad": round(float(arm_motion), 3),
    }
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print("RESULT " + json.dumps(result), flush=True)
    env.close()


if __name__ == "__main__":
    main(sys.argv[1])

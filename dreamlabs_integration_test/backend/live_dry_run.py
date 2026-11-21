import sys
import time
from collections import deque
from pathlib import Path

import dream
import dream_examples

sys.path.insert(0, str(Path(list(dream_examples.__path__)[0]) / "yam_mujoco"))
import dream_control as dc  # noqa: E402
from yam_env import YamEnv  # noqa: E402

TASK = "put apple into the bowl"
MODEL = dc.BETA_MODEL
KEEP_PAGE_OPEN_S = 1800


def main():
    settings = dc.settings()
    env = YamEnv()
    windows = {name: deque(maxlen=dc.FRAMES) for name in dc.VIDEO_ROLES}
    steps = 0
    while any(len(window) < dc.FRAMES for window in windows.values()):
        hold = env.state()
        env.step(hold["left"], hold["right"])
        dc.capture(env, windows, keep=steps % dc.FRAME_EVERY_STEPS == 0)
        steps += 1

    config = dc.policy_config(MODEL, settings.get("DREAM_GATEWAY_URL"), True)
    client = dc.open_policy(config, settings["DREAM_API_KEY"])
    view = dream.DebugView(client)
    try:
        dc.feed(client, env, windows, TASK, MODEL, steps, every=0, station=True)
        started = time.monotonic()
        reply = client.get_action()
        left, right = dc.validate_chunk(env, reply, verbose=False)
        print(f"BILLED_CALLS=1 RTT_S={time.monotonic() - started:.2f}", flush=True)
        print(f"CHUNK_ROWS={len(left)} LEFT_SHAPE={left.shape} RIGHT_SHAPE={right.shape}", flush=True)
        print("ARM_ACTIONS_EXECUTED=0", flush=True)
        print(f"VIEW_URL={view.url}", flush=True)
        time.sleep(KEEP_PAGE_OPEN_S)
    finally:
        view.close()
        client.close()
        env.close()


if __name__ == "__main__":
    main()

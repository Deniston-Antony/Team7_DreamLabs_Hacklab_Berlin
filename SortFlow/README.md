# SortFlow

A success checker, parallel attempts and video recording for the Dream YAM simulator (Dream-1).

## The problem

Dream-1 never says whether a task worked. In the local simulator, `put orange into the bowl` simply runs until
the timer ends (120 s by default), and the terminal prints "Finished" whether the orange is in the bowl or on the
floor. Dream-1 is also inconsistent (Dream Labs' own notes say banana-to-plate is unreliable), so a user cannot
tell whether a change made it better or worse. SortFlow fills that gap without touching Dream Labs' code.

## What it does

`yam_mujoco_checked.py` is the same terminal, prompt, commands and MuJoCo window as Dream Labs' `yam_mujoco.py`,
plus:

- **Success detection.** For tasks like `put apple into the bowl` or `put banana on the plate`, it reads the item's
  position from MuJoCo, and when the item rests inside the target for 1 simulated second it stops the run and prints
  `✓ Success: the apple is in the bowl (14.2 s of simulated time).` Otherwise, at the timeout:
  `✗ Not completed: ... (closest it got: 9 cm from the bowl centre).`
- **`--tries N`: N attempts at once.** The task runs as N independent attempts: the visible window plus N-1 hidden
  headless workers, each with its own scene and Dream-1 session. The verdict is a reliability number:
  `✓ 3 of 4 attempts succeeded: put orange into the bowl`.
- **`--record`: video of every attempt.** Each attempt's top camera is saved as an MP4, and with several attempts a
  labelled `grid.mp4` shows them side by side (green frame = success, red = failure), including the failures.

Supported items: apple, orange, banana, water bottle. Supported targets: bowl, plate. A task that names none of
these runs exactly as in Dream Labs' sim, with no check.

## Install

Python 3.12, Linux (the Dream Labs sim also runs on macOS). `pip install dreamlabs` brings the Dream client, MuJoCo,
PyAV and prompt_toolkit.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install dreamlabs
export DREAM_API_KEY=dl_live_...      # from https://platform.dreamlabs.ai/api-keys; never commit it
```

## Run

```bash
python yam_mujoco_checked.py                        # one attempt, success check
python yam_mujoco_checked.py --tries 4              # four attempts at once (about $0.06 each, 120 s limit)
python yam_mujoco_checked.py --tries 4 --record     # plus videos under sortflow_results/videos/
```

At the `❯` prompt type a task, for example `put orange into the bowl`. `/seconds 60` shortens each attempt;
`/reset`, `/model`, `/actions` work as in Dream Labs' sim. Ctrl-C, `/reset` or closing the window stops the task
and ends the hidden workers too. `--tries` accepts 1 to 16. Every attempt calls the API and costs credits; the
maximum is printed before it starts.

## How it is built

Dream Labs' files are untouched. Two small launchers wrap them at run time:

| File | Role |
|---|---|
| `yam_mujoco_checked.py` | Starts Dream Labs' launcher, reads `--tries` and `--record`, runs our viewer script instead of theirs |
| `viewer_workspace_checked.py` | Wraps each task: starts the success watcher, the hidden workers and the recording, prints the verdict |
| `sortflow.py` | The checker: target zones, `parse_task`, `is_inside`, `SuccessWatcher` |
| `parallel_bench.py` | The worker (`--worker`): one headless attempt in its own process; also a batch runner |
| `recording.py` | MP4 recording of the top camera and the grid video |
| `test_sortflow.py`, `test_parallel_bench.py`, `test_recording.py` | Offline tests: no API call, no key |

Everything else in this folder is Dream Labs' Dream YAM Sim (their README is `README_DREAM_YAM_SIM.md`).

```bash
MUJOCO_GL=egl python -m unittest test_sortflow test_parallel_bench test_recording
```

## Status and limits (honest)

- The checker, the parallel runner and the recording are tested offline with stand-in models. They have **not yet
  been run end to end with a live Dream-1 session across many trials**, so there is no reliability number to quote yet.
- One early live benchmark, `put orange into the bowl`, 5 trials of 60 s each, scored **0 of 5**. We have not
  yet established whether Dream-1 failed or the detector zone (60 mm radius around the bowl centre, an estimate from
  the scene geometry) is too strict; the diagnostics for this are in place.
- Hidden workers show no window; only attempt 1 is visible live.
- The success zones are our estimates and may need tuning.
- Parallel limits on a Dream Labs account are unknown. Start with 4; a busy server answers `DA-2000` (retried).

## Attribution

The Dream YAM Sim files (everything except the eight SortFlow files above) are from
https://github.com/dreamlabs-co/dreamlabs, Apache License 2.0 (`LICENSE`, `NOTICE`; `assets/LICENSE` for the robot
meshes and `assets/fruits/ATTRIBUTION.md` for the fruit models).

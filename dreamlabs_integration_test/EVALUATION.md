# Evaluation and Reports

The evaluation module scores each finished website task from MuJoCo evidence. It does not change Dream-1, the robot control loop, or the task itself.

## How a task is scored

1. Before motion, the backend records a settled copy of the scene.
2. During the run, it samples object heights, trajectories and contacts at the 20 Hz control rate.
3. After the run, it records a settled final scene.
4. It checks the task, finds disturbances, calculates the score and saves the report in `backend/evaluation_data/`.

Every parallel attempt records its own snapshots, contacts, video and report. The run's **Final report** averages the scored attempts, and each attempt keeps its own recommendations. Reports and videos stay available under **Benchmark history** after a restart.

## Supported instructions

| Task type | Example |
| --- | --- |
| Pick and place | `put apple into the bowl` |
| Relocation | `move the orange to the left of the table` |
| Stacking | `put the block on top of the plate` |
| Rotation | `rotate the banana by 90 degrees`, `rotate the bottle upright` |
| Protected object | `put apple into the bowl without moving the plate` |

Left, right, front and back follow the top camera view. Other instructions run normally but are marked `NOT_EVALUABLE`.

## Score

| Component | Weight |
| --- | ---: |
| Goal completion | 40 |
| Object and destination correctness | 20 |
| Scene preservation | 15 |
| Interaction quality | 15 |
| Efficiency | 10 |

`final_score = clamp(base_score - penalties, 0, 100)`, followed by the caps below.

- Weights, tolerances and penalties are in `backend/evaluation/config.py`, and every result stores the config version.
- Unavailable components are `NOT_MEASURED`; remaining weights are renormalised and coverage is shown.
- Each penalty is counted once. The displayed scene or interaction points show where it was deducted.
- Scene credit needs task engagement, and interaction and efficiency credit scale with goal completion, so doing nothing cannot earn a high score.
- A timeout caps a non-successful task at 50. A critical unsafe event caps the score at 20.
- Cancelled runs and service failures before any robot action are `NOT_EVALUABLE`.

These values are hackathon benchmark settings, not validated safety thresholds.

## Penalties

| Incident | Penalty |
| --- | ---: |
| Minor unintended displacement | -2 |
| Noticeable unintended displacement | -5 |
| Protected object moved beyond tolerance | -8 |
| Object tipped over | -10 |
| Object pushed off the table | -15 |
| Unnecessary contact with a protected object | -5 |

Only the most serious incident per object is scored, which prevents one collision from being penalised repeatedly.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/evaluation/tasks/{task_id}/evaluate` | Re-score stored evidence |
| `GET` | `/api/evaluation/tasks/{task_id}/result` | Task score and breakdown |
| `GET` | `/api/evaluation/batches` | Current and saved benchmarks |
| `POST` | `/api/evaluation/batches` | Start a new benchmark |
| `GET` | `/api/reports/tasks/{task_id}?format=json\|html` | Full task report |
| `GET` | `/api/reports/runs/{run_id}?format=json\|html` | Final report with every attempt |
| `GET` | `/api/reports/batches/{batch_id}?format=json\|html` | Full benchmark report |
| `GET` | `/api/reports/batches/{batch_id}/summary` | Website summary |

## Shared scenes and comparison

Every task runs once in each scene, and the n-th run of every task uses the same starting layout. The Final report verifies those positions, then compares tasks and scenes in a score table and lists the best task, best scene, hardest scene and where to improve.

Run recommendations come from each attempt's stored data: where it failed, closest distance, lift, contact time, distance to the goal, movement time, disturbances and video analysis.

## Suggestion tests

After a finished run with failed attempts, **Test suggestions** in its Final report turns the attempts' suggestions into separate runs. It uses one test per original run plus an unchanged control, up to 8 parallel runs, with 2 controls from 6 runs.

Each test starts from the failed attempt's saved scene and makes one change: move the object, shorten the carry, add clear space, use a successful orientation, name the object more clearly, or give more time. The report compares each test with its original attempt and subtracts the change seen in the control. A difference under 10 points is shown as no clear change.

Tests are stored with the run but do not change its benchmark score. They do not retrain Dream-1, and each change is tried once, so repeat a promising result before relying on it.

## Video judges

Each run uses the judge selected on the website. The judge also decides the task prompt sent to Dream-1, and reports show that prompt.

- **Offline judge:** OWLv2 samples 10 frames from each attempt's video and tracks which objects moved. Its prompt uses camera-visible names, such as `put red apple into the white bowl`.
- **Claude judge:** `video_judge.py` sends 8 frames from each attempt's video to Claude, half from the final 15%. Claude returns Pass, Fail or Unsure with a confidence and reason, and a disagreement with the simulator is flagged. Its prompt asks the robot to let go, such as `put apple into the bowl and let go so it rests there`.

Neither judge changes the score. Both describe visible behaviour rather than Dream-1's internal understanding. In suggestion tests, the unchanged control uses the selected judge's prompt, so each change can still be compared with the control.

## Timing and Dream-1 capacity

The time limit counts **robot movement, Dream-1 decision time and waiting for a free Dream-1 seat**. Every attempt runs for that long unless it succeeds first. A finished attempt leaves Dream-1, but the website waits for the full time before creating videos and reports. Only loading is outside the limit. Reports show movement, decision, wait and total time for each attempt. The efficiency score uses movement and decision time, so a busy server does not lower Dream-1's score.

When Dream-1 reports no capacity, the attempt holds still and retries with increasing delays for up to 90 seconds or until its time limit ends. Attempts also start 1.5 seconds apart.

## Limitations

- Dream-1's API returns arm actions, not detections or attention maps, so video perception is evidence of behaviour and does not prove what the model recognised.
- The Claude judge needs an Anthropic credential and uses API credits. The offline judge needs its local model. Either can be wrong, so review flagged videos.
- Prompts differ by judge, so scores from different judges are not a clean model comparison without a control.
- Contacts are sampled at control steps, so very brief contacts can be missed.
- Uploaded STL collisions use a convex shape.
- A small number of runs does not establish statistically meaningful performance.

Install the optional offline judge from `backend`:

```bash
uv venv --python 3.12 .vision-venv
uv pip install --python .vision-venv/bin/python -r vision/requirements.txt
```

Run the tests from `backend`:

```bash
PYTHONPATH=.deps ~/dream-env/bin/python -m unittest test_evaluation test_outcome
```

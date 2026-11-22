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

## Video perception

Dream-1 returns actions, not its detections. After a run, OWLv2 samples 10 frames from each attempt's video and tracks which objects moved. This is shown as **Perception (from video)**, is not scored, and describes observed behaviour rather than the model's internal understanding. On 36 object checks from real runs, it matched MuJoCo's movement data in 35.

## Timing and Dream-1 capacity

The time limit counts **robot movement, Dream-1 decision time and waiting for a free Dream-1 seat**. Every attempt runs for that long unless it succeeds first. A finished attempt leaves Dream-1, but the website waits for the full time before creating videos and reports. Only loading is outside the limit. Reports show movement, decision, wait and total time for each attempt. The efficiency score uses movement and decision time, so a busy server does not lower Dream-1's score.

When Dream-1 reports no capacity, the attempt holds still and retries with increasing delays for up to 90 seconds or until its time limit ends. Attempts also start 1.5 seconds apart.

## Limitations

- Dream-1's API returns arm actions, not detections or attention maps, so video perception is evidence of behaviour and does not prove what the model recognised.
- The vision check runs locally after the task and is not scored. On 108 first-frame simulator checks it reached 94% agreement with MuJoCo visibility, with 6 missed objects and no false detections.
- Contacts are sampled at control steps, so very brief contacts can be missed.
- Uploaded STL collisions use a convex shape.
- A small number of runs does not establish statistically meaningful performance.

Install the optional vision model from `backend`:

```bash
uv venv --python 3.12 .vision-venv
uv pip install --python .vision-venv/bin/python -r vision/requirements.txt
```

Run the tests from `backend`:

```bash
PYTHONPATH=.deps ~/dream-env/bin/python -m unittest test_evaluation test_outcome
```

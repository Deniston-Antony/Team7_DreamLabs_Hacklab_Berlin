# Evaluation and Reports

The evaluation module scores each finished website task from MuJoCo evidence. It does not change Dream-1, the robot control loop, or the task itself.

## How a task is scored

1. Before motion, the backend records a settled copy of the scene.
2. During the run, it samples object heights, trajectories and contacts at the 20 Hz control rate.
3. After the run, it records a settled final scene.
4. It checks the task, finds disturbances, calculates the score and saves the report in `backend/evaluation_data/`.

Hidden parallel attempts are listed with their success and closest distance. **Only the live attempt receives the full score**, because hidden workers do not yet send full snapshots.

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
| `GET` | `/api/reports/batches/{batch_id}?format=json\|html` | Full benchmark report |
| `GET` | `/api/reports/batches/{batch_id}/summary` | Website summary |

## Limitations

- Perception is `NOT_MEASURED` because Dream-1 does not expose what it recognised.
- Contacts are sampled at control steps, so very brief contacts can be missed.
- Uploaded STL collisions use a convex shape.
- A small number of runs does not establish statistically meaningful performance.

Run the tests from `backend`:

```bash
PYTHONPATH=.deps ~/dream-env/bin/python -m unittest test_evaluation test_outcome
```

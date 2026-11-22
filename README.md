# RoboTester: Dream-1 Robot Evaluation and Benchmarking Platform

Team 7, Dream Labs Hacklab Berlin.

RoboTester is a website for testing the Dream-1 robot model in a MuJoCo simulator. You set up a tabletop scene, give Dream-1 natural-language tasks, watch one or many runs live, and get scored reports from the simulator's evidence. It can then test the report's suggestions and show what changed before and after.

The simulator runs on your own computer through the official Dream Labs library. The hosted Dream Labs Playground is not embedded or controlled. Simulation results do not prove real-robot safety or reliability.

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Install](#install)
- [Run locally](#run-locally)
- [Use the website](#use-the-website)
- [Scoring and reports](#scoring-and-reports)
- [Suggestion testing](#suggestion-testing)
- [Host it on a domain](#host-it-on-a-domain)
- [API](#api)
- [Tests](#tests)
- [Costs and limitations](#costs-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Project history](#project-history)

## What it does

| Area | Features |
| --- | --- |
| Scene setup | Six scene styles, shuffle, save layout, add blocks, import STL meshes, mark objects Movable or Fixed |
| Object editing | Click to select and outline, drag, rotate and move objects in the overhead or 3D view |
| 3D view | Ctrl + drag to rotate and two-finger scroll to zoom, for live and parallel runs |
| Dream-1 runs | Several tasks across shared scenes, up to 9 runs at once |
| Fair comparison | The n-th run of every task starts from the same object layout |
| Live tracking | Each run has its own tab, cameras, timer and 0 to 100% progress bar |
| Timing | The limit counts movement, Dream-1 decisions and waiting for a free seat, and the site holds until the full time has passed |
| Reliability | Waits and retries when Dream-1 reports no free capacity |
| Evaluation | 0 to 100 score, outcome, penalties, task checks and missing-evidence notes for each attempt |
| Benchmarks | Numbered, renamable history with old reports, videos and timestamps |
| Video | A video for every run, a combined video, and 1x, 1.5x, 2x and 4x playback |
| Video judges | Choose the free Offline judge (OWLv2) or the Claude judge. Each sends its own task prompt to Dream-1 and reports on the video |
| Final reports | Task and scene comparison, best task, best and hardest scene, and recommendations from each run's data |
| Suggestion testing | Selectable one-change tests, unchanged controls, a test viewer and a before/after graph |
| Hosting | Runs from your computer on a custom domain through Cloudflare Tunnel, behind an email login |

## How it works

```text
Browser (React + TypeScript)
   │  HTTP and WebSocket
   ▼
FastAPI backend ───────► evaluation engine, reports and saved benchmarks
   │
   ├─ live MuJoCo simulator
   ├─ hidden worker processes for parallel runs
   └─ selected video judge: local OWLv2 or Claude
   │
   ▼
Dream-1 API (the key stays on the backend machine)
```

| Folder | Purpose |
| --- | --- |
| `dreamlabs_integration_test/frontend/` | Website |
| `dreamlabs_integration_test/backend/` | API, simulator control, scenes and run sessions |
| `dreamlabs_integration_test/backend/evaluation/` | Scoring, insights, reports, storage and suggestion comparison |
| `dreamlabs_integration_test/backend/judges.py` | Judge choices and their task prompts |
| `dreamlabs_integration_test/backend/video_judge.py` | Claude video judge |
| `dreamlabs_integration_test/backend/vision/` | Offline OWLv2 judge |
| `SortFlow/` | Success checks, parallel workers, capacity retries, live feeds and recording |
| `dreamlabs_integration_test/EVALUATION.md` | Detailed scoring and evidence rules |

Run data is saved locally in `backend/evaluation_data/` and `backend/task_videos/`. Both are ignored by Git.

## Install

**Requirements:** Python 3.12 (installed by `uv` below), Node.js 20.19+ or 22.12+, Git, curl, OpenGL support and a [Dream Labs API key](https://platform.dreamlabs.ai/api-keys).

| System | Status |
| --- | --- |
| macOS on Apple Silicon | Used for development |
| Ubuntu 22.04 or newer | Supported by the Dream Labs library, not tested with this project |
| Windows | Not tested. Use Ubuntu under WSL2 |

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

git clone -b Final_RoboTester_Website https://github.com/Deniston-Antony/Team7_DreamLabs_Hacklab_Berlin.git
cd Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test

cd backend
uv venv --seed --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade dreamlabs dreamlabs-client -r requirements.txt

cd ../frontend
npm ci
cd ..
```

### Optional: install the Offline judge

```bash
cd backend
uv venv --python 3.12 .vision-venv
uv pip install --python .vision-venv/bin/python -r vision/requirements.txt
cd ..
```

### Optional: turn on the Claude judge

Without an Anthropic key, the Claude option is unavailable. To turn it on, run this in the terminal that starts the backend:

```bash
printf 'Paste your Anthropic API key, then press Enter: '
read -rs ANTHROPIC_API_KEY; echo
export ANTHROPIC_API_KEY
```

The default model is `claude-opus-5-5`. Set `VIDEO_JUDGE_MODEL` to use another. Never commit the key or put it in frontend code.

### Add your API key

Paste this block, then paste your key when asked. The key stays hidden and is saved outside the project.

```bash
{
  printf 'Paste your Dream API key, then press Enter: '
  read -rs DREAM_KEY; echo
  mkdir -p ~/.dreamlabs && chmod 700 ~/.dreamlabs
  [ ! -f ~/.dreamlabs/credentials ] || cp ~/.dreamlabs/credentials ~/.dreamlabs/credentials.bak
  printf '%s\n' \
    DREAM_GATEWAY_URL=https://api.dreamlabs.ai \
    DREAM_API_URL=https://platform.dreamlabs.ai \
    DREAM_DATA_URL=https://data.dreamlabs.ai \
    "DREAM_API_KEY=$DREAM_KEY" > ~/.dreamlabs/credentials
  chmod 600 ~/.dreamlabs/credentials
  unset DREAM_KEY
  echo "API key saved."
}
```

Never commit this file or put the key in frontend code.

Before the first run on a new computer, a person should check what the model receives, as the [Dream Labs docs](https://platform.dreamlabs.ai/api-documentation/control-loop/#check-what-the-model-sees) require.

## Run locally

From `dreamlabs_integration_test`, use two terminals.

```bash
cd backend
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173**. API docs are at http://127.0.0.1:8000/docs. In local mode, `http://127.0.0.1:8000` showing `{"detail":"Not Found"}` is normal.

## Use the website

### Set up and run

1. On **Simulator**, press **Reset scene** for a clean start.
2. Choose a scene style and arrange the objects.
3. Enter a task such as `put apple into the bowl` or `put orange on the plate`. Use **Add task** for more tasks.
4. Set **Scenes per task**. For example, 2 tasks × 3 scenes = 6 runs, up to 9.
5. Choose **Start scenes**: copies of the live scene, shuffled positions, or shuffled positions with scene styles.
6. Set **Time limit per run**, from 5 to 200 seconds. Start with at least 120 seconds.
7. Press **Run task** and click any run card to watch it.

**Layout seed** is the number that decides the shuffled layouts and styles. The same starting scene, mode and seed give the same layouts again. **New layouts** picks a new seed and replaces hand edits in the parallel scenes.

### Time limit

Each run's timer starts at its first Dream-1 request and counts robot movement, Dream-1 decision time and waiting for a free seat. Loading is not counted. A finished run leaves Dream-1, but the website waits for the full limit before creating videos and reports.

### Reports

Open **Benchmarks** to see every benchmark, rename it, and open its runs. Each run has a **Final report** and a tab for each attempt.

## Scoring and reports

Each attempt gets a score from 0 to 100:

| Component | Points |
| --- | ---: |
| Goal completion | 40 |
| Correct object and placement | 20 |
| Scene preservation | 15 |
| Interaction quality | 15 |
| Efficiency | 10 |

Outcomes are **Success**, **Partial success**, **Failure**, **Unsafe** and **Not evaluable**. Penalties and score caps are explained in [EVALUATION.md](dreamlabs_integration_test/EVALUATION.md).

Dream-1 returns robot actions, not what it internally detected. Choose a **Judge** in the Tasks panel or Suggestion testing tab:

| Judge | Runs on | Prompt sent to Dream-1 for `put apple into the bowl` | What it reports |
| --- | --- | --- | --- |
| Offline judge | This computer, using OWLv2 | `put red apple into the white bowl` | Which objects moved in the video |
| Claude judge | Anthropic API | `put apple into the bowl and let go so it rests there` | Pass, Fail or Unsure, with a reason and any disagreement with the simulator |

The page shows the exact prompt before a run. Conditions you write, such as `without moving the banana`, are kept. Judges are not scored, so simulator evidence remains the source of the score. Because the prompts differ, results from different judges are not a clean model comparison unless an unchanged control uses the same prompt.

## Suggestion testing

Open a benchmark and choose the **Suggestion testing** tab beside its title. Testing is available after a run and its analysis have finished, when at least one attempt failed.

1. Tick the tests to run, or use **Select all / Deselect all**.
2. Press **Run selected tests**. The button shows the most they can cost.
3. Watch each test in the simulator-style viewer.
4. Compare the before/after graph, result table and saved rounds.

Each test starts from a failed attempt's saved scene and makes **one change**:

- Move the object closer to an arm.
- Shorten the carry to the destination.
- Add clear space around the object.
- Use the orientation from a successful attempt.
- Name the object more clearly.
- Give the run more time.

An **unchanged control** measures normal run-to-run variation. A change is marked **Helped** or **Worse** only when it differs from the control's change by at least 10 points. Each test runs once, so repeat a promising result before relying on it.

These tests do not change the benchmark score and do not retrain Dream-1. Fine-tuning Dream-1 requires an uploaded dataset of robot demonstrations, not written suggestions.

## Host it on a domain

This setup serves the site from your computer through Cloudflare Tunnel. GitHub stores the code, but GitHub Pages cannot run this app because it needs the Python backend, MuJoCo and a private API key.

**Do not expose the app without a login. Anyone who can open it can spend your Dream-1 credits.**

1. Add your domain to Cloudflare and change the nameservers at your registrar.
2. In Cloudflare Zero Trust, create a **Self-hosted** Access application for the domain, with an **Allow** policy listing only approved email addresses.
3. Install and approve the tunnel:

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create robotester
cloudflared tunnel route dns robotester your-domain.example
```

4. Create `~/.cloudflared/robotester.yml`, using the tunnel ID printed above. Never commit this file or the tunnel's credentials.

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /Users/YOUR_USER/.cloudflared/YOUR_TUNNEL_ID.json
ingress:
  - hostname: your-domain.example
    service: http://127.0.0.1:8020
  - service: http_status:404
```

5. Build the website and start the backend from `dreamlabs_integration_test`:

```bash
(cd frontend && npm run build)
cd backend
DREAM_PUBLIC_ORIGIN=https://your-domain.example MUJOCO_GL=cgl caffeinate -i .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

On Linux, leave out `caffeinate -i` and use a suitable `MUJOCO_GL` value.

6. In a second terminal, start the tunnel:

```bash
cloudflared tunnel --config ~/.cloudflared/robotester.yml run
```

7. In a private browser window, confirm that the domain asks for a login before showing the website.

The site is online only while the computer is awake, connected and running both commands. Everyone who logs in shares one simulator and the owner's Dream-1 credits. If a network cannot resolve a new domain, try Chrome's **Use secure DNS** setting with Cloudflare (1.1.1.1), or another network.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/tasks/execute` | Start a run |
| `GET` | `/api/tasks/{task_id}/status` | Run status |
| `POST` | `/api/tasks/{task_id}/cancel` | Stop a run |
| `GET` | `/api/tasks/{task_id}/video/{clip}` | Run video |
| `GET`, `POST` | `/api/evaluation/batches` | List or start benchmarks |
| `PATCH` | `/api/evaluation/batches/{batch_id}` | Rename a benchmark |
| `GET` | `/api/reports/tasks/{task_id}?format=json\|html` | Attempt report |
| `GET` | `/api/reports/runs/{run_id}?format=json\|html` | Final run report |
| `GET` | `/api/reports/batches/{batch_id}?format=json\|html` | Benchmark report |
| `POST` | `/api/reports/runs/{run_id}/analyze-videos` | Analyse an older run's videos |
| `GET` | `/api/reports/runs/{run_id}/suggestion-tests` | Test plan, progress and results |
| `POST` | `/api/reports/runs/{run_id}/suggestion-tests` | Start selected tests |
| `WS` | `/ws/live` | Live state and camera frames |

## Tests

These tests use no API key and no credits:

```bash
cd dreamlabs_integration_test/backend
uv pip install --python .venv/bin/python httpx
DREAM_VISION=off DREAM_GATEWAY_URL=https://127.0.0.1:9 MUJOCO_GL=cgl .venv/bin/python -m unittest test_video_judge test_evaluation test_outcome
cd ../../SortFlow
MUJOCO_GL=cgl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_capacity test_parallel_bench test_recording
```

Use `MUJOCO_GL=egl` on headless Linux.

## Costs and limitations

- The video judge uses Anthropic API credits for each judged attempt and sends its video frames to Anthropic.
- Every live, parallel and suggestion run uses Dream Labs credits. The website estimates about $0.07 for a 120-second run and shows the most a request can cost. Check the [Billing page](https://platform.dreamlabs.ai/billing) for current prices.
- One request runs at a time, with up to 9 runs, or up to 8 for suggestion tests.
- Only the YAM robot can run. Other arms are listed until they have simulator actuators, cameras and a measured tool center point.
- Automatic success checks cover putting a movable object in the bowl or on the plate.
- Small benchmarks can vary a lot from run to run. Repeat results before drawing conclusions.
- STL uploads are limited to 60 MB and 1.5 million triangles. Hollow objects behave as solid because MuJoCo uses a convex collision shape.
- A saved layout lasts only while the backend runs.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `No module named uvicorn` | Run the install step and use `.venv/bin/python` from `backend` |
| Port 8000, 5173 or 8020 is in use | Stop the other server using that port |
| Website says **Reconnecting** | Start the backend, then refresh |
| Task fails with a key or credit error | Check `~/.dreamlabs/credentials` and your Dream Labs balance |
| Dream-1 reports no capacity | The run waits and retries. Use fewer parallel runs or try later |
| Claude judge is not available | Set `ANTHROPIC_API_KEY` in the backend terminal and restart it |
| Offline judge is not available | Install the optional Offline judge |
| Domain does not open in Chrome | Check that the backend and tunnel are running, then try Cloudflare secure DNS |
| Page differs from this README | Restart the servers after pulling, rebuild if hosted, and refresh |
| `task_videos` is large | Delete old run folders you no longer need |

## Project history

| Branch | What it added |
| --- | --- |
| `Deniston` | Website, live MuJoCo stream, scene editing, STL import and timers |
| `ashiq-integration-v2` | SortFlow parallel runs, run tabs, recording and scene environments |
| `Deniston+improved_report` | Evaluation engine, reports, benchmark history, vision check and fair timing |
| `Deniston+Final_Report` | Shared scenes, combined videos, playback speeds, scene comparison, parallel 3D editing and per-run timers |
| `suggestion_testing` | One-change tests, controls, test viewer and before/after results |
| `web-integration` | Custom-domain hosting through Cloudflare Tunnel and this README |
| `umair-video-judge` | Claude video judge |
| `Umair+Deniston_code_combined` | Web interface with a choice of Offline or Claude judge |
| `Deniston-+-Ashiq-code-combined` | Per-run Dream-1 logs, saved start scenes and run-card log tabs |
| `Final_RoboTester_Website` | Final combined version deployed to robotester.services |

**Team:** Deniston (website and simulator integration), Umair (evaluation, scoring, reports and video judge) and Ashiq (robot, CLI and execution).

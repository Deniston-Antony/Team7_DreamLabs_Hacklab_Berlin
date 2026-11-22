# RoboTester

**A website for testing, scoring and comparing the Dream-1 robot model in a MuJoCo simulator.**

Set up a tabletop scene, give Dream-1 tasks in plain English, watch up to 9 runs live, and get scored reports. RoboTester can then test its own suggestions and show what changed before and after.

Built by Team 7 at Dream Labs Hacklab Berlin.

> [!NOTE]
> **Claude is optional.** RoboTester works without an Anthropic account or key. The free **Offline judge** is the default. You can [add the Claude judge](#choose-your-operating-system) later without changing any code.

> [!IMPORTANT]
> Real runs use your Dream Labs credits. The simulator runs on your computer through the official Dream Labs library. Simulation results do not prove that a real robot is safe or reliable.

## Choose your operating system

Click your system. Its guide contains only the installation and setup steps for that system.

<table>
  <tr>
    <td align="center" width="33%"><a href="docs/setup/macos.md"><strong>macOS</strong></a><br><sub>Apple Silicon</sub></td>
    <td align="center" width="33%"><a href="docs/setup/linux.md"><strong>Linux</strong></a><br><sub>Ubuntu or Debian</sub></td>
    <td align="center" width="33%"><a href="docs/setup/windows.md"><strong>Windows</strong></a><br><sub>WSL2 Ubuntu</sub></td>
  </tr>
</table>

Each guide covers installation, **running locally**, **using a custom domain**, the **optional Claude judge**, updates, tests and troubleshooting.

| | Run locally | Use a custom domain |
| --- | --- | --- |
| **Best for** | Personal use and testing | Sharing with approved people |
| **Address** | `http://127.0.0.1:5173` | `https://your-domain.com` |
| **Needs** | Just this computer | A domain and a free Cloudflare account |
| **Login** | Not needed, only this computer can open it | Required, by email code |
| **Online when** | Your terminals are running | Your computer is awake and both services are running |

## Contents

- [Choose your operating system](#choose-your-operating-system)
- [What RoboTester does](#what-robotester-does)
- [Use the website](#use-the-website)
- [How scoring works](#how-scoring-works)
- [Costs, security and limitations](#costs-security-and-limitations)
- [Project structure](#project-structure)
- [API](#api)
- [Project history and team](#project-history-and-team)

## What RoboTester does

| Area | Features |
| --- | --- |
| **Scene setup** | Six scene styles, shuffling, saved layouts, added blocks and imported STL meshes |
| **Object editing** | Select, drag, move and rotate objects, and mark them Movable or Fixed |
| **Dream-1 runs** | Multiple tasks across shared scenes, up to 9 runs at once |
| **Fair comparison** | The same scene number uses the same object layout for every task |
| **Live view** | Overhead, wrist and rotatable 3D cameras for each run |
| **Progress** | A timer and progress bar for every run |
| **Dream-1 logs** | A request log for each run, including timing and errors |
| **Saved scenes** | Every run saves its starting scenes so a test can be repeated |
| **Scoring** | A 0 to 100 score from simulator evidence, with penalties and checks |
| **Video judges** | The free Offline judge by default, with an optional Claude judge add-on |
| **Benchmarks** | Named history with old reports, videos and timestamps |
| **Reports** | Task and scene comparison, combined videos and recommendations based on each run |
| **Suggestion testing** | One-change tests, unchanged controls and a before/after graph |
| **Video playback** | 1x, 1.5x, 2x and 4x speeds |
| **Custom domain** | Secure hosting from your own computer through Cloudflare Tunnel |

## Use the website

### Run a test

1. On **Simulator**, press **Reset scene**.
2. Choose a scene style and arrange the objects.
3. In **Tasks**, keep **Offline judge** selected unless you added the [optional Claude judge](#choose-your-operating-system).
4. Enter a task such as `put apple into the bowl` or `put orange on the plate`.
5. Use **Add task** for more tasks.
6. Set **Scenes per task**. For example, 2 tasks × 3 scenes = 6 runs, up to 9.
7. Choose **Start scenes**: copies, shuffled positions, or shuffled positions with scene styles.
8. Set **Time limit per run**. Start with **120 seconds**.
9. Check **Prompt sent to Dream-1** and the maximum cost.
10. Press **Run task**, then click a run card to watch it.

**Layout seed** decides the shuffled layouts. The same starting scene, mode and seed recreate them. **New layouts** picks a new seed and replaces hand edits in parallel scenes.

### Choose a judge

The **Offline judge** is the default. The Claude judge is available only after you complete [its optional setup](#choose-your-operating-system).

| Judge | Runs on | Prompt sent for `put apple into the bowl` | Result |
| --- | --- | --- | --- |
| **Offline judge** | Your computer | `put red apple into the white bowl` | Which objects moved |
| **Claude judge (optional)** | Anthropic API | `put apple into the bowl and let go so it rests there` | Pass, Fail or Unsure with a reason |

The judges do not change the 0 to 100 score. Because their prompts differ, compare them on the same scenes and repeat the test before deciding which prompt works better.

### Read the time limit

Each run's timer starts at its first Dream-1 request. It counts robot movement, Dream-1 decisions and waiting for a free Dream-1 seat. Loading is not counted. A finished run leaves Dream-1, but the website waits for the full limit before creating videos and reports.

### Open reports

Go to **Benchmarks**, choose a benchmark, and open a run. The **Final report** shows combined videos, scene and task comparisons, each attempt's results, and recommendations. Each attempt has its own tab.

### Test suggestions

1. Open a benchmark and choose **Suggestion testing**.
2. Keep the Offline judge, or choose Claude if you added it, then tick the tests to run.
3. Keep at least one **unchanged control**.
4. Press **Run selected tests**.
5. Watch each test, then compare the before/after graph.

Each test starts from a failed attempt's saved scene and changes one thing. A result counts as **Helped** or **Worse** only when it differs from the control by at least 10 points. Each test runs once, so repeat a promising result before relying on it. Suggestion tests do not retrain Dream-1 or change the benchmark score.

## How scoring works

| Component | Points |
| --- | ---: |
| Goal completion | 40 |
| Correct object and placement | 20 |
| Scene preservation | 15 |
| Interaction quality | 15 |
| Efficiency | 10 |

Outcomes are **Success**, **Partial success**, **Failure**, **Unsafe** and **Not evaluable**. Full rules are in [EVALUATION.md](dreamlabs_integration_test/EVALUATION.md).

## Costs, security and limitations

**Costs**

- Every live, parallel and suggestion run uses Dream Labs credits. The website estimates about **$0.07 per 120-second run** and shows the maximum before you start. Check [Dream Labs billing](https://platform.dreamlabs.ai/billing) for current prices.
- The optional Claude judge uses Anthropic credits for each judged attempt.
- The Offline judge, tests and Cloudflare's free plan do not use those credits.

**Security**

- Keep API keys out of Git, screenshots and chat messages.
- Dream-1 credentials stay in `~/.dreamlabs/credentials`.
- Run data stays in ignored folders on your computer.
- On a custom domain, anyone you approve can spend your credits.
- Rotate a key immediately if it is exposed.

**Limitations**

- One request runs at a time, with up to 9 runs, or up to 8 for suggestion tests.
- Only the YAM robot can run. Other arms are listed until they have simulator actuators, cameras and a measured tool center point.
- Automatic success checks cover putting a movable object in the bowl or on the plate.
- Small benchmarks can vary a lot from run to run, so repeat results before drawing conclusions.
- Video judges can be wrong, and neither proves what Dream-1 internally understood.
- STL uploads are limited to 60 MB and 1.5 million triangles. Hollow objects behave as solid.
- Everyone on a hosted site shares one simulator.
- The custom-domain option goes offline when the computer sleeps, loses internet or stops the services.

## Project structure

```text
Team7_DreamLabs_Hacklab_Berlin/
├── README.md
├── SortFlow/                         Parallel workers, success checks, logs and recording
├── Website/                          Reference material and screenshots
└── dreamlabs_integration_test/
    ├── EVALUATION.md                 Detailed scoring rules
    ├── frontend/                     React and TypeScript website
    └── backend/
        ├── main.py                   API, simulator control and live stream
        ├── outcome.py                Run sessions and parallel workers
        ├── environments.py           Shuffled and styled scenes
        ├── scene_saves.py            Saved starting scenes
        ├── suggestions.py            One-change test scenes
        ├── judges.py                 Judge choices and task prompts
        ├── video_judge.py            Claude video judge
        ├── vision/                   Offline OWLv2 judge
        └── evaluation/               Scoring, reports and benchmarks
```

```text
Browser
   │ HTTP and WebSocket
   ▼
FastAPI backend ───► scoring, reports and saved benchmarks
   ├─ live MuJoCo simulator
   ├─ hidden worker processes for parallel runs
   └─ video judge: Offline by default, optional Claude
   │
   ▼
Dream-1 API (the key stays on the backend computer)
```

## API

With the backend running locally, full API docs are at `http://127.0.0.1:8000/docs`.

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/tasks/execute` | Start a run |
| `GET` | `/api/tasks/{task_id}/status` | Run status |
| `POST` | `/api/tasks/{task_id}/cancel` | Stop a run |
| `GET` | `/api/tasks/{task_id}/video/{clip}` | Run video |
| `GET` | `/api/judges` | Available judges |
| `POST` | `/api/judges/preview` | Preview a judge's task prompts |
| `GET`, `POST` | `/api/evaluation/batches` | List or start benchmarks |
| `PATCH` | `/api/evaluation/batches/{batch_id}` | Rename a benchmark |
| `GET` | `/api/reports/runs/{run_id}?format=json\|html` | Final run report |
| `GET` | `/api/reports/batches/{batch_id}?format=json\|html` | Benchmark report |
| `GET`, `POST` | `/api/reports/runs/{run_id}/suggestion-tests` | Suggestion plan, progress, results and start |
| `WS` | `/ws/live` | Live state and camera frames |

## Project history and team

| Branch | What it added |
| --- | --- |
| `Deniston` | Website, live MuJoCo stream, scene editing, STL import and timers |
| `ashiq-integration-v2` | SortFlow parallel runs, run tabs, recording and scene environments |
| `Deniston+improved_report` | Evaluation engine, reports, benchmark history and fair timing |
| `Deniston+Final_Report` | Shared scenes, combined videos, playback speeds, comparisons and per-run timers |
| `suggestion_testing` | One-change tests, controls, test viewer and before/after results |
| `web-integration` | Custom-domain hosting through Cloudflare Tunnel |
| `umair-video-judge` | Claude video judge |
| `Umair+Deniston_code_combined` | Choice of Offline or Claude judge |
| `Deniston-+-Ashiq-code-combined` | Per-run Dream-1 logs and saved start scenes |
| `Final_RoboTester_Website` | Final combined version |

**Team:** Deniston (website and simulator integration), Umair (evaluation, reports and video judge) and Ashiq (robot, CLI and execution).

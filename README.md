# Team7 Dream Labs Hacklab Berlin

A website for configuring a MuJoCo robot scene, sending natural-language tasks to Dream-1, and watching the simulated YAM arms live.

It also measures how reliable Dream-1 is: one task or several different tasks can run as parallel runs, each run is checked for success automatically, every run can be watched live, and every run is recorded to video.

The simulator runs on your computer through the official Dream Labs library. The hosted Dream Labs Playground is not embedded or controlled.

## System support

| System | Status |
| --- | --- |
| macOS on Apple Silicon | Clean install tested |
| Ubuntu 22.04 or newer | Supported by the Dream Labs library, not yet tested with this project |
| Windows | Not tested. Use Ubuntu under WSL2 and follow the Ubuntu steps |

## Requirements

- Python 3.12, installed automatically by `uv` below
- Node.js 20.19+ or 22.12+
- Git and curl
- OpenGL support for camera rendering
- A [Dream Labs API key](https://platform.dreamlabs.ai/api-keys)

On Ubuntu, install the basic tools first:

```bash
sudo apt update
sudo apt install -y git curl
```

Install Node.js from [nodejs.org](https://nodejs.org/) if `node -v` is missing or too old.

## Install

Run these from the folder where you want the project:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

git clone -b ashiq-integration-v2 https://github.com/Deniston-Antony/Team7_DreamLabs_Hacklab_Berlin.git
cd Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test

cd backend
uv venv --seed --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade dreamlabs dreamlabs-client -r requirements.txt

cd ../frontend
npm ci
cd ..
```

The parallel runs, run tabs and videos are on the `ashiq-integration-v2` branch. Once it is merged, clone `Deniston` instead.

## Add your API key

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

## Run

Start from the `dreamlabs_integration_test` folder and use two terminals.

**Terminal 1: backend**

```bash
cd backend
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Wait for `Uvicorn running on http://127.0.0.1:8000`.

**Terminal 2: website**

```bash
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173**. Press `Ctrl+C` in each terminal to stop.

| Service | Link |
| --- | --- |
| Website | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Backend check | http://127.0.0.1:8000/api/scenes |

`http://127.0.0.1:8000` showing `{"detail":"Not Found"}` is normal because the backend has no home page.

## Use it

### Run one task

1. Press **Reset scene** for a clean scene.
2. Choose a scene. Click an object in the **Overhead** or **3D view** camera to select and outline it, then drag it, rotate it, or set it to **Movable** or **Fixed** in the **Scene objects** table. You can also add a block, import a `.stl` file with **Import mesh**, or press **Shuffle**. In 3D view, hold **Ctrl** and drag to rotate the camera, and scroll with two fingers to zoom.
3. In **Tasks**, enter a task in the form `put <object> into the bowl` or `put <object> on the plate`, such as `put apple into the bowl`.
4. Set **Time limit per run** from 5 to 200 seconds.
5. Press **Run task** and watch the live view and timer. The run stops with **Succeeded** as soon as the simulator confirms the object has settled in the bowl or on the plate, or ends with **Time limit reached**. Use **Stop** to end early.

### Run a task several times at once

Set **Runs for this task** to more than 1, for example 4. The first run is the live one in the picture; the others are parallel runs in the background. Each parallel run starts from a copy of the scene as it is when you press **Run task**, including objects you moved, added or removed, and is judged by the same success check. Parallel runs exist only while a task runs, so **Reset scene** before **Run task** gives every run, live and parallel, the same clean start. A stopped parallel run still keeps its video and shows **Stopped**. At the end the result reads, for example, **3 of 4 runs succeeded**, with each run's time or how close it got.

### Run several different tasks at once

Press **Add task** and enter another task, such as `put orange into the bowl`. Each task has its own number of runs. Press **Run 2 tasks**. The result lists each task, for example **2 of 3 tasks succeeded**. A task the checker cannot understand is refused before anything starts, and the message names it.

At most 8 runs can run at once, counting every task. The line under **Add task** shows the total and the most it can cost.

### Watch every run

The **Runs** tabs on the left of the picture list **Live run**, **Parallel run 2**, and so on, each with its goal (such as `Orange → Bowl`) and its state: Starting, Running, Succeeded, Failed or Stopped. Click a tab to watch that run live. Parallel runs show their camera view only (Overhead, Left wrist and Right wrist); the **Scene objects** table always shows the live run. Editing the scene is turned off while a parallel run is shown.

### Reset, save a layout, restart

- **Reset scene** puts every object and both arms back where they started. It also works while a task runs: after you confirm, it stops the live run and every parallel run, keeps their videos, then resets.
- **Save layout** remembers the scene as it is now, including objects you moved, added, removed or set to Fixed. From then on, **Reset scene** puts that layout back instead of the default table. **Use default instead** forgets it.
- **Restart** stops whatever is running, resets the scene and runs the last tasks again with the same runs and time limit. It uses credits again.

### Videos

Every run is recorded. When a task ends, a video player appears under the result, with links to each run (**Live run**, **Parallel run 2**, ...) and, with more than one run, **All runs**: one video with every run side by side, framed green for success and red for failure. The files are saved in `dreamlabs_integration_test/backend/task_videos/<task id>/`.

**Every run uses Dream Labs credits, parallel runs included.** About $0.06 for a 120-second run; the page shows the most a request can cost before you press **Run**. Check current prices on the [Billing page](https://platform.dreamlabs.ai/billing).

Before the first run on a new computer, a person should check what the model receives, as the [Dream Labs docs](https://platform.dreamlabs.ai/api-documentation/control-loop/#check-what-the-model-sees) require.

## Current limitations

- Six local tabletop scene styles. They change colors, lighting and walls, and are not the hosted Playground rooms.
- Only the YAM robot can run. Other arms are listed as setup required until each has simulator actuators, cameras and a person-measured tool center point.
- Automatic success checks cover putting a movable object in the bowl or on the plate. Other instructions run until the time limit or until stopped, and cannot have parallel runs.
- One request runs at a time, with up to 8 runs in it. **Add simulation** is a placeholder.
- Parallel runs show the camera view only, not the 3D view, the object table or the joint values.
- STL uploads are limited to 60 MB and 1.5 million triangles, are simplified to 60,000 triangles when larger, and are scaled to the size you enter. MuJoCo uses a convex collision shape, so hollow objects such as cups behave as solid.
- Objects and arms stay where a task leaves them until you press **Reset scene**.
- The saved layout is kept only while the backend runs; restarting the backend forgets it.
- The website only accepts connections from the same computer.
- Simulation results do not prove real-robot safety or reliability.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `cd: no such file or directory` | Move to the `dreamlabs_integration_test` folder first |
| `No module named uvicorn` | Run the install step and use `.venv/bin/python` from `backend` |
| Port 8000 or 5173 is in use | Stop the other server using that port |
| Website says **Reconnecting** | Start the backend, then refresh |
| Camera rendering fails on headless Linux | Try `MUJOCO_GL=egl` before the backend command on a compatible GPU |
| Task fails with an API key or credit error | Check `~/.dreamlabs/credentials` and your Dream Labs balance |
| A parallel run shows `exit 2` or "Use /login, set DREAM_API_KEY" | Parallel runs read the same key as the backend. Save it with the step above, then restart the backend |
| A parallel run shows **Stopped** without a result | Its process ended early; its error is listed under the result. Check the backend terminal |
| Buttons do not match this README | Restart both servers after pulling, then refresh the page |
| `task_videos` is getting large | Delete old folders in `dreamlabs_integration_test/backend/task_videos/`; they are not part of the project |

## Tests

The success check, parallel runs, live run view and videos have offline tests that need no API key and no credits:

```bash
cd dreamlabs_integration_test/backend
uv pip install --python .venv/bin/python httpx   # only once: the API tests need it
MUJOCO_GL=egl .venv/bin/python -m unittest test_outcome
cd ../../SortFlow
MUJOCO_GL=egl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_sortflow test_parallel_bench test_livefeed test_recording
```

On macOS, leave out `MUJOCO_GL=egl`.

## More

- `SortFlow/` holds the success checker, the parallel-run worker, live streaming and recording the website uses, and a terminal version of the simulator with the same features. See [SortFlow/README.md](SortFlow/README.md).
- More detail is in the [integration feasibility report](dreamlabs_integration_test/DREAMLABS_INTEGRATION_FEASIBILITY_REPORT.md).

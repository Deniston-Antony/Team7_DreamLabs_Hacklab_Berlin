# Team7 Dream Labs Hacklab Berlin

A website for configuring a MuJoCo robot scene, sending natural-language tasks to Dream-1, and watching the simulated YAM arms live.

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

git clone -b Deniston https://github.com/Deniston-Antony/Team7_DreamLabs_Hacklab_Berlin.git
cd Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test

cd backend
uv venv --seed --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade dreamlabs dreamlabs-client -r requirements.txt

cd ../frontend
npm ci
cd ..
```

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

1. Press **Reset** for a clean scene.
2. Select an object in the table to move, rotate or remove it, or add a block.
3. Enter a task, such as `put apple into the bowl`.
4. Set a time limit from 5 to 120 seconds.
5. Press **Execute task** and watch the live view. Use **Stop task** to end early.

**Execute task uses Dream Labs credits.** Check current prices on the [Billing page](https://platform.dreamlabs.ai/billing).

Before the first run on a new computer, a person should check what the model receives, as the [Dream Labs docs](https://platform.dreamlabs.ai/api-documentation/control-loop/#check-what-the-model-sees) require.

## Current limitations

- One tabletop scene and the YAM robot only.
- The website does not judge task success yet, so a run ends at its time limit or when stopped.
- One task runs at a time.
- Objects and arms stay where a task leaves them until you press **Reset**.
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

More detail is in the [integration feasibility report](dreamlabs_integration_test/DREAMLABS_INTEGRATION_FEASIBILITY_REPORT.md).

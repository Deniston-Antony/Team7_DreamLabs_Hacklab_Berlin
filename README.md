# RoboTester

**A website for testing, scoring and comparing the Dream-1 robot model in a MuJoCo simulator.**

Set up a tabletop scene, give Dream-1 tasks in plain English, watch up to 9 runs live, and get scored reports. RoboTester can then test its own suggestions and show what changed before and after.

Built by Team 7 at Dream Labs Hacklab Berlin.

> [!NOTE]
> **Claude is optional.** RoboTester works without an Anthropic account or key. The free **Offline judge** is the default. You can [add the Claude judge](#optional-add-the-claude-judge) later without changing any code.

> [!IMPORTANT]
> Real runs use your Dream Labs credits. The simulator runs on your computer through the official Dream Labs library. Simulation results do not prove that a real robot is safe or reliable.

## Choose your setup

Everyone completes [Part 1: Install RoboTester](#part-1-install-robotester). Then choose one option:

| | [Option A: Run locally](#option-a-run-locally) | [Option B: Use a custom domain](#option-b-use-a-custom-domain) |
| --- | --- | --- |
| **Best for** | Personal use and testing | Sharing with approved people |
| **Address** | `http://127.0.0.1:5173` | `https://your-domain.com` |
| **Needs** | Just this computer | A domain and a free Cloudflare account |
| **Login** | Not needed, only this computer can open it | Required, by email code |
| **Online when** | Your terminals are running | Your computer is awake and both services are running |

> **How to use the operating system tabs:** GitHub READMEs do not support real tabs, so each step has a collapsible section for **macOS**, **Linux** and **Windows**. Click your system and follow only that section.

## Contents

- [What RoboTester does](#what-robotester-does)
- [Before you start](#before-you-start)
- [Part 1: Install RoboTester](#part-1-install-robotester)
- [Option A: Run locally](#option-a-run-locally)
- [Option B: Use a custom domain](#option-b-use-a-custom-domain)
- [Optional: Add the Claude judge](#optional-add-the-claude-judge)
- [Use the website](#use-the-website)
- [How scoring works](#how-scoring-works)
- [Stop, restart and update](#stop-restart-and-update)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
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

## Before you start

You need:

- A [Dream Labs API key](https://platform.dreamlabs.ai/api-keys) with credits.
- About **8 GB of free disk space**, including the optional Offline judge.
- At least **16 GB of RAM** if you plan to run several parallel runs.
- An internet connection.
- A domain name, only for Option B.

You do **not** need an Anthropic account unless you choose the [optional Claude judge](#optional-add-the-claude-judge).

| System | Status |
| --- | --- |
| **macOS on Apple Silicon** | Clean install tested |
| **Ubuntu 22.04 or 24.04** | Instructions provided, not yet tested with this project |
| **Windows 10 or 11** | Supported through WSL2 Ubuntu, not yet tested with this project |

Windows must use WSL2 because RoboTester's parallel runs use process features that native Windows Python does not provide.

---

## Part 1: Install RoboTester

### Step 1: Open a terminal

<details>
<summary><strong>macOS</strong></summary>

Press `Cmd + Space`, type **Terminal**, and press Enter.

</details>

<details>
<summary><strong>Linux</strong></summary>

Press `Ctrl + Alt + T`, or open **Terminal** from your applications.

</details>

<details>
<summary><strong>Windows</strong></summary>

1. Click **Start**, type **PowerShell**, right-click it, and choose **Run as administrator**.
2. Install WSL2 with Ubuntu:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

3. Restart Windows if asked.
4. Open **Ubuntu** from the Start menu.
5. Create a Linux username and password when asked. The password stays hidden while you type.
6. Run every remaining command in the **Ubuntu** window, not PowerShell.

Keep the project inside your Ubuntu home folder. Do not put it under `/mnt/c`, because that is much slower.

</details>

### Step 2: Install the required tools

This installs Git, Node.js, `uv` and the graphics libraries the simulator needs.

<details>
<summary><strong>macOS</strong></summary>

1. Install Apple's command-line tools. Click **Install** if a window appears, and wait for it to finish:

   ```bash
   xcode-select --install
   ```

2. If you do not have Homebrew, install it:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   eval "$(/opt/homebrew/bin/brew shellenv)"
   ```

   Follow any **Next steps** Homebrew prints.

3. Install the tools:

   ```bash
   brew install git node uv
   ```

4. Check them. Each command should print a version, and Node must be 20.19 or newer:

   ```bash
   git --version
   node --version
   uv --version
   ```

</details>

<details>
<summary><strong>Linux</strong></summary>

These commands are for Ubuntu or Debian.

1. Install the system tools:

   ```bash
   sudo apt update
   sudo apt install -y git curl build-essential libgl1 libegl1 libgl1-mesa-dri libosmesa6 libglib2.0-0
   ```

2. Install Node.js 22:

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
   sudo apt install -y nodejs
   ```

3. Install `uv`:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH="$HOME/.local/bin:$PATH"
   ```

4. Check them. Each command should print a version:

   ```bash
   git --version
   node --version
   uv --version
   ```

</details>

<details>
<summary><strong>Windows</strong></summary>

Run these in the **Ubuntu** window.

1. Install the system tools:

   ```bash
   sudo apt update
   sudo apt install -y git curl build-essential libgl1 libegl1 libgl1-mesa-dri libosmesa6 libglib2.0-0
   ```

2. Install Node.js 22:

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
   sudo apt install -y nodejs
   ```

3. Install `uv`:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH="$HOME/.local/bin:$PATH"
   ```

4. Check them. Each command should print a version:

   ```bash
   git --version
   node --version
   uv --version
   ```

</details>

### Step 3: Download RoboTester

This step is the same on macOS, Linux and Windows Ubuntu:

```bash
cd ~
git clone https://github.com/Deniston-Antony/Team7_DreamLabs_Hacklab_Berlin.git
cd Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test
```

### Step 4: Install the backend

The backend controls the simulator and talks to Dream-1. This step can take several minutes.

```bash
cd backend
uv venv --seed --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade dreamlabs dreamlabs-client -r requirements.txt
.venv/bin/python -c "import dream, mujoco, fastapi; print('Backend installed')"
cd ..
```

You should see `Backend installed`.

### Step 5: Install the website

```bash
cd frontend
npm ci
npm run build
cd ..
```

You should see `built in` near the end.

### Step 6: Add your Dream Labs API key

1. Open [Dream Labs API keys](https://platform.dreamlabs.ai/api-keys) and create or copy a key.
2. Paste this whole block into the terminal and press Enter:

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

3. Paste your key when asked and press Enter. **Nothing appears while you paste.** That is normal.

The key is saved outside the project. Never commit it or put it in website code.

### Step 7: Install the Offline judge (default)

The Offline judge is RoboTester's default. It checks which objects moved in each video, runs on your computer, and needs no Anthropic account or credits. It is a large download. If you skip it, runs and scoring still work, but videos are not analysed.

```bash
cd backend
uv venv --python 3.12 .vision-venv
uv pip install --python .vision-venv/bin/python -r vision/requirements.txt
cd ..
```

The vision model downloads the first time this judge analyses a video.

Installation is finished. Continue with [Option A](#option-a-run-locally) or [Option B](#option-b-use-a-custom-domain). You do not need Claude for either option.

---

## Option A: Run locally

Only your computer can open the website in this mode. You need **two terminals**.

### A1: Start the backend

Open a terminal and go to the backend folder:

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
```

Start the backend:

<details>
<summary><strong>macOS</strong></summary>

```bash
MUJOCO_GL=cgl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

If rendering fails, press `Ctrl + C` and use `MUJOCO_GL=osmesa` instead.

</details>

<details>
<summary><strong>Windows</strong></summary>

Run this in **Ubuntu**:

```bash
MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

If rendering fails, press `Ctrl + C` and use `MUJOCO_GL=osmesa` instead.

</details>

Wait for `Uvicorn running on http://127.0.0.1:8000`, and keep this terminal open.

### A2: Start the website

Open a **second terminal**. On Windows, open a second **Ubuntu** window.

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/frontend
npm run dev
```

### A3: Open RoboTester

Open **http://127.0.0.1:5173** in your browser. On Windows, use a normal Windows browser.

Before your first real run on a new computer, a person should check what the model receives, as the [Dream Labs documentation](https://platform.dreamlabs.ai/api-documentation/control-loop/#check-what-the-model-sees) requires.

To stop, press `Ctrl + C` in both terminals.

---

## Option B: Use a custom domain

This serves RoboTester from your computer at your own domain. Cloudflare Tunnel connects it without opening a port on your router, and Cloudflare Access requires an email login.

GitHub Pages cannot host the full app because it needs a running Python backend, MuJoCo and private API keys.

> [!CAUTION]
> **Never make this site public without the login.** Anyone who can open it can start runs and spend your credits. Complete B3 before B5.

In the commands below, replace `your-domain.com` with your domain.

### B1: Add your domain to Cloudflare

1. Create a free [Cloudflare account](https://dash.cloudflare.com/sign-up).
2. Click **Add a domain**, enter your domain, and choose the **Free** plan.
3. You do not need to add DNS records. The tunnel creates the record later.
4. Continue until Cloudflare shows **two nameservers**.

### B2: Point your domain to Cloudflare

1. Open your domain registrar, such as GoDaddy or Namecheap.
2. Find **Nameservers** and choose custom nameservers.
3. Replace the existing nameservers with the two from Cloudflare.
4. Turn **DNSSEC** off if it is on.
5. Save, return to Cloudflare, and click **Check nameservers**.
6. Wait until the domain says **Active**. This is often quick but can take up to 24 hours.

<details>
<summary><strong>GoDaddy steps</strong></summary>

Go to **My Products → your domain → DNS → Nameservers → Change Nameservers → I'll use my own nameservers**, enter Cloudflare's two nameservers, and save.

</details>

### B3: Add the login

1. In Cloudflare, open **Zero Trust**. Choose a team name and the **Free** plan if asked.
2. Go to **Access → Applications → Create new application → Self-hosted**.
3. Set **Application name** to `RoboTester`.
4. Click **Add public hostname**. Leave **Subdomain** and **Path** blank, and choose your domain.
5. Under **Access policies**, create a policy:
   - **Policy name:** `Allowed people`
   - **Action:** `Allow`
   - **Include → Selector:** `Emails`
   - **Value:** only the email addresses you approve
6. Keep **One-time PIN** on, then save the application.

Do not use the **Everyone** selector.

### B4: Install Cloudflare Tunnel

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install cloudflared
cloudflared --version
```

</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update
sudo apt install -y cloudflared
cloudflared --version
```

</details>

<details>
<summary><strong>Windows</strong></summary>

Run this in **Ubuntu**:

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update
sudo apt install -y cloudflared
cloudflared --version
```

</details>

### B5: Create the tunnel

1. Log in to Cloudflare from the terminal:

   ```bash
   cloudflared tunnel login
   ```

   A browser page opens. If it does not, copy the printed link into your browser. Choose your domain and click **Authorize**.

2. Edit the first line, then paste this whole block. It works on macOS, Linux and Windows Ubuntu:

   ```bash
   DOMAIN=your-domain.com
   cloudflared tunnel create robotester
   TUNNEL_ID=$(cloudflared tunnel list | awk '$2=="robotester"{print $1}')
   cat > ~/.cloudflared/robotester.yml <<EOF
   tunnel: $TUNNEL_ID
   credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json
   ingress:
     - hostname: $DOMAIN
       service: http://127.0.0.1:8020
     - service: http_status:404
   EOF
   chmod 600 ~/.cloudflared/robotester.yml
   cloudflared tunnel route dns robotester "$DOMAIN"
   ```

Never commit anything from `~/.cloudflared`. It contains your tunnel credentials.

### B6: Start RoboTester

Open a terminal and build the latest website:

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/frontend
npm run build
cd ../backend
```

Start the backend, using your domain:

<details>
<summary><strong>macOS</strong></summary>

`caffeinate` keeps the Mac awake while the site runs.

```bash
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=cgl caffeinate -i .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

If rendering fails, press `Ctrl + C` and use `MUJOCO_GL=osmesa` instead. Turn off automatic sleep in your system settings.

</details>

<details>
<summary><strong>Windows</strong></summary>

Run this in **Ubuntu**:

```bash
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

If rendering fails, press `Ctrl + C` and use `MUJOCO_GL=osmesa` instead. In Windows **Power & battery**, stop the PC from sleeping while the site runs.

</details>

Wait for `Uvicorn running`. Then open a **second terminal** and start the tunnel:

```bash
cloudflared tunnel --config ~/.cloudflared/robotester.yml run
```

Keep both terminals open.

### B7: Confirm the login works

1. Open your domain in a **private browser window**.
2. You must see the Cloudflare login **before** RoboTester.
3. Enter an approved email and the code sent to it.
4. Confirm the Simulator and Benchmarks pages load.
5. Try an unapproved email. It must be refused.

If RoboTester opens without a login, **stop the tunnel immediately** with `Ctrl + C` and fix B3.

---

## Optional: Add the Claude judge

**Skip this section unless you want it.** Without it, the whole website works with the Offline judge, and **Claude judge** simply shows as not available.

No code changes are needed. Claude support is already included and stays off until the backend has an Anthropic API key.

| | Offline judge (default) | Claude judge (optional) |
| --- | --- | --- |
| **Account needed** | None | Anthropic API account |
| **Cost** | Free | Uses Anthropic credits for each judged attempt |
| **Runs on** | Your computer | Anthropic API |
| **Video data** | Stays on your computer | Selected video frames are sent to Anthropic |
| **Result** | Which objects moved | Pass, Fail or Unsure with a reason |

### C1: Create an Anthropic API key

1. Open the [Claude Console](https://platform.claude.com) and sign up or log in.
2. Add credits under **Settings → Billing**. A Claude chat subscription does not include API credits.
3. Optional: set a monthly spend limit under **Settings → Limits**.
4. Open [API keys](https://platform.claude.com/settings/keys) and click **Create Key**.
5. Name it `RoboTester`, then copy it. The full key is only shown once.

Never put this key in the project, GitHub, a screenshot or a chat.

### C2: Add the key to the backend terminal

1. Stop the running backend with `Ctrl + C`.
2. In that same terminal, paste this whole block and press Enter:

   ```bash
   {
     printf 'Paste your Anthropic API key, then press Enter: '
     read -rs ANTHROPIC_API_KEY; echo
     export ANTHROPIC_API_KEY
     echo "Anthropic key loaded for this terminal."
   }
   ```

3. Paste your key when asked and press Enter. **Nothing appears while you paste.** That is normal.

The key is kept only in this terminal and is not saved to a file. Repeat this step whenever you open a new backend terminal.

### C3: Start the backend again

Use the command for your system and setup. For a custom domain, replace `your-domain.com`.

<details>
<summary><strong>macOS</strong></summary>

**Run locally:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
MUJOCO_GL=cgl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Custom domain:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=cgl caffeinate -i .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

</details>

<details>
<summary><strong>Linux</strong></summary>

**Run locally:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Custom domain:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

If rendering fails, use `MUJOCO_GL=osmesa`.

</details>

<details>
<summary><strong>Windows</strong></summary>

Run these in **Ubuntu**.

**Run locally:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Custom domain:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=egl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

If rendering fails, use `MUJOCO_GL=osmesa`.

</details>

### C4: Choose the Claude judge

1. Refresh the website.
2. In **Tasks** or **Suggestion testing**, open **Judge** and choose **Claude judge**.
3. Check that **Prompt sent to Dream-1** now ends with `and let go so it rests there`.
4. Run a task. After it finishes, open its report and find **Video judge (not scored)**.

On a custom domain, everyone you approve can use the Claude judge and spend your Anthropic credits.

### Turn Claude off

Stop the backend with `Ctrl + C`, run this, and start the backend normally:

```bash
unset ANTHROPIC_API_KEY
```

The website returns to the Offline judge. Your saved reports stay available.

## Use the website

### Run a test

1. On **Simulator**, press **Reset scene**.
2. Choose a scene style and arrange the objects.
3. In **Tasks**, keep **Offline judge** selected unless you added the [optional Claude judge](#optional-add-the-claude-judge).
4. Enter a task such as `put apple into the bowl` or `put orange on the plate`.
5. Use **Add task** for more tasks.
6. Set **Scenes per task**. For example, 2 tasks × 3 scenes = 6 runs, up to 9.
7. Choose **Start scenes**: copies, shuffled positions, or shuffled positions with scene styles.
8. Set **Time limit per run**. Start with **120 seconds**.
9. Check **Prompt sent to Dream-1** and the maximum cost.
10. Press **Run task**, then click a run card to watch it.

**Layout seed** decides the shuffled layouts. The same starting scene, mode and seed recreate them. **New layouts** picks a new seed and replaces hand edits in parallel scenes.

### Choose a judge

The **Offline judge** is the default. The Claude judge is available only after you complete [its optional setup](#optional-add-the-claude-judge).

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

## Stop, restart and update

**Stop:** press `Ctrl + C` in each terminal.

**Restart:** repeat [Option A](#option-a-run-locally) or [B6](#b6-start-robotester).

**Update to the latest code:**

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin
git pull origin main
cd dreamlabs_integration_test/backend
uv pip install --python .venv/bin/python --upgrade dreamlabs dreamlabs-client -r requirements.txt
cd ../frontend
npm ci
npm run build
```

Then start the servers again.

**Take the custom domain offline:** press `Ctrl + C` in the tunnel terminal. To remove it permanently:

```bash
cloudflared tunnel cleanup robotester
cloudflared tunnel delete robotester
```

Then delete its DNS record and Access application in Cloudflare.

## Run the tests

These tests use no API keys and no credits.

The result is the `OK` or `FAILED` line. On macOS, Python can print `Segmentation fault` while closing the simulator after a line that says `OK`. The tests still passed.

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
uv pip install --python .venv/bin/python httpx
```

<details>
<summary><strong>macOS</strong></summary>

```bash
DREAM_VISION=off DREAM_GATEWAY_URL=https://127.0.0.1:9 MUJOCO_GL=cgl .venv/bin/python -m unittest test_dream_client_log test_evaluation test_outcome test_video_judge
cd ../../SortFlow
MUJOCO_GL=cgl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_capacity test_collision_audit test_env test_livefeed test_parallel_bench test_recording test_roundtrip_log test_sortflow test_terminal_input
```

</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
DREAM_VISION=off DREAM_GATEWAY_URL=https://127.0.0.1:9 MUJOCO_GL=egl .venv/bin/python -m unittest test_dream_client_log test_evaluation test_outcome test_video_judge
cd ../../SortFlow
MUJOCO_GL=egl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_capacity test_collision_audit test_env test_livefeed test_parallel_bench test_recording test_roundtrip_log test_sortflow test_terminal_input
```

</details>

<details>
<summary><strong>Windows</strong></summary>

Run this in **Ubuntu**:

```bash
DREAM_VISION=off DREAM_GATEWAY_URL=https://127.0.0.1:9 MUJOCO_GL=egl .venv/bin/python -m unittest test_dream_client_log test_evaluation test_outcome test_video_judge
cd ../../SortFlow
MUJOCO_GL=egl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_capacity test_collision_audit test_env test_livefeed test_parallel_bench test_recording test_roundtrip_log test_sortflow test_terminal_input
```

</details>

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `command not found: brew`, `node` or `uv` | Close and reopen the terminal, then repeat Step 2 |
| `No module named uvicorn` | Run Step 4 and start the backend from the `backend` folder |
| `No such file or directory` | Run the full `cd ~/Team7_DreamLabs_Hacklab_Berlin/...` command from that step |
| Port 8000, 5173 or 8020 is in use | Stop the other server. On macOS or Linux, `lsof -i :8000` shows what is using it |
| Website says **Reconnecting** | Start the backend, then refresh |
| Camera or OpenGL error on Linux or WSL2 | Use `MUJOCO_GL=osmesa` and confirm the Step 2 graphics libraries are installed |
| `http://127.0.0.1:5173` does not open on Windows | Keep both Ubuntu windows open. In PowerShell, run `wsl --update` and `wsl --shutdown`, then start again |
| Run fails with a key or credit error | Check `~/.dreamlabs/credentials` and your [Dream Labs billing](https://platform.dreamlabs.ai/billing) |
| Dream-1 reports no capacity | The run waits and retries. Use fewer parallel runs or try later |
| Offline judge says not installed | Complete Step 7 and restart the backend |
| Claude judge is unavailable | This is normal without a key. To use it, follow [Optional: Add the Claude judge](#optional-add-the-claude-judge) |
| Claude judge shows a credit error | Add credits in the [Claude Console](https://platform.claude.com) |
| Domain does not resolve | Wait for Cloudflare to show **Active**. In Chrome, try **Use secure DNS** with Cloudflare, or another network |
| Cloudflare error `1033` or `530` | The tunnel is not running. Start it from B6 |
| Cloudflare error `502` | The backend is not running on port 8020. Start it from B6 |
| Domain opens but live view does not connect | Set `DREAM_PUBLIC_ORIGIN` to your exact `https://` domain and restart the backend |
| The page looks out of date | Run `npm run build` in `frontend`, restart the servers, and refresh |
| `task_videos` is too large | Delete old folders in `backend/task_videos/` that you no longer need |

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

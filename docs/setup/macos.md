# RoboTester setup: macOS

<table>
  <tr>
    <td align="center" width="33%"><strong>● macOS</strong><br><sub>Apple Silicon</sub></td>
    <td align="center" width="33%"><a href="linux.md"><strong>Linux</strong></a><br><sub>Ubuntu or Debian</sub></td>
    <td align="center" width="33%"><a href="windows.md"><strong>Windows</strong></a><br><sub>WSL2 Ubuntu</sub></td>
  </tr>
</table>

[← Back to README](../../README.md)

This guide is for **macOS on Apple Silicon**. A clean install has been tested.

Complete **Part 1**, then choose **Option A** to run on this computer or **Option B** to use your own domain.

## On this page

- [Before you start](#before-you-start)
- [Part 1: Install RoboTester](#part-1-install-robotester)
- [Option A: Run locally](#option-a-run-locally)
- [Option B: Use a custom domain](#option-b-use-a-custom-domain)
- [Optional: Add the Claude judge](#optional-add-the-claude-judge)
- [Stop, restart and update](#stop-restart-and-update)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)

## Before you start

You need:

- A [Dream Labs API key](https://platform.dreamlabs.ai/api-keys) with credits.
- About **8 GB of free disk space**, including the optional Offline judge.
- At least **16 GB of RAM** if you plan to run several parallel runs.
- An internet connection.
- A domain name, only for Option B.

You do **not** need an Anthropic account unless you choose the [optional Claude judge](#optional-add-the-claude-judge).

---

## Part 1: Install RoboTester

### Step 1: Open a terminal

Press `Cmd + Space`, type **Terminal**, and press Enter.

### Step 2: Install the required tools

This installs Git, Node.js and `uv`.

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

### Step 3: Download RoboTester

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

## Option A: Run locally

Only your computer can open the website in this mode. You need **two terminals**.

### A1: Start the backend

Open a terminal and go to the backend folder:

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
```

Start the backend:

```bash
MUJOCO_GL=cgl .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Wait for `Uvicorn running on http://127.0.0.1:8000`, and keep this terminal open.

### A2: Start the website

Open a **second terminal**.

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/frontend
npm run dev
```

### A3: Open RoboTester

Open **http://127.0.0.1:5173** in your browser.

Before your first real run on a new computer, a person should check what the model receives, as the [Dream Labs documentation](https://platform.dreamlabs.ai/api-documentation/control-loop/#check-what-the-model-sees) requires.

To stop, press `Ctrl + C` in both terminals.

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

```bash
brew install cloudflared
cloudflared --version
```

### B5: Create the tunnel

1. Log in to Cloudflare from the terminal:

   ```bash
   cloudflared tunnel login
   ```

   A browser page opens. If it does not, copy the printed link into your browser. Choose your domain and click **Authorize**.

2. Edit the first line, then paste this whole block:

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

`caffeinate` keeps the Mac awake while the site runs.

```bash
DREAM_PUBLIC_ORIGIN=https://your-domain.com MUJOCO_GL=cgl caffeinate -i .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

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

Use the command for your setup. For a custom domain, replace `your-domain.com`.

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

The result is the `OK` or `FAILED` line. Python can print `Segmentation fault` while closing the simulator after a line that says `OK`. The tests still passed.

```bash
cd ~/Team7_DreamLabs_Hacklab_Berlin/dreamlabs_integration_test/backend
uv pip install --python .venv/bin/python httpx
```

```bash
DREAM_VISION=off DREAM_GATEWAY_URL=https://127.0.0.1:9 MUJOCO_GL=cgl .venv/bin/python -m unittest test_dream_client_log test_evaluation test_outcome test_video_judge
cd ../../SortFlow
MUJOCO_GL=cgl ../dreamlabs_integration_test/backend/.venv/bin/python -m unittest test_capacity test_collision_audit test_env test_livefeed test_parallel_bench test_recording test_roundtrip_log test_sortflow test_terminal_input
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `command not found: brew`, `node` or `uv` | Close and reopen the terminal, then repeat Step 2 |
| `No module named uvicorn` | Run Step 4 and start the backend from the `backend` folder |
| `No such file or directory` | Run the full `cd ~/Team7_DreamLabs_Hacklab_Berlin/...` command from that step |
| Port 8000, 5173 or 8020 is in use | Stop the other server. `lsof -i :8000` shows what is using it |
| Website says **Reconnecting** | Start the backend, then refresh |
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

---

[← Back to README](../../README.md)

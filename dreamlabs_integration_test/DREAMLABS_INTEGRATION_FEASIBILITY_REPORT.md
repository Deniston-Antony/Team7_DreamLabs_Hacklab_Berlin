# Dream Labs Integration Feasibility Report

**Date:** 2026-11-21
**Audience:** Deniston, Umar and Ashiq
**Tested on:** macOS arm64, Python 3.12.13, `dreamlabs` 1.0.42, `dreamlabs-client` 1.1.7, MuJoCo 4.0.1
**Official repository inspected:** `dreamlabs-co/dreamlabs` at `ba8ad98`
**Dream Labs usage:** 39 model calls, about $0.05 at the Playground's displayed $0.0014 per call

## Section 1: Executive summary

**Verdict: Feasible with limitations. Conditional GO.**

- **Build on the local Dream YAM Sim, not the hosted Playground.** The local simulator gives our backend real object poses, scene edits, resets, contacts and camera frames.
- **The hosted Playground cannot be our control surface.** Its platform pages forbid iframe embedding, and we found no documented API for its rooms, robots or objects.
- **The live pipeline works.** A real Dream-1 run drove the simulated arms from a backend script for 1,824 actions.
- **The first task failed physically.** The apple did not reach the bowl, and the simulator evidence detected that correctly.

Status labels used below:

| Label | Meaning |
| --- | --- |
| VERIFIED | Run and observed in this investigation |
| DOCUMENTED | Stated in official docs or source, not run here |
| NOT SUPPORTED | Explicitly blocked or unavailable |
| UNKNOWN | Not enough evidence |

## Section 2: Repository investigation

| Area | Class | Location | Confirmed interface |
| --- | --- | --- | --- |
| Model client | A: SDK | `dream_library/dream_client/src/dream/facade.py` | `policy`, `set_observation`, `get_action`, `reset_episode`, `on_round_trip`, `last_timing`, `close` |
| Live View client | A: SDK | `dream_library/dream_client/src/dream/live.py` | `LiveViewClient`; needs a registered `robot_id` |
| Local simulator | A: SDK example | `dream_library/examples/yam_mujoco/yam_env.py` | `YamEnv.reset`, `state`, `step`, `images`, `close`, `build_xml` |
| Simulator control loop | A: SDK example | `dream_library/examples/yam_mujoco/dream_control.py` | `policy_config`, `open_policy`, `observation`, `validate_chunk` |
| CLI | B: CLI | `dream_library/robots/src/robots/cli.py` | `dreamcli setup`, `robot up`, `robot run`, `robot report`, `example` |
| Station HTTP API | B: CLI | `dream_library/robots/src/robots/station_api.py` | `GET /status`; `POST /start`, `/stop`, `/home`, `/reset`, `/prompt` |
| Rooms, object catalog, shuffle, clear | C: Playground only | Hosted frontend | Visible in the UI only |
| External Playground scene API | D: not exposed | No match in repo or docs | None found |

Authentication uses `~/.dreamlabs/credentials`. The key must stay on the backend.

## Section 3: Playground embedding

| Route | Status | Evidence |
| --- | --- | --- |
| Iframe of `platform.dreamlabs.ai` pages, including `/sandbox` and `/live` | NOT SUPPORTED | Response header has `frame-ancestors 'none'` |
| Iframe of public `dreamlabs.ai/playground/` | UNKNOWN | No blocking header seen, but no embedding docs; not used |
| Embeddable simulator component | UNKNOWN | None found in repo or docs |
| Public JavaScript SDK | UNKNOWN | None found; the page's own scripts are not a public API |
| Public simulator session API | UNKNOWN | None documented |
| Authenticated external integration | NOT SUPPORTED for scene control | Keys cover model, dataset and robot APIs; no scene routes documented |
| Live View for our own robot stream | DOCUMENTED | `dream.LiveViewClient`; shown on the Dream Labs page |
| Local simulator | VERIFIED | Self-test and proof of concept passed |

No browser security or authentication protection was bypassed.

## Section 4: Scene manipulation

| Operation | Available interface | Can control externally? | Evidence | Limitation |
| --- | --- | --- | --- | --- |
| Select scene | Six local MuJoCo styles | Local only | All six selected and rendered | Styles are not the hosted rooms |
| Select robot | YAM runs; 8 other arms listed | YAM only | `/api/robots`; others return 501 | Others need actuators, cameras and a measured tool center point |
| List objects | MuJoCo model | Yes, local | 6 objects listed | Smaller catalog than Playground |
| Add object | `MjSpec` rebuild | Yes, local | `block_1` added | Proof of concept adds blocks only |
| Remove object | `MjSpec` rebuild | Yes, local | Block and apple removed | Rebuild resets arm state |
| Move object | MuJoCo free joint | Yes, local | Pose read back exactly | Bowl and plate are fixed |
| Rotate object | MuJoCo free joint | Yes, local | 90° quaternion read back | Fixed objects cannot rotate |
| Shuffle objects | Random collision-checked layout | Yes, local | 25 of 25 runs succeeded | Fixed bowl and plate stay in place |
| Clear table | Compose from remove | Feasible, not built | Remove verified | Fixed objects need a rebuild rule |
| Reset scene | `YamEnv.reset` plus rebuild | Yes, local | Removed apple restored | None found |
| Read object positions | `MjData` | Yes, local | World position and wxyz orientation | Simulator ground truth only |
| Read robot state | `YamEnv.state` | Yes, local | 6 joints and gripper per arm | None found |

## Section 5: Robot and CLI integration

**Recommended route:** a backend loop using `DreamClient` and `YamEnv`, modelled on `dream_control.py`.

1. Backend receives the instruction.
2. Backend resets the scene and records the initial snapshot.
3. Loop sends camera windows, joint state and prompt to `get_action`.
4. Loop validates each action chunk and steps `YamEnv`.
5. Backend records actions, timing, poses and contacts.
6. Backend records the final snapshot and passes it to evaluation.

`dreamcli robot run` targets a configured robot station, so it is not the natural route for this simulator. A successful run does not prove task success; only the final scene evidence can do that.

## Section 6: Real-time monitoring

| Signal | Classification | Source |
| --- | --- | --- |
| Robot actions | Directly available, verified | `get_action` response |
| Camera observations | Directly available, verified | `YamEnv.images` |
| Robot status | Directly available, verified | `YamEnv.state` |
| Task execution status | Available with additional processing | Our loop must report it |
| Errors | Not verified | `DreamError` types and loop handling |
| Action timestamps | Available with additional processing | Our loop clock; `last_timing` not run |
| Object positions | Directly available, verified | `MjData` |
| Object movements | Available with additional processing | Compare poses over time |
| Contact events | Directly available, verified | `MjData.contact` |
| Collision events | Available with additional processing | Classify contact pairs |
| Final scene state | Directly available, verified | `MjData` |

## Section 7: Proof-of-concept results

| Test | Result | Evidence |
| --- | --- | --- |
| Official simulator self-test | PASS | `dreamcli example yam-mujoco --self-test` |
| Headless three-camera render | PASS | 360x640 RGB per camera |
| List scene, robot and objects | PASS | HTTP 200 |
| Add, move, rotate and remove block | PASS | Pose read back from simulator |
| Remove built-in apple, then reset | PASS | Apple absent, then restored |
| Repeat identical idle rollout after reset | PASS | Matching final pose |
| Unsupported scene or object type | PASS | HTTP 501 |
| Moving fixed plate | PASS | HTTP 404 |
| Out-of-range position | PASS | HTTP 422 |
| Website render | PASS | Headless Chrome showed camera and live state |
| Frontend type check and build | PASS | `npm run build` |
| Website button clicks | NOT RUN | Same endpoints tested directly |
| One-call dry run with arms held | PASS | 48 valid rows per arm in 2.09 s; a person confirmed the inputs |
| Real Dream-1 pipeline, "put apple into the bowl" | PASS | 38 calls, 1,824 actions executed, arms moved up to 1.649 rad |
| Physical task outcome | **FAIL** | Apple ended 0.27 m from the bowl centre, after moving 0.0755 m |

The integration worked, but Dream-1 did not complete this task in this single zero-shot trial. One trial does not measure its success rate. Other objects moved 0.0042 to 0.0157 m.

No mock adapter was needed. Every displayed pose and frame comes from the local simulator.

## Section 8: Recommended architecture

**Choose Option C:** the website backend talks to the Dream SDK and local simulator.

```text
React dashboard
      | REST + WebSocket
      v
FastAPI backend
      |-- Simulator adapter --> YamEnv / MuJoCo --> poses, contacts, frames
      |-- Execution loop ----> DreamClient ------> Dream-1 actions
      |
      v
Evidence store --> Umar's evaluator --> reports --> dashboard
```

| Criterion | A: Embed | B: External session | C: SDK + local sim | D: Hybrid |
| --- | --- | --- | --- | --- |
| Development time | Blocked | Blocked | Medium | Medium to high |
| Integration complexity | High | High | Medium | High |
| Authentication | Blocked | Undocumented | Backend key | Two flows |
| Simulator access | None | None | Full | Split |
| Object manipulation | No | No | Yes | Local only |
| Robot execution | No | No | Documented | Documented |
| Real-time monitoring | No | No | Full | Partial |
| Reliability | Low | Low | High | Medium |
| Future scalability | Low | Low | Medium | Medium |

## Section 9: Unsupported features

- Embedding the hosted platform Playground.
- External control of Playground rooms, robots, catalog, object transforms, shuffle or clear.
- Reading object poses or contacts from a hosted Playground session.
- Playground rooms and non-YAM robots in the local simulator without additional assets and calibration.

## Section 10: Development recommendations

1. Run a small repeated baseline of the demo tasks to measure how often Dream-1 succeeds.
2. Freeze the shared scene, task, event and result contracts around the verified fields.
3. Ashiq turns the control loop into a one-task backend executor.
4. Umar scores simulator snapshots and contact data.
5. Deniston extends this interface into the batch builder and live monitor.
6. Use demo tasks the scene supports: apple to bowl, orange to bowl, and apple to bowl without moving the water bottle. Avoid banana to plate, which the docs call inconsistent.

## Section 11: Final go/no-go decision

| Question | Answer |
| --- | --- |
| 1. Reuse the existing simulator? | **Yes**, the official local Dream YAM Sim |
| 2. Display it inside our website? | **Yes** for local camera views; **no** for the hosted platform Playground |
| 3. Manipulate objects externally? | **Yes locally**; no hosted API |
| 4. Select scenes and robots externally? | **Limited**: six local scene styles; only YAM can run |
| 5. Trigger robot tasks from our backend? | **Yes**, verified by a backend script; the website button is wired but tested only with a stand-in model |
| 6. Monitor robot execution? | **Yes** for actions, state and poses; live website streaming tested with a stand-in model |
| 7. Enough evidence for evaluation? | **Yes for simulator state**; it correctly identified a failed task |
| 8. Execute multiple tasks independently? | **Sequentially likely**: reset verified. Parallel isolation is unknown |
| 9. Capabilities needing workarounds? | Scene variety, robot variety, shuffle, clear and non-block objects |
| 10. Sensible to proceed? | **Yes, with Option C** |

**Recommendation: GO for the benchmarking platform with Option C.** The pipeline and failure detection work. Task reliability remains unmeasured and needs repeated trials.

## Setup and execution

Requires the Dream library in `~/dream-env`, and Node.js.

```bash
cd dreamlabs_integration_test/backend
uv pip install --python ~/dream-env/bin/python --target .deps -r requirements.txt
PYTHONPATH=.deps ~/dream-env/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd dreamlabs_integration_test/frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The website streams the simulator live over WebSocket. **Execute task uses Dream Labs credits.** The stream and task controls were tested with a stand-in model at 7.7 FPS and 0.73 Mbps; the button has not yet made a billed Dream-1 run.

To run the billed command-line test instead:

```bash
cd dreamlabs_integration_test/backend
~/dream-env/bin/python live_task_test.py /tmp/dream_live_run
```

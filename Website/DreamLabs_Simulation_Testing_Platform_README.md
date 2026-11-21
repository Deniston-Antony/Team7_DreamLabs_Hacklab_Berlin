# Dream Labs Simulation Testing, Monitoring & Benchmarking Platform

**Project type:** Hackathon MVP\
**Team:** Deniston (website and simulator integration), Umar
(evaluation, scoring and reports), Ashiq (robot, simulation execution
and CLI)\
**Time available:** 15 hours\
**Primary objective:** Test robot task execution in the existing Dream
Labs sandbox. Training is explicitly out of scope for this sprint.

------------------------------------------------------------------------

## 1. Product objective

Build a web application that lets a user configure one or more
robot-manipulation scenarios, submit multiple natural-language tasks,
execute them through the Dream Labs robot stack, monitor what happens in
the simulator, and produce evidence-based task and batch reports.

The application is an evaluation and orchestration layer around the
existing simulator. It must not recreate the Dream Labs simulator or
claim to control it through undocumented endpoints.

### MVP user journey

1.  Open the web application.
2.  Select a supported scene (for example, Kitchen, Sunny apartment,
    Cottage, Walnut study or Evening den, as shown in the supplied
    Playground screenshots).
3.  Select a supported robot (the screenshots show YAM, Trossen WidowX
    AI, MakerMods Metal, SO-101, OpenYAM, AgiLeX PiPER, Universal Robots
    UR5e, Seeed reBot B601 and MakerMods Maker Arm).
4.  Add, remove, move, rotate, shuffle or clear supported objects, if
    the existing sandbox interface exposes those operations through a
    supported integration route.
5.  Create a batch containing several tasks. Each task has its own scene
    configuration, robot, instruction, expected outcome and evaluation
    settings.
6.  Submit the batch.
7.  The execution service runs each task using the Dream Labs CLI or a
    supported DreamClient control loop.
8.  The website receives execution events and observations and displays
    task status and evidence.
9.  The evaluator checks the final scene against task-specific success
    conditions, measures unwanted interactions and calculates a score.
10. The dashboard displays a report for each task and an aggregate
    report for the batch.

### Out of scope for this 15-hour sprint

-   Training or fine-tuning Dream-1.
-   Building a new physics simulator.
-   Reimplementing the Dream Labs Playground.
-   Claiming physical safety from simulation-only tests.
-   Building a production-grade user, billing or credit-management
    system.
-   Promising parallel execution on one shared simulator instance.
    Parallelism is allowed only if independent sessions/instances are
    confirmed to work.

------------------------------------------------------------------------

## 2. What the supplied Playground screenshots establish

The screenshots show the following existing Playground capabilities:

-   Scene/room selection: Kitchen, Sunny apartment, Cottage, Walnut
    study and Evening den.
-   Robot selection: multiple robot options are listed.
-   Object categories: Food, Toys, Kitchen, Home and tools, Containers
    and Pictures.
-   Example objects: apple, avocado, banana, croissant, cupcake, egg,
    grapes, ice cream, kiwi, lemon, lime, lychee, basket, bin, bowl,
    box, deep bowl, flower pot, plate, plant pot, marker, scissors,
    blocks and toys.
-   Object selection and placement on a work surface.
-   Selected-object move and rotate controls.
-   Clear-table and shuffle controls.
-   Natural-language task entry.
-   A visible credit balance and account menu.

These are observations from the supplied screenshots, not proof that the
same features are available through a public API.

### Integration feasibility gate

The public Dream Labs Python repository documents `dreamcli` for setup
and robot operation, and `dream.DreamClient` for a custom
observation/action loop. It does not, by itself, document a public API
for controlling the Playground's room selector, object catalogue, object
transforms, shuffle or clear-table functions.

Before implementing the scene editor, inspect the current official
documentation, installed package, Playground's supported integration
options and any public SDK examples. Do not invent endpoint names, event
names, methods or payloads.

Preferred integration order:

1.  Official documented scene/simulator API or SDK, if available.
2.  Officially supported embedded Playground/session interface, if
    available.
3.  A documented local simulator interface supplied by Dream Labs, if
    available.
4.  A clearly labelled manual/assisted fallback: open the existing
    Playground separately and record the execution through the supported
    CLI/client interface.

Do not rely on brittle DOM selectors, private undocumented endpoints,
reverse-engineered authentication flows or automated clicks against a
third-party site as the core architecture. If no supported scene-control
interface is available, implement the adapter contract and a mock/demo
adapter so the rest of the platform can be tested. Report the limitation
honestly.

------------------------------------------------------------------------

## 3. System architecture

``` text
                         WEB BROWSER
┌──────────────────────────────────────────────────────────────────┐
│ Dashboard                                                        │
│  • Scene and robot selection                                     │
│  • Object/scene controls (through simulator adapter)             │
│  • Batch and task builder                                        │
│  • Live task monitor                                             │
│  • Individual and aggregate reports                              │
└──────────────────────────────┬───────────────────────────────────┘
                               │ REST + WebSocket
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ BACKEND API / ORCHESTRATOR                                       │
│  • Batch and task management                                     │
│  • Validation and queue                                           │
│  • Execution lifecycle                                            │
│  • Event collection                                               │
│  • Evidence storage                                               │
└───────────────┬───────────────────────────┬──────────────────────┘
                │                           │
                ▼                           ▼
┌──────────────────────────┐   ┌───────────────────────────────────┐
│ SIMULATOR ADAPTER        │   │ DREAM LABS EXECUTION ADAPTER      │
│ • List/select scenes     │   │ • dreamcli or DreamClient         │
│ • List/select objects    │   │ • Robot setup/status               │
│ • Add/remove objects     │   │ • Submit task instruction          │
│ • Set object pose        │   │ • Collect action/observation events│
│ • Reset/seed scene       │   │ • Stop/timeout/error handling      │
│ • Read scene state       │   └───────────────────────────────────┘
└───────────────┬──────────┘                    │
                └────────────────┬─────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│ EVIDENCE + EVALUATION ENGINE                                     │
│ • Initial and final scene snapshots                              │
│ • Task success predicates                                        │
│ • Object pose and state comparison                               │
│ • Contact/collision/action events, if available                  │
│ • Unwanted disturbance and safety penalties                      │
│ • Explainable score calculation                                  │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ REPORTING                                                        │
│ • Per-task result and score                                      │
│ • Evidence and timeline                                          │
│ • Failure/penalty breakdown                                      │
│ • Batch aggregate and comparison                                 │
│ • JSON export; HTML/PDF if time permits                           │
└──────────────────────────────────────────────────────────────────┘
```

### Recommended implementation stack

Keep the stack familiar and small. If the repository already has a
working stack, retain it rather than rewriting it.

  -----------------------------------------------------------------------
  Layer                               MVP recommendation
  ----------------------------------- -----------------------------------
  Frontend                            React + TypeScript + Vite

  UI                                  Existing project UI library or
                                      simple CSS

  Backend                             Python + FastAPI

  Async execution                     Background worker / asyncio task
                                      queue

  Live updates                        WebSocket

  Persistence                         SQLite for MVP

  Dream Labs integration              Python adapter around supported
                                      `dreamcli` or `dream.DreamClient`
                                      interfaces

  Reports                             JSON first; HTML/PDF only after
                                      core evaluation works

  Testing                             Pytest + frontend smoke tests
  -----------------------------------------------------------------------

### Deployment assumption

For the 15-hour MVP, run the backend and Dream Labs CLI on the same
development machine where the required Python environment, simulator
access and credentials are configured. The browser connects to the
backend. Do not expose the Dream Labs API key to frontend JavaScript.

------------------------------------------------------------------------

## 4. Core modules and ownership

  -----------------------------------------------------------------------
  Module                  Responsibility          Owner
  ----------------------- ----------------------- -----------------------
  Web dashboard           Scene/task UI, robot    Deniston
                          selection, live status, 
                          reports view            

  Simulator adapter       Existing sandbox        Deniston, with Ashiq
                          integration and         validating robot
                          scene/object operations compatibility

  Execution adapter       CLI/client startup,     Ashiq
                          task execution, event   
                          stream, cleanup         

  Batch orchestration     Run multiple task       Ashiq
                          records, reset between  
                          tasks, capture          
                          lifecycle               

  Evidence contract       Common event and        All three; Ashiq owns
                          snapshot format         execution events

  Evaluator               Task success predicates Umar
                          and scene-state         
                          comparison              

  Scoring                 Base score, penalties,  Umar
                          caps and explanations   

  Reports                 Per-task and aggregate  Umar
                          reports                 

  Integration             End-to-end test and     All three
                          final demo              
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 5. Shared interface contract --- agree this before parallel coding

The team must agree on these data structures in the first hour. Put them
in a shared `contracts/` folder or a single agreed schema document. Do
not let each workstream invent incompatible field names.

### 5.1 Batch request

``` json
{
  "batch_id": "generated-by-backend",
  "name": "Kitchen manipulation benchmark",
  "tasks": [
    {
      "task_id": "task-001",
      "scene_id": "kitchen",
      "robot_id": "yam",
      "instruction": "Pick up the apple and place it in the bin.",
      "initial_scene": {
        "objects": [
          {"object_id": "apple-1", "type": "apple", "pose": null},
          {"object_id": "bin-1", "type": "bin", "pose": null}
        ]
      },
      "success_criteria": [
        {"type": "object_inside_container", "object_id": "apple-1", "container_id": "bin-1"}
      ],
      "max_duration_seconds": 120
    }
  ]
}
```

`pose: null` means the simulator adapter must resolve the actual pose
after scene setup. Do not assume that screenshot coordinates are world
coordinates.

### 5.2 Task lifecycle

Use a consistent state machine:

``` text
PENDING → VALIDATING → PREPARING → RUNNING
                         │           │
                         │           ├──→ COMPLETED
                         │           ├──→ FAILED
                         │           ├──→ TIMED_OUT
                         │           └──→ CANCELLED
                         └──→ SETUP_FAILED
```

A task can be `COMPLETED` at the execution layer while the evaluator
labels its outcome `SUCCESS`, `PARTIAL_SUCCESS` or `FAILURE`. These are
different concepts.

### 5.3 Execution event

``` json
{
  "event_id": "generated-id",
  "batch_id": "batch-001",
  "task_id": "task-001",
  "timestamp": "ISO-8601",
  "event_type": "action | observation | scene_snapshot | contact | collision | status | error",
  "source": "dreamcli | dream_client | simulator | orchestrator",
  "payload": {}
}
```

Only emit `contact` or `collision` events when the simulator or robot
interface actually provides that evidence. Never fabricate them from a
language-model interpretation.

### 5.4 Scene snapshot

``` json
{
  "task_id": "task-001",
  "timestamp": "ISO-8601",
  "stage": "initial | intermediate | final",
  "objects": [
    {
      "object_id": "apple-1",
      "type": "apple",
      "position": [0.0, 0.0, 0.0],
      "orientation": [0.0, 0.0, 0.0, 1.0],
      "state": {}
    }
  ],
  "robot_state": {},
  "capture_source": "simulator"
}
```

Coordinate conventions, units, quaternion order and object identifiers
must be defined by the adapter. If the source does not expose a field,
omit it or mark it unavailable; do not fill it with guessed values.

### 5.5 Task result

``` json
{
  "task_id": "task-001",
  "execution_status": "COMPLETED",
  "outcome": "SUCCESS",
  "score": 88,
  "score_components": {},
  "penalties": [],
  "evidence_refs": [],
  "summary": "Apple reached the target bin. One unrelated object was disturbed."
}
```

------------------------------------------------------------------------

## 6. Task creation and batch execution

### Task configuration fields

Each task should contain:

-   Task name and natural-language instruction.
-   Scene/environment.
-   Robot model.
-   Initial object list and placements.
-   Target object(s) and destination/goal.
-   Expected final condition(s).
-   Objects that must not move or be touched.
-   Maximum duration or action/call budget, if supported.
-   Optional task tags: pick-and-place, push, reorientation, insertion,
    stacking, sorting, tool use.
-   Optional repeat count or random seed, if the simulator supports
    deterministic resets.

### Batch rules

1.  A batch contains one or more independent task records.
2.  Each task has its own scene setup and expected outcome.
3.  Capture an initial snapshot before execution.
4.  Reset or reconstruct the scene before the next task.
5.  Do not carry object poses or robot state from one task into another
    unless the task explicitly depends on the previous task.
6.  Run sequentially on a single simulator session unless the team
    proves that separate isolated sessions are supported.
7.  Store each task's events and evidence under its own `task_id`.
8.  Continue the batch after a task failure unless the user selects
    stop-on-failure.
9.  Calculate the batch score only from valid task results. Show
    skipped, cancelled and invalid tasks separately.
10. Display progress such as `2 / 5 tasks completed`.

### MVP execution sequence

``` text
Create batch
   ↓
Validate every task
   ↓
For each task:
   Prepare/reset scene
   ↓
   Capture initial state
   ↓
   Start selected robot and submit instruction
   ↓
   Stream status/actions/observations
   ↓
   Capture final state
   ↓
   Evaluate success and unwanted interactions
   ↓
   Save score and report
   ↓
Next task
   ↓
Aggregate batch report
```

------------------------------------------------------------------------

## 7. Evaluation: what the system must measure

A task should not receive a high score merely because the CLI returned
without an error. Evaluate the result against the expected outcome and
the scene evidence.

### 7.1 Task completion

Examples of measurable success predicates:

  -----------------------------------------------------------------------
  Task instruction                    Example verification
  ----------------------------------- -----------------------------------
  Put apple in bin                    Apple is inside the bin boundary at
                                      final state

  Put object on plate                 Object is supported by/within the
                                      plate region, subject to available
                                      geometry

  Move block to the left              Final block position is left of its
                                      initial position in the defined
                                      coordinate frame

  Stack bowl on box                   Bowl is above and supported by the
                                      box

  Rotate object upright               Orientation is within the defined
                                      angular tolerance

  Do not disturb the cup              Cup pose remains within the allowed
                                      position/orientation tolerance
  -----------------------------------------------------------------------

Predicates must be task-specific. A generic text similarity score is not
sufficient to prove physical task completion.

### 7.2 Scene understanding

Measure whether the system correctly identified and used the relevant
scene entities. Where available, check:

-   Correct target object identity.
-   Correct destination/container identity.
-   Correct spatial relationship.
-   Correct initial position and final position.
-   Whether protected or irrelevant objects were left undisturbed.
-   Whether the target was occluded, inaccessible or outside the robot
    workspace.

If only screenshots are available, label the result as **visual
estimate** and include confidence/limitations. Prefer simulator
ground-truth object states.

### 7.3 Environment interaction quality

Track, when supported:

-   Unintended object displacement.
-   Unintended object rotation.
-   Objects knocked over or dropped.
-   Unnecessary contacts.
-   Collision/contact events.
-   Objects moved outside the workspace or off the table.
-   Damage-like or irreversible simulated state changes, if the
    environment models them.
-   Excessive action count or task duration.

Distinguish intended contact (grasping the apple) from unintended
contact (knocking over a protected cup). A contact event alone is not
automatically a penalty.

------------------------------------------------------------------------

## 8. Proposed scoring model (MVP)

Use a transparent 0--100 score. Umar should implement the formula in one
place, version it, and return a component-by-component explanation.
These weights are proposed starting values for the hackathon, not
validated scientific metrics.

### 8.1 Base score

  ------------------------------------------------------------------------
  Component                                   Weight What it measures
  --------------------- ---------------------------- ---------------------
  Goal completion                                 40 Whether the required
                                                     final state was
                                                     achieved

  Object and                                      20 Correct objects and
  destination                                        spatial relationships
  correctness                                        

  Scene preservation                              15 Whether
                                                     unrelated/protected
                                                     objects stayed within
                                                     tolerance

  Interaction quality                             15 Avoidable
                                                     disturbance,
                                                     unnecessary contacts
                                                     and controlled
                                                     manipulation

  Efficiency                                      10 Time/actions relative
                                                     to configured limits

  **Total**                                  **100** 
  ------------------------------------------------------------------------

For MVP, use deterministic predicates where possible. If a metric is
unavailable, mark it `NOT_MEASURED` and renormalise the available
component weights, while clearly displaying coverage. Do not silently
award full points for missing evidence.

### 8.2 Penalty model

Apply penalties after the base score:

  -----------------------------------------------------------------------
  Event                                                 Suggested penalty
  ------------------------------ ----------------------------------------
  Minor unintended displacement                                        −2
  of an unprotected object       

  Noticeable unintended                                                −5
  displacement                   

  Protected object moved beyond                                        −8
  tolerance                      

  Unintended object tipped over                                       −10

  Object pushed off the work                                          −15
  surface                        

  Unnecessary collision with a                                         −5
  protected object               

  Task timeout                                       Outcome capped at 50

  Critical unsafe event / severe  Outcome capped at 20 or marked `UNSAFE`
  scene disruption               
  -----------------------------------------------------------------------

These are initial configurable values. Do not double-penalise the same
incident under multiple labels. For example, one collision that causes
one object to fall should have a single incident record with a defined
severity, not three independent penalties unless distinct consequences
are evidenced.

### 8.3 Formula

``` text
base_score = weighted_sum(available_components)
raw_score  = base_score - sum(valid_penalties)
task_score = clamp(raw_score, 0, 100)
```

Apply outcome caps after penalty calculation:

``` text
if critical_unsafe_event:
    task_score = min(task_score, 20)
elif timed_out:
    task_score = min(task_score, 50)
```

Suggested outcome labels:

-   `SUCCESS`: all mandatory success criteria passed.
-   `PARTIAL_SUCCESS`: some progress or criteria passed, but not all
    mandatory criteria.
-   `FAILURE`: mandatory criteria failed or no meaningful progress was
    verified.
-   `UNSAFE`: a configured critical event occurred.
-   `NOT_EVALUABLE`: insufficient evidence to score reliably.

A task can be `SUCCESS` with a lower quality score if it completes the
goal but disturbs unrelated objects. Keep outcome and score separate.

### 8.4 Batch score

Use a weighted mean of evaluable task scores:

``` text
batch_score = sum(task_score × task_weight) / sum(task_weight)
```

Default every task weight to 1. Show the number of evaluable, failed,
unsafe, timed-out and skipped tasks. Do not treat `NOT_EVALUABLE` as
zero or 100; report it separately and show score coverage.

------------------------------------------------------------------------

## 9. Report specification

### 9.1 Individual task report

Every task report should contain:

1.  Task ID, task instruction, scene and robot.
2.  Execution status and outcome.
3.  Final score out of 100.
4.  Goal completion checks, each marked pass/fail/not measured.
5.  Scene-understanding checks.
6.  Initial versus final object states.
7.  Interaction timeline.
8.  Unintended interactions and penalties, with evidence references.
9.  Runtime, action/call count and timeout information where available.
10. Screenshots or video references, if captured and permitted.
11. Failure explanation and recommended next test.
12. Evidence coverage and limitations.

Example:

``` text
TASK: task-001
Instruction: Pick up the apple and place it in the bin.
Scene: Kitchen
Robot: YAM
Execution: COMPLETED
Outcome: SUCCESS
Score: 88 / 100

Goal completion: PASS
Correct target: PASS
Correct destination: PASS
Protected-object preservation: PARTIAL
Unintended interactions: 1 minor displacement
Penalties: -2
Evidence: initial snapshot, final snapshot, event timeline

Summary:
The apple reached the target bin. One unrelated object moved beyond
its configured tolerance. The task succeeded but was not disturbance-free.
```

This is an illustrative report, not an actual test result.

### 9.2 Aggregate batch report

Include:

-   Batch name and run timestamp.
-   Selected scenes and robots.
-   Number of tasks submitted, completed, failed, unsafe, timed out and
    not evaluable.
-   Overall score and score coverage.
-   Mean, median, minimum and maximum task score.
-   Per-task score table.
-   Success rate based on evaluable tasks.
-   Penalty totals grouped by severity/type.
-   Most common failure modes.
-   Scene/robot/task-category breakdown where sample sizes permit.
-   Links to individual task reports.
-   Overall conclusion and next experiments.

Do not claim statistically meaningful model performance from a handful
of tasks. Present the report as a run-level benchmark.

------------------------------------------------------------------------

## 10. Website pages and UI

### Page A --- Simulation workspace

-   Existing simulator view or supported embedded view.
-   Scene selector.
-   Robot selector.
-   Object catalogue with category filters and search.
-   Add/remove object controls.
-   Move/rotate controls for selected object.
-   Shuffle and clear controls, only if supported by the integration.
-   Reset scene.
-   Current scene object list.

### Page B --- Batch task builder

-   Add multiple task cards.
-   Select scene and robot per task.
-   Instruction text area.
-   Target and destination selection.
-   Protected objects / do-not-disturb list.
-   Expected outcome and tolerance settings.
-   Task timeout.
-   Reorder tasks and remove tasks.
-   Submit batch.

### Page C --- Live monitoring

-   Batch progress.
-   Current task and state.
-   Robot/simulator connection status.
-   Latest event and event timeline.
-   Current/final observation preview if available.
-   Live score only when enough evidence exists; otherwise show
    `Evaluating`.
-   Pause/cancel controls only if the execution interface supports them.

### Page D --- Results

-   Batch score and outcome distribution.
-   Individual task cards/table.
-   Score component breakdown.
-   Penalty explanation.
-   Evidence timeline and snapshots.
-   Export JSON; PDF/HTML if time permits.

------------------------------------------------------------------------

## 11. Suggested repository structure

Adapt to the existing repository. Do not overwrite an existing working
project structure without inspecting it first.

``` text
dream-benchmark/
├── README.md
├── contracts/
│   ├── batch.schema.json
│   ├── event.schema.json
│   ├── scene.schema.json
│   └── task-result.schema.json
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/
│   │   └── types/
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── orchestration/
│   │   ├── adapters/
│   │   │   ├── simulator/
│   │   │   └── dreamlabs/
│   │   ├── evaluation/
│   │   ├── reports/
│   │   ├── models/
│   │   └── websocket/
│   └── tests/
├── evidence/
│   └── .gitkeep
└── docs/
    ├── API_CONTRACT.md
    ├── INTEGRATION_NOTES.md
    └── SCORING.md
```

### Backend API proposal

These are application-owned routes to implement, not claimed Dream Labs
endpoints.

  Method   Route                        Purpose
  -------- ---------------------------- --------------------------------------
  GET      `/api/scenes`                List scenes supported by the adapter
  GET      `/api/robots`                List configured robot profiles
  GET      `/api/objects`               List supported object catalogue
  POST     `/api/scenes/configure`      Configure a scene, if supported
  POST     `/api/batches`               Create a batch
  GET      `/api/batches/{id}`          Get batch status/results
  POST     `/api/batches/{id}/start`    Start batch execution
  POST     `/api/batches/{id}/cancel`   Request cancellation
  GET      `/api/tasks/{id}`            Get task result
  GET      `/api/tasks/{id}/events`     Retrieve task events
  GET      `/api/tasks/{id}/report`     Retrieve task report
  GET      `/api/batches/{id}/report`   Retrieve aggregate report
  WS       `/ws/batches/{id}`           Stream progress/events

Return `501 Not Implemented` or a clear capability error for scene
operations that the real simulator adapter cannot perform. Do not return
fake success.

------------------------------------------------------------------------

## 12. Fifteen-hour delivery plan

The plan prioritises a working end-to-end vertical slice over a large
number of incomplete features.

### Time allocation

  ------------------------------------------------------------------------------------
  Time              Deniston ---          Umar ---               Ashiq ---
                    website/integration   evaluation/reporting   robot/CLI/execution
  ----------------- --------------------- ---------------------- ---------------------
  0:00--1:00        Inspect repository,   Agree score components Inspect CLI/client
                    confirm UI framework, and report schema      and prove one
                    define                                       simulator task can
                    frontend/backend                             run
                    boundary                                     

  1:00--3:00        Build workspace       Implement task         Implement execution
                    shell,                predicates and scoring adapter and one-task
                    scene/robot/task UI,  function with test     lifecycle
                    API client            fixtures               

  3:00--5:00        Implement simulator   Implement              Implement event
                    adapter interface and initial/final scene    capture, status
                    scene/object controls comparison and penalty updates and
                    where supported       records                reset/cleanup

  5:00--7:00        Build batch task      Build individual       Implement batch queue
                    builder and live      report JSON and score  and sequential task
                    monitoring UI         breakdown              execution

  7:00--9:00        Connect UI to         Implement aggregate    Connect execution
                    backend, display real batch report and       events to shared
                    execution status      summary metrics        backend contract

  9:00--11:00       Add evidence viewer   Add report UI/API      Add error handling,
                    and results dashboard integration support    timeout, cancellation
                                          and edge-case tests    if supported

  11:00--13:00      Integration testing   Validate score         End-to-end run with
                    and UI fixes          consistency and report multiple tasks and
                                          evidence               reset validation

  13:00--15:00      Demo flow, bug fixes, Verify example reports Stabilise execution
                    documentation         and aggregate          and prepare demo
                                          calculations           scenarios
  ------------------------------------------------------------------------------------

This is a parallel work plan. All three people must agree the shared
contract in the first hour and integrate continuously, not wait until
hour 15.

### Deniston --- website and simulator integration

**Primary deliverable:** A website that configures test scenarios,
submits batches, monitors execution and displays reports.

1.  Inspect the existing repository and preserve its current working
    setup.
2.  Confirm how the Dream Labs Playground can be embedded or controlled
    through supported interfaces.
3.  Build the frontend workspace and page navigation.
4.  Implement scene and robot selectors using adapter-provided
    capability lists.
5.  Implement object catalogue, category filtering and task scene
    configuration.
6.  Implement move, rotate, add, remove, shuffle, clear and reset UI
    controls only where supported.
7.  Build multi-task batch creation.
8.  Connect REST endpoints and WebSocket updates.
9.  Build live task status and evidence display.
10. Connect Umar's task and batch report output.
11. Run an end-to-end demo with Ashiq's execution service.

**Acceptance criteria:**

-   User can create at least two task definitions in one batch.
-   Each task can specify its scene, robot and instruction.
-   Supported scene/object controls work against the actual simulator or
    clearly report unsupported operations.
-   User can see task progress without refreshing.
-   Individual and aggregate results appear in the website.

### Umar --- report and score generation

**Primary deliverable:** Deterministic, explainable evaluation and
reporting.

1.  Agree the common task/event/snapshot schema.
2.  Define mandatory success predicates for at least three demo task
    types.
3.  Implement initial/final state comparison.
4.  Implement task completion and partial-success classification.
5.  Implement disturbance detection using actual available
    object-state/contact evidence.
6.  Implement the weighted 100-point scoring function.
7.  Implement penalty severity, caps and duplicate-incident handling.
8.  Implement individual task reports.
9.  Implement aggregate batch report and metrics.
10. Add unit tests for success, partial success, failure, timeout,
    unsafe and missing-evidence cases.
11. Provide report API/JSON to Deniston.

**Acceptance criteria:**

-   Same evidence always produces the same score.
-   Every score component and penalty is explainable.
-   A task can succeed while still receiving a disturbance penalty.
-   Missing evidence is shown as not measured, not fabricated.
-   Batch score excludes non-evaluable tasks and shows score coverage.

### Ashiq --- robot, sandbox and CLI

**Primary deliverable:** Reliable task execution and evidence collection
through the existing Dream Labs stack.

1.  Inspect official Dream Labs SDK/CLI documentation and local
    environment.
2.  Verify CLI availability, credentials, robot compatibility and
    simulator connection.
3.  Run one known task manually through the supported route.
4.  Implement a Python execution adapter around the confirmed CLI/client
    interface.
5.  Expose task start, status, completion, error and timeout states.
6.  Capture available action, observation, robot-state and simulator
    events.
7.  Capture initial and final scene state through supported simulator
    access.
8.  Implement sequential batch execution.
9.  Reset/reconfigure the scene between task runs where supported.
10. Add robust cleanup, timeout and failure handling.
11. Validate that the next task starts from its intended initial state.
12. Connect the event stream to the shared backend contract.

**Acceptance criteria:**

-   At least one real Dream Labs sandbox task executes from the
    application backend.
-   Execution status and errors are sent to the website.
-   Evidence is stored against the correct task ID.
-   At least two tasks can execute sequentially in one batch.
-   Scene reset/isolation is verified, or limitations are clearly
    reported.

------------------------------------------------------------------------

## 13. Integration checkpoints

### Checkpoint 1 --- Hour 1: Contract freeze

All three agree on: - Batch/task IDs and JSON schemas. - Task lifecycle
states. - API ownership and endpoint names. - Event and snapshot
fields. - How execution is started and how reports are returned.

Commit the contract before parallel implementation.

### Checkpoint 2 --- Hour 3: Vertical slice

-   Deniston can submit a task from the UI or a simple API test.
-   Ashiq can start one execution and return status.
-   Umar can score a fixture with known initial/final states.

Use mock data only for this checkpoint if real simulator evidence is not
yet available. Label it as mock.

### Checkpoint 3 --- Hour 7: First real execution

-   One task runs through the supported Dream Labs interface.
-   The website receives its status.
-   Initial/final evidence is captured if exposed.
-   Umar's evaluator produces a result.

### Checkpoint 4 --- Hour 11: Multi-task batch

-   At least two tasks run sequentially.
-   Each task has a separate scene setup and evidence record.
-   One task's failure does not corrupt the next task.
-   Individual reports and batch summary are available.

### Checkpoint 5 --- Hour 13--15: Stabilise and demo

-   Fix integration failures.
-   Run the same test twice to check score consistency.
-   Verify reset and evidence association.
-   Prepare a short demo with one successful task and one task that
    causes an unwanted disturbance, if the simulator can reproduce it
    reliably.
-   Clearly identify any mocked or unavailable integration feature.

------------------------------------------------------------------------

## 14. Demo benchmark scenarios

Use a small set of repeatable tasks. Start with simple pick-and-place
because the screenshots show apples, bins, baskets, bowls and plates.

  --------------------------------------------------------------------------
  ID                Scene             Instruction       Evaluation focus
  ----------------- ----------------- ----------------- --------------------
  T1                Kitchen           Put the apple in  Pick-and-place
                                      the bin           success

  T2                Kitchen           Put the orange in Object/destination
                                      the bowl          identity

  T3                Kitchen           Put the apple in  Goal success +
                                      the bin without   protected-object
                                      moving the plate  preservation

  T4                Kitchen           Move the object   Scene preservation
                                      to the target     
                                      area without      
                                      disturbing other  
                                      objects           

  T5                Another supported Move a selected   Scene/robot
                    room              object to a       configuration and
                                      specified         generalisation
                                      location          
  --------------------------------------------------------------------------

Only run tasks with objects and scenes confirmed available in the actual
simulator. The table describes proposed test cases, not guaranteed
Playground capabilities.

For the first demo, use two or three tasks, not all five. One clean
success and one controlled disturbance case demonstrate the evaluator
better than a large batch with weak evidence.

------------------------------------------------------------------------

## 15. Risks and mitigations

  -----------------------------------------------------------------------
  Risk                                Mitigation
  ----------------------------------- -----------------------------------
  Playground has no public scene-edit Confirm early; implement adapter
  API                                 boundary and a labelled fallback

  CLI runs only in a local            Run backend execution worker on the
  environment                         same machine

  No access to collision/contact      Use object-state deltas as a weaker
  events                              disturbance signal and label
                                      evidence quality

  No access to ground-truth object    Use available observations and mark
  poses                               visual estimates; do not claim
                                      exact geometric verification

  Scene reset is unreliable           Rebuild each task from a known
                                      scene configuration or run tasks in
                                      isolated sessions

  Multiple tasks conflict             Execute sequentially on a shared
                                      simulator

  Dream API credits/rate limits       Set task limits, show estimated
                                      call usage if available, stop
                                      runaway loops

  Robot action call fails             Capture error, stop task safely,
                                      clean up and continue/stop batch
                                      according to policy

  Scoring is subjective               Use explicit predicates, fixed
                                      weights, versioned penalties and
                                      unit tests

  Integration consumes the sprint     Prioritise one real task
                                      end-to-end, then multi-task
                                      execution, then optional UI polish
  -----------------------------------------------------------------------

Dream-1 is described by Dream Labs as a research beta and not validated
for safety-critical use. Keep testing controlled, use conservative
limits and do not treat a high benchmark score as a real-world safety
certification.

------------------------------------------------------------------------

## 16. What to improve later: simulation testing and training

Training is not part of this sprint. The test platform should
nevertheless produce useful data for later training and evaluation.

### Phase 1 --- Stronger evaluation

-   Add deterministic scene seeds and repeatable initial states.
-   Add ground-truth object pose, grasp state, contact and collision
    data where supported.
-   Add task-specific tolerances and geometric predicates.
-   Track action count, wall-clock duration, model/API calls and cost.
-   Add video replay with event markers.
-   Add repeat trials and confidence intervals.
-   Compare robots, model versions, prompt variants and scene
    difficulty.

### Phase 2 --- Better scenario generation

-   Randomise object positions, orientations and distractors.
-   Vary object counts, target locations and clutter.
-   Add occlusion and reachability constraints.
-   Create protected-object and negative-constraint tasks.
-   Generate task variants from templates.
-   Store seeds so a failed scenario can be reproduced.

### Phase 3 --- Better diagnostics

-   Categorise failures: perception, object selection, planning, grasp,
    motion, placement, recovery or timeout.
-   Detect repeated actions and stalled progress.
-   Compare intended versus actual object trajectories.
-   Distinguish perception mistakes from manipulation mistakes.
-   Add confidence and evidence coverage to every report.

### Phase 4 --- Training-data preparation (future)

-   Save aligned task instructions, camera observations, robot states
    and action sequences where permitted.
-   Label successful, failed and disturbed-object episodes.
-   Curate failure cases for targeted fine-tuning or policy evaluation.
-   Keep train/validation/test scenarios separate.
-   Prevent evaluation scenes from leaking into training data.
-   Compare the trained policy against the same frozen benchmark.

The key principle is: **first build a trustworthy test and measurement
pipeline; then use its evidence to decide what should be trained.**

------------------------------------------------------------------------

## 17. Final MVP definition of done

The sprint is successful when:

-   [ ] Website opens and connects to the backend.
-   [ ] User can select a supported scene and robot.
-   [ ] User can configure supported objects and their positions.
-   [ ] User can create at least two different tasks in one batch.
-   [ ] Batch executes through the confirmed Dream Labs CLI/client
    integration.
-   [ ] Website shows live task progress.
-   [ ] Initial/final evidence is attached to the correct task.
-   [ ] Evaluator verifies at least one task-specific success condition.
-   [ ] Evaluator records at least one disturbance/penalty case, if
    reproducible.
-   [ ] Every task receives an individual report and score or an
    explicit `NOT_EVALUABLE` result.
-   [ ] Batch receives an aggregate report and score coverage.
-   [ ] The team can demonstrate the complete workflow in 3--5 minutes.
-   [ ] Mocked features and integration limitations are clearly
    disclosed.

## 18. Official references

-   Dream Labs Python SDK/CLI repository:
    https://github.com/dreamlabs-co/dreamlabs
-   Dream Labs website and Playground: https://dreamlabs.ai/
-   Dream-1 research and API overview:
    https://dreamlabs.ai/research/dream-1/
-   Dream Labs platform: https://platform.dreamlabs.ai/

The repository's public quick guide documents `pip install dreamlabs`,
`dreamcli setup`, `dreamcli robot up`, and the `dream.DreamClient`
custom-control-loop route. Confirm exact installed package versions and
command behaviour against the current official documentation before
implementation.

------------------------------------------------------------------------

## 19. Team execution rule

**Do not wait for another person to finish before integrating.**

-   Deniston publishes the UI/API contract and a working frontend shell
    early.
-   Ashiq publishes a working execution adapter and event examples
    early.
-   Umar publishes the scoring function and report schema using fixtures
    early.
-   All three integrate at the checkpoints above.
-   One person owns each module, but all three participate in the final
    end-to-end test.

The next implementation stage will be Claude-assisted development, one
person at a time. When requested, generate Deniston's Claude prompts
first, broken into sequential steps. After Deniston's work is completed
or reviewed, generate Umar's prompts when his name is requested,
followed by Ashiq's prompts when his name is requested. Each prompt
should use the actual repository state and the agreed interface contract
rather than assuming code that has not been built.

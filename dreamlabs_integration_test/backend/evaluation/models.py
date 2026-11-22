from typing import Any, Literal

from pydantic import BaseModel, Field

Outcome = Literal["SUCCESS", "PARTIAL_SUCCESS", "FAILURE", "UNSAFE", "NOT_EVALUABLE"]
CheckStatus = Literal["PASS", "PARTIAL", "FAIL", "NOT_MEASURED"]


class ObjectState(BaseModel):
    object_id: str
    position: list[float] | None = None
    orientation_wxyz: list[float] | None = None
    movable: bool = True
    aabb_min: list[float] | None = None
    aabb_max: list[float] | None = None
    speed_m_s: float | None = None


class Environment(BaseModel):
    table_bounds: list[float] | None = None
    table_z: float | None = None


class Snapshot(BaseModel):
    stage: Literal["initial", "intermediate", "final"]
    sim_time: float | None = None
    objects: list[ObjectState] = Field(default_factory=list)
    robot_state: dict[str, list[float]] = Field(default_factory=dict)
    environment: Environment = Field(default_factory=Environment)
    capture_source: str = "simulator"

    def get(self, object_id: str | None) -> ObjectState | None:
        return next((item for item in self.objects if item.object_id == object_id), None)


class ContactEvent(BaseModel):
    a: str
    b: str
    start_s: float
    end_s: float
    samples: int = 1


class Criterion(BaseModel):
    type: str
    object_id: str
    destination_id: str | None = None
    direction: str | None = None
    upright: bool = False
    angle_deg: float | None = None


class TaskDefinition(BaseModel):
    task_id: str
    batch_id: str
    scene_id: str
    robot_id: str
    instruction: str
    criteria: list[Criterion] = Field(default_factory=list)
    protected_objects: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    max_duration_seconds: float
    weight: float = 1
    started_at: float | None = None
    run_id: str | None = None
    attempt: int = 1
    where: Literal["live", "hidden"] = "live"
    scene_number: int | None = None
    scene_label: str | None = None
    refinement_of: str | None = None
    source_attempt: int | None = None
    test_kind: str | None = None
    test_change: str | None = None
    test_suggestion: str | None = None

    @property
    def run(self) -> str:
        return self.run_id or self.task_id


class Evidence(BaseModel):
    task_id: str
    execution_status: str
    source: str = "simulator"
    initial_snapshot: Snapshot | None = None
    final_snapshot: Snapshot | None = None
    contacts: list[ContactEvent] | None = None
    final_contacts: list[list[str]] | None = None
    trajectory: dict[str, list[list[float]]] = Field(default_factory=dict)
    max_heights: dict[str, float] = Field(default_factory=dict)
    duration_seconds: float | None = None
    decision_seconds: float | None = None
    wait_seconds: float | None = None
    wall_seconds: float | None = None
    action_count: int | None = None
    model_calls: int | None = None
    errors: list[str] = Field(default_factory=list)
    videos: list[str] = Field(default_factory=list)
    run_videos: list[str] = Field(default_factory=list)
    approach: dict[str, Any] | None = None
    model_output_keys: list[str] = Field(default_factory=list)
    frames: dict[str, str] = Field(default_factory=dict)
    visible_pixels: dict[str, dict[str, int]] = Field(default_factory=dict)
    vision: dict[str, Any] | None = None  # the earlier OWLv2 check, kept so saved reports still show it
    video_judge: dict[str, Any] | None = None
    hidden_attempts: list[dict[str, Any]] = Field(default_factory=list)


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str
    value: float | None = None


class Verification(BaseModel):
    criterion: Criterion
    status: CheckStatus
    fraction: float | None
    checks: list[Check]


class Incident(BaseModel):
    incident_id: str
    type: str
    object_id: str
    severity: Literal["MINOR", "MAJOR", "CRITICAL"]
    penalty: float
    evidence: dict[str, Any]
    explanation: str
    confidence: Literal["simulator_ground_truth", "sampled_contact"] = "simulator_ground_truth"


class Component(BaseModel):
    status: Literal["MEASURED", "NOT_MEASURED"]
    weight: float
    effective_weight: float
    base_points: float | None
    penalty_points: float = 0
    points: float | None
    detail: str


class TaskResult(BaseModel):
    task_id: str
    batch_id: str
    outcome: Outcome
    score: float | None
    base_score: float | None
    total_penalties: float
    coverage: float
    config_version: str
    caps_applied: list[str]
    components: dict[str, Component]
    verification: list[Verification]
    scene_understanding: dict[str, dict[str, Any]]
    interactions: dict[str, int]
    incidents: list[Incident]
    missing_evidence: list[str]
    summary: str
    went_well: list[str]
    went_wrong: list[str]
    recommendations: list[str]

from pydantic import BaseModel, Field


class ScoringConfig(BaseModel):
    version: str = "eval-1.0"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "goal_completion": 40,
            "object_correctness": 20,
            "scene_preservation": 15,
            "interaction_quality": 15,
            "efficiency": 10,
        }
    )
    penalties: dict[str, float] = Field(
        default_factory=lambda: {
            "MINOR_DISPLACEMENT": 2,
            "NOTICEABLE_DISPLACEMENT": 5,
            "PROTECTED_MOVED": 8,
            "OBJECT_TIPPED": 10,
            "OBJECT_OFF_TABLE": 15,
            "PROTECTED_COLLISION": 5,
        }
    )
    timeout_cap: float = 50
    unsafe_cap: float = 20
    minor_displacement_m: float = 0.02
    noticeable_displacement_m: float = 0.05
    protected_position_m: float = 0.015
    protected_rotation_deg: float = 15
    tipped_deg: float = 45
    off_table_drop_m: float = 0.03
    lift_m: float = 0.03
    stable_speed_m_s: float = 0.03
    relocation_min_m: float = 0.05
    rotation_tolerance_deg: float = 15
    stack_vertical_tolerance_m: float = 0.02
    container_radii_m: dict[str, float] = Field(default_factory=lambda: {"bowl": 0.08, "plate": 0.07})
    efficient_time_fraction: float = 0.5
    unnecessary_contact_cost: float = 0.1
    drop_cost: float = 0.25


DEFAULT_CONFIG = ScoringConfig()

"""Canonical data contracts for the Lite Campaign workflow."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class CampaignStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_FAILURES = "COMPLETED_WITH_FAILURES"
    COMPLETED_WITH_UNRESOLVED = "COMPLETED_WITH_UNRESOLVED"
    BLOCKED = "BLOCKED"


class ScenarioCoverage(str, Enum):
    OPEN = "OPEN"
    COVERED = "COVERED"
    FAILED = "FAILED"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"


class RubricCriterion(StrictModel):
    criterion_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    observable_behavior: str = Field(min_length=1)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    expected_behavior: str = Field(min_length=1)
    prohibited_behavior: str = Field(min_length=1)
    evidence_required: list[str] = Field(min_length=1)


class TestScenario(StrictModel):
    scenario_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: int = Field(ge=1)
    actor_goal: str = Field(min_length=1)
    user_persona: str = Field(min_length=1)
    starting_state: dict[str, str | int | float | bool]
    user_actions: list[str] = Field(min_length=1)
    evidence_to_capture: list[str] = Field(min_length=1)
    criterion_ids: list[str] = Field(min_length=1)
    recommended_actor: str = "simulated"


class CampaignSpec(StrictModel):
    who: str = Field(min_length=1)
    what: str = Field(min_length=1)
    where: str = Field(min_length=1)
    when: str = Field(min_length=1)
    why: str = Field(min_length=1)
    how: str = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    max_tests: int = Field(ge=1, le=10)
    max_turns_per_test: int = Field(ge=1, le=10)
    scenarios: list[TestScenario] = Field(min_length=1)
    rubric: list[RubricCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "CampaignSpec":
        scenario_ids = [item.scenario_id for item in self.scenarios]
        criterion_ids = [item.criterion_id for item in self.rubric]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario_id values must be unique")
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion_id values must be unique")
        unknown = {item.scenario_id for item in self.rubric} - set(scenario_ids)
        if unknown:
            raise ValueError(f"rubric references unknown scenarios: {sorted(unknown)}")
        for scenario in self.scenarios:
            missing = set(scenario.criterion_ids) - set(criterion_ids)
            if missing:
                raise ValueError(f"{scenario.scenario_id} references unknown criteria: {sorted(missing)}")
        return self


class ArchonRationale(StrictModel):
    selected_scenario_id: str
    rubric_coverage: list[str]
    risk_investigated: str
    missing_evidence: list[str]
    priority_rationale: str
    deferred_scenarios: list[str]


class TestTask(StrictModel):
    task_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    actor_adapter: Literal["simulated"] = "simulated"
    goal: str = Field(min_length=1)
    user_persona: str = Field(min_length=1)
    target_description: str = Field(min_length=1)
    starting_state: dict[str, str | int | float | bool]
    user_actions: list[str] = Field(min_length=1)
    max_turns: int = Field(ge=1, le=10)
    evidence_to_capture: list[str] = Field(min_length=1)
    criterion_ids: list[str] = Field(min_length=1)
    grading_context: str = Field(min_length=1)
    allow_repeat: bool = False

    def actor_view(self) -> dict:
        """Return only fields allowed to cross the Actor isolation boundary."""

        return {
            "task_id": self.task_id,
            "scenario_id": self.scenario_id,
            "actor_adapter": self.actor_adapter,
            "goal": self.goal,
            "user_persona": self.user_persona,
            "target_description": self.target_description,
            "starting_state": self.starting_state,
            "user_actions": self.user_actions,
            "max_turns": self.max_turns,
            "evidence_to_capture": self.evidence_to_capture,
        }


class CampaignPlan(StrictModel):
    campaign: CampaignSpec
    first_task: TestTask
    rationale: ArchonRationale
    normalization_notes: list[str] = Field(default_factory=list)


class ConversationTurn(StrictModel):
    speaker: Literal["actor", "feature"]
    content: str = Field(min_length=1)


class TestRecord(StrictModel):
    record_id: str
    campaign_id: str
    task_id: str
    scenario_id: str
    actor_adapter: Literal["simulated"]
    transcript: list[ConversationTurn] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    execution_status: Literal["completed", "blocked", "limit_reached"]
    termination_reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    simulation_disclosure: bool


class CriterionResult(StrictModel):
    criterion_id: str
    result: Verdict
    evidence_citations: list[str] = Field(default_factory=list)
    explanation: str


class JudgeEvaluation(StrictModel):
    evaluation_id: str
    record_id: str
    scenario_id: str
    verdict: Verdict
    score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    criteria: list[CriterionResult] = Field(min_length=1)
    reasoning_summary: str
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_follow_up: str | None = None
    framework_notes: list[str] = Field(default_factory=list)


class CoverageEntry(StrictModel):
    scenario_id: str
    status: ScenarioCoverage = ScenarioCoverage.OPEN
    record_ids: list[str] = Field(default_factory=list)
    evaluation_ids: list[str] = Field(default_factory=list)


class ArchonReview(StrictModel):
    coverage_gained: list[str]
    open_risks: list[str]
    evidence_still_missing: list[str]
    recommendation: Literal["CONTINUE", "COMPLETE"]
    rationale: str
    next_task: TestTask | None = None


class ArchonInputRequest(StrictModel):
    """Information DevUI displays while the workflow waits for the user."""

    archon_message: str = Field(description="The Archon's latest response")
    conversation: str = Field(description="Conversation retained for the next turn")


class ArchonInputResponse(StrictModel):
    """The single field the user fills in to continue Campaign intake."""

    message: str = Field(min_length=1, description="Reply to the Archon, or say 'run it' to begin testing")

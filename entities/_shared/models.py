"""Canonical data contracts for the Lite Campaign workflow."""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Verdict(str, Enum):
    PASS="PASS"; FAIL="FAIL"; INCONCLUSIVE="INCONCLUSIVE"

class CampaignStatus(str, Enum):
    RUNNING="RUNNING"; COMPLETED="COMPLETED"; COMPLETED_WITH_FAILURES="COMPLETED_WITH_FAILURES"
    COMPLETED_WITH_UNRESOLVED="COMPLETED_WITH_UNRESOLVED"; BLOCKED="BLOCKED"

class ExecutionCoverage(str, Enum):
    NOT_RUN="NOT_RUN"; EXECUTED="EXECUTED"; BLOCKED="BLOCKED"

class EvidenceCoverage(str, Enum):
    NONE="NONE"; SUFFICIENT="SUFFICIENT"; INSUFFICIENT="INSUFFICIENT"

class CampaignRequirement(StrictModel):
    requirement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: Literal["user", "clarified"] = "user"

class RubricCriterion(StrictModel):
    criterion_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    observable_behavior: str = Field(min_length=1)
    severity: Literal["LOW","MEDIUM","HIGH","CRITICAL"]
    expected_behavior: str = Field(min_length=1)
    prohibited_behavior: str = Field(min_length=1)
    evidence_required: list[str] = Field(min_length=1)
    required_evidence_values: list[str] = Field(min_length=1)

class TestScenario(StrictModel):
    scenario_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: int = Field(ge=1)
    requirement_ids: list[str] = Field(min_length=1)
    actor_goal: str = Field(min_length=1)
    user_persona: str = Field(min_length=1)
    starting_state: dict[str, str|int|float|bool] = Field(min_length=1)
    actor_actions: list[str] = Field(min_length=1)
    evidence_to_capture: list[str] = Field(min_length=1)
    criterion_ids: list[str] = Field(min_length=1)
    recommended_actor: Literal["simulated"] = "simulated"

class CampaignSpec(StrictModel):
    who: str = Field(min_length=1); what: str = Field(min_length=1); where: str = Field(min_length=1)
    when: str = Field(min_length=1); why: str = Field(min_length=1); how: str = Field(min_length=1)
    authoritative_requirements: list[CampaignRequirement] = Field(min_length=1)
    unresolved_ambiguities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    max_tests: int = Field(ge=1,le=10); max_turns_per_test: int = Field(ge=1,le=10)
    scenarios: list[TestScenario] = Field(min_length=1)
    rubric: list[RubricCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self):
        sids=[x.scenario_id for x in self.scenarios]; cids=[x.criterion_id for x in self.rubric]
        rids=[x.requirement_id for x in self.authoritative_requirements]
        if any(len(x)!=len(set(x)) for x in (sids,cids,rids)): raise ValueError("all IDs must be unique")
        ss,cs,rs=set(sids),set(cids),set(rids)
        if {x.scenario_id for x in self.rubric}-ss: raise ValueError("rubric references unknown scenarios")
        for x in self.scenarios:
            if set(x.criterion_ids)-cs: raise ValueError(f"{x.scenario_id} references unknown criteria")
            if set(x.requirement_ids)-rs: raise ValueError(f"{x.scenario_id} references unknown requirements")
        for x in self.rubric:
            if set(x.requirement_ids)-rs: raise ValueError(f"{x.criterion_id} references unknown requirements")
        sc={v for x in self.scenarios for v in x.requirement_ids}; rc={v for x in self.rubric for v in x.requirement_ids}
        if rs-sc: raise ValueError(f"requirements without scenarios: {sorted(rs-sc)}")
        if rs-rc: raise ValueError(f"requirements without rubric: {sorted(rs-rc)}")
        return self

class ArchonRationale(StrictModel):
    selected_scenario_id: str; rubric_coverage: list[str]; requirement_coverage: list[str]
    risk_investigated: str; missing_evidence: list[str]; priority_rationale: str; deferred_scenarios: list[str]

class TestTask(StrictModel):
    task_id: str = Field(min_length=1); scenario_id: str = Field(min_length=1)
    actor_adapter: Literal["simulated"]="simulated"; goal: str = Field(min_length=1)
    user_persona: str = Field(min_length=1); target_description: str = Field(min_length=1)
    starting_state: dict[str,str|int|float|bool] = Field(min_length=1)
    actor_actions: list[str] = Field(min_length=1); max_turns: int = Field(ge=1,le=10)
    evidence_to_capture: list[str] = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1); criterion_ids: list[str] = Field(min_length=1)
    grading_context: str = Field(min_length=1); allow_repeat: bool=False
    def actor_view(self):
        return {"task_id":self.task_id,"scenario_id":self.scenario_id,"goal":self.goal,
                "user_persona":self.user_persona,"starting_state":self.starting_state,
                "actor_actions":self.actor_actions,"max_turns":self.max_turns}

class CampaignPlan(StrictModel):
    campaign: CampaignSpec; first_task: TestTask; rationale: ArchonRationale
    normalization_notes: list[str] = Field(default_factory=list)

class ActorPlan(StrictModel):
    task_id: str; scenario_id: str; actor_messages: list[str] = Field(min_length=1)
    strategy_summary: str = Field(min_length=1)

class TargetSimulation(StrictModel):
    task_id: str; scenario_id: str; feature_responses: list[str] = Field(min_length=1)
    observations: list[str] = Field(default_factory=list)
    execution_status: Literal["completed","blocked","limit_reached"]
    termination_reason: str = Field(min_length=1)

class ConversationTurn(StrictModel):
    speaker: Literal["actor","feature"]; content: str = Field(min_length=1)

class TestRecord(StrictModel):
    record_id: str; campaign_id: str; task_id: str; scenario_id: str
    actor_adapter: Literal["simulated"]; starting_state: dict[str,str|int|float|bool] = Field(min_length=1)
    transcript: list[ConversationTurn] = Field(min_length=2); observations: list[str] = Field(default_factory=list)
    execution_status: Literal["completed","blocked","limit_reached"]
    termination_reason: str = Field(min_length=1); evidence: list[str] = Field(default_factory=list)
    simulation_disclosure: bool

class CriterionResult(StrictModel):
    criterion_id: str; result: Verdict; evidence_citations: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

class JudgeEvaluation(StrictModel):
    evaluation_id: str; record_id: str; scenario_id: str; verdict: Verdict
    score: int = Field(ge=0,le=100); confidence: Literal["low","medium","high"]
    criteria: list[CriterionResult] = Field(min_length=1); reasoning_summary: str = Field(min_length=1)
    missing_evidence: list[str] = Field(default_factory=list); recommended_follow_up: str|None=None
    framework_notes: list[str] = Field(default_factory=list)

class CoverageEntry(StrictModel):
    scenario_id: str; execution: ExecutionCoverage=ExecutionCoverage.NOT_RUN
    evidence: EvidenceCoverage=EvidenceCoverage.NONE; verdict: Verdict|None=None
    record_ids: list[str] = Field(default_factory=list); evaluation_ids: list[str] = Field(default_factory=list)

class ArchonReview(StrictModel):
    coverage_gained: list[str]; open_risks: list[str]; evidence_still_missing: list[str]
    recommendation: Literal["CONTINUE","COMPLETE"]; rationale: str; next_task: TestTask|None=None

class ArchonInputRequest(StrictModel):
    archon_message: str = Field(description="The Archon's latest response")
    conversation: str = Field(description="Conversation retained for the next turn")

class ArchonInputResponse(StrictModel):
    message: str = Field(min_length=1,description="Reply to the Archon, or say 'run it' to begin testing")

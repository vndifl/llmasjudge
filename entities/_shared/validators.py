"""Deterministic validation and canonicalization rules."""

from __future__ import annotations

import re

from .models import (
    CampaignPlan,
    CampaignSpec,
    CoverageEntry,
    CriterionResult,
    JudgeEvaluation,
    ScenarioCoverage,
    TestRecord,
    TestTask,
    Verdict,
)


PLACEHOLDER_PATTERNS = (
    r"^\s*\(.*\)\s*$",
    r"no specific task",
    r"no task",
    r"goal, persona, target",
    r"if (?:criteria|eligible|successful)",
    r"potential application",
    r"the (?:assistant|feature) should",
    r"expected to",
)


def _is_placeholder(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in PLACEHOLDER_PATTERNS)


def validate_plan(plan: CampaignPlan) -> None:
    campaign = plan.campaign
    if campaign.max_tests < len(campaign.scenarios):
        raise ValueError("max_tests cannot be smaller than the required scenario count")
    validate_task(plan.first_task, campaign, {})


def validate_task(task: TestTask, campaign: CampaignSpec, coverage: dict[str, dict]) -> None:
    scenarios = {item.scenario_id: item for item in campaign.scenarios}
    if task.scenario_id not in scenarios:
        raise ValueError(f"task references unknown scenario {task.scenario_id}")
    scenario = scenarios[task.scenario_id]
    if set(task.criterion_ids) != set(scenario.criterion_ids):
        raise ValueError("task criterion_ids do not match the scenario")
    if _is_placeholder(task.goal) or _is_placeholder(task.target_description):
        raise ValueError("task contains placeholder language")
    if any(_is_placeholder(item) for item in task.user_actions):
        raise ValueError("task user_actions contain placeholder language")
    current = coverage.get(task.scenario_id, {})
    if current.get("status") in {ScenarioCoverage.COVERED.value, ScenarioCoverage.FAILED.value} and not task.allow_repeat:
        raise ValueError("task repeats an already covered scenario")


def fallback_task(campaign: CampaignSpec, scenario_id: str, sequence: int) -> TestTask:
    scenario = next(item for item in campaign.scenarios if item.scenario_id == scenario_id)
    criteria = [item for item in campaign.rubric if item.criterion_id in scenario.criterion_ids]
    grading = "\n".join(
        f"{item.criterion_id}: expected={item.expected_behavior}; prohibited={item.prohibited_behavior}; "
        f"evidence={'; '.join(item.evidence_required)}"
        for item in criteria
    )
    return TestTask(
        task_id=f"TASK-{scenario_id}-{sequence:02d}",
        scenario_id=scenario_id,
        goal=scenario.actor_goal,
        user_persona=scenario.user_persona,
        target_description=campaign.where,
        starting_state=scenario.starting_state,
        user_actions=scenario.user_actions,
        max_turns=campaign.max_turns_per_test,
        evidence_to_capture=scenario.evidence_to_capture,
        criterion_ids=scenario.criterion_ids,
        grading_context=grading,
    )


def validate_record(record: TestRecord, task: TestTask, campaign_id: str) -> None:
    if record.campaign_id != campaign_id or record.task_id != task.task_id or record.scenario_id != task.scenario_id:
        raise ValueError("record identifiers do not match the assigned task")
    if not record.simulation_disclosure:
        raise ValueError("simulated records require simulation_disclosure=true")
    if record.execution_status == "completed":
        actor_turns = [turn for turn in record.transcript if turn.speaker == "actor"]
        feature_turns = [turn for turn in record.transcript if turn.speaker == "feature"]
        if not actor_turns or not feature_turns:
            raise ValueError("completed record requires Actor and feature transcript turns")
        if any(_is_placeholder(turn.content) for turn in feature_turns):
            raise ValueError("feature response contains expected or placeholder language, not observation")


def canonicalize_evaluation(
    evaluation: JudgeEvaluation,
    record: TestRecord,
    task: TestTask,
) -> JudgeEvaluation:
    notes = list(evaluation.framework_notes)
    if evaluation.record_id != record.record_id or evaluation.scenario_id != record.scenario_id:
        raise ValueError("evaluation identifiers do not match the Test Record")
    expected_criteria = set(task.criterion_ids)
    returned_criteria = {item.criterion_id for item in evaluation.criteria}
    if returned_criteria != expected_criteria:
        raise ValueError("evaluation criteria do not match the assigned scenario")

    valid_citations = True
    for result in evaluation.criteria:
        for citation in result.evidence_citations:
            match = re.fullmatch(r"transcript\[(\d+)\]", citation)
            if not match or int(match.group(1)) >= len(record.transcript):
                valid_citations = False

    pass_inconsistent = (
        evaluation.verdict == Verdict.PASS
        and (
            bool(evaluation.missing_evidence)
            or any(item.result != Verdict.PASS for item in evaluation.criteria)
            or not all(item.evidence_citations for item in evaluation.criteria)
            or not valid_citations
            or record.execution_status != "completed"
        )
    )
    if pass_inconsistent:
        notes.append("Framework changed PASS to INCONCLUSIVE because required evidence was missing or invalid.")
        evaluation = evaluation.model_copy(
            update={
                "verdict": Verdict.INCONCLUSIVE,
                "score": 0,
                "framework_notes": notes,
            }
        )
    return evaluation


def new_coverage(campaign: CampaignSpec) -> dict[str, dict]:
    return {
        item.scenario_id: CoverageEntry(scenario_id=item.scenario_id).model_dump(mode="json")
        for item in campaign.scenarios
    }


def apply_coverage(coverage: dict[str, dict], record: TestRecord, evaluation: JudgeEvaluation) -> None:
    entry = CoverageEntry.model_validate(coverage[record.scenario_id])
    entry.record_ids.append(record.record_id)
    entry.evaluation_ids.append(evaluation.evaluation_id)
    if record.execution_status == "blocked":
        entry.status = ScenarioCoverage.BLOCKED
    elif evaluation.verdict == Verdict.PASS:
        entry.status = ScenarioCoverage.COVERED
    elif evaluation.verdict == Verdict.FAIL:
        entry.status = ScenarioCoverage.FAILED
    else:
        entry.status = ScenarioCoverage.UNRESOLVED
    coverage[record.scenario_id] = entry.model_dump(mode="json")


def first_open_scenario(campaign: CampaignSpec, coverage: dict[str, dict]) -> str | None:
    ordered = sorted(campaign.scenarios, key=lambda item: item.priority)
    for scenario in ordered:
        if coverage[scenario.scenario_id]["status"] == ScenarioCoverage.OPEN.value:
            return scenario.scenario_id
    return None

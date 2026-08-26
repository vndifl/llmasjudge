"""Validated Lite Campaign workflow for Agent Framework DevUI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from agent_framework import Case, Default, Executor, Message, WorkflowBuilder, WorkflowContext, handler

from .agents import actor_agent, judge_agent, planning_agent, review_agent
from .json_support import parse_model, validation_message
from .models import (ArchonReview, CampaignPlan, CampaignSpec, CampaignStatus, JudgeEvaluation,
                     ScenarioCoverage, TestRecord, TestTask, Verdict)
from .storage import write_once, write_snapshot, write_text
from .validators import (apply_coverage, canonicalize_evaluation, fallback_task,
                         first_open_scenario, new_coverage, validate_plan,
                         validate_record, validate_task)

DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() in {"1", "true", "yes", "on"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False)


def campaign(state: dict) -> CampaignSpec:
    return CampaignSpec.model_validate(state["campaign"])


def task(state: dict) -> TestTask:
    return TestTask.model_validate(state["current_task"])


def next_task(state: dict) -> TestTask | None:
    scenario_id = first_open_scenario(campaign(state), state["coverage"])
    return (fallback_task(campaign(state), scenario_id, state["valid_test_count"] + 1)
            if scenario_id else None)


async def model_json(agent, prompt: str, model_type, validator=None):
    response = await agent.run(prompt)
    try:
        value = parse_model(response.text, model_type)
        if validator:
            validator(value)
        return value, False
    except Exception as error:
        repair = await agent.run(
            f"{prompt}\n\nINVALID RESPONSE:\n{response.text}\n\nVALIDATION ERRORS:\n"
            f"{validation_message(error)}\nReturn corrected JSON only."
        )
        value = parse_model(repair.text, model_type)
        if validator:
            validator(value)
        return value, True


class Compiler(Executor):
    def __init__(self): super().__init__(id="01-Campaign-Compiler")

    @handler
    async def compile(self, messages: list[Message], ctx: WorkflowContext[dict, str]):
        request = "\n\n".join(message.text for message in messages if message.text).strip()
        if not request:
            raise ValueError("Campaign input is empty")
        plan, repaired = await model_json(planning_agent, request, CampaignPlan, validate_plan)
        limit = max(1, int(os.getenv("LITE_MAX_TESTS", "3")))
        if len(plan.campaign.scenarios) > limit:
            raise ValueError(f"Campaign has {len(plan.campaign.scenarios)} required scenarios but LITE_MAX_TESTS={limit}")
        spec = plan.campaign.model_copy(update={"max_tests": min(plan.campaign.max_tests, limit)})
        campaign_id = f"lite-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        state = {
            "schema_version": "0.3", "campaign_id": campaign_id, "mode": "lite",
            "created_at": now(), "original_request": request,
            "campaign": spec.model_dump(mode="json"),
            "current_task": plan.first_task.model_dump(mode="json"),
            "planner_rationale": plan.rationale.model_dump(mode="json"),
            "normalization_notes": plan.normalization_notes, "coverage": new_coverage(spec),
            "records": [], "evaluations": [], "decisions": [], "errors": [],
            "valid_test_count": 0, "status": CampaignStatus.RUNNING.value, "complete": False,
        }
        write_snapshot(campaign_id, "campaign.json", state)
        await ctx.yield_output(f"# Canonical Campaign Plan{' (repaired)' if repaired else ''}\n\n{dump(plan)}")
        await ctx.send_message(state)


class TaskValidator(Executor):
    def __init__(self): super().__init__(id="02-Task-Validator")

    @handler
    async def check(self, state: dict, ctx: WorkflowContext[dict, str]):
        source = "Archon"
        try:
            accepted = task(state)
            validate_task(accepted, campaign(state), state["coverage"])
        except Exception as error:
            accepted = next_task(state)
            if not accepted:
                raise RuntimeError("No valid task and no open scenario") from error
            source = "deterministic fallback"
            state["current_task"] = accepted.model_dump(mode="json")
            state["errors"].append({"stage": "task_validation", "error": str(error), "recovered": True})
        await ctx.yield_output(f"# Accepted Task\n\nSource: **{source}**\n\n{dump(accepted)}\n\nRubric and grades are excluded from the Actor call.")
        await ctx.send_message(state)


class Actor(Executor):
    def __init__(self): super().__init__(id="03-Simulated-Actor")

    @handler
    async def simulate(self, state: dict, ctx: WorkflowContext[dict, str]):
        number = state["valid_test_count"] + 1
        actor_input = {"record_id": f"{state['campaign_id']}-T{number:02d}",
                       "campaign_id": state["campaign_id"], **task(state).actor_view()}
        response = await actor_agent.run(dump(actor_input))
        state["pending_actor_input"] = actor_input
        state["pending_record_raw"] = response.text
        await ctx.yield_output(f"# Raw Actor Output (untrusted)\n\n```json\n{response.text}\n```")
        await ctx.send_message(state)


class RecordValidator(Executor):
    def __init__(self): super().__init__(id="04-Record-Validator")

    def parse(self, state: dict, raw: str) -> TestRecord:
        record = parse_model(raw, TestRecord)
        assigned = state["pending_actor_input"]
        record = record.model_copy(update={"record_id": assigned["record_id"],
            "campaign_id": assigned["campaign_id"], "task_id": assigned["task_id"],
            "scenario_id": assigned["scenario_id"], "actor_adapter": "simulated",
            "simulation_disclosure": True})
        validate_record(record, task(state), state["campaign_id"])
        return record

    @handler
    async def check(self, state: dict, ctx: WorkflowContext[dict, str]):
        raw = state.pop("pending_record_raw")
        repaired = False
        try:
            record = self.parse(state, raw)
        except Exception as error:
            response = await actor_agent.run(
                f"ASSIGNMENT:\n{dump(state['pending_actor_input'])}\nINVALID OUTPUT:\n{raw}\n"
                f"ERRORS:\n{validation_message(error)}\nReturn corrected TestRecord JSON only."
            )
            repaired = True
            try:
                record = self.parse(state, response.text)
            except Exception as final_error:
                state["errors"].append({"stage": "record_validation", "error": str(final_error), "recovered": False})
                state["status"], state["complete"] = CampaignStatus.BLOCKED.value, True
                await ctx.yield_output("# Record Rejected\n\nTwo invalid Actor outputs. No test slot was consumed; campaign is BLOCKED.")
                await ctx.send_message(state)
                return
        state.pop("pending_actor_input", None)
        state["pending_record"] = record.model_dump(mode="json")
        number = state["valid_test_count"] + 1
        write_once(state["campaign_id"], f"record-{number:02d}.json", state["pending_record"])
        await ctx.yield_output(f"# Validated Record {number}{' (repaired)' if repaired else ''}\n\n{dump(record)}")
        await ctx.send_message(state)


class Judge(Executor):
    def __init__(self): super().__init__(id="05-Judge")

    @handler
    async def evaluate(self, state: dict, ctx: WorkflowContext[dict, str]):
        spec, current = campaign(state), task(state)
        rubric = [item.model_dump(mode="json") for item in spec.rubric if item.criterion_id in current.criterion_ids]
        response = await judge_agent.run(dump({"relevant_rubric": rubric, "immutable_test_record": state["pending_record"]}))
        state["pending_evaluation_raw"] = response.text
        await ctx.yield_output(f"# Raw Judge Output (provisional)\n\n```json\n{response.text}\n```")
        await ctx.send_message(state)


class EvaluationValidator(Executor):
    def __init__(self): super().__init__(id="06-Evaluation-Validator")

    def parse(self, state: dict, raw: str) -> JudgeEvaluation:
        value = parse_model(raw, JudgeEvaluation)
        record = TestRecord.model_validate(state["pending_record"])
        value = value.model_copy(update={"evaluation_id": f"{record.record_id}-EVAL",
            "record_id": record.record_id, "scenario_id": record.scenario_id})
        return canonicalize_evaluation(value, record, task(state))

    @handler
    async def check(self, state: dict, ctx: WorkflowContext[dict, str]):
        raw, repaired = state.pop("pending_evaluation_raw"), False
        try:
            evaluation = self.parse(state, raw)
            if any("changed PASS" in note for note in evaluation.framework_notes):
                raise ValueError("PASS lacked valid direct evidence")
        except Exception as error:
            response = await judge_agent.run(
                f"RECORD:\n{dump(state['pending_record'])}\nINVALID EVALUATION:\n{raw}\n"
                f"ERRORS:\n{validation_message(error)}\nReturn corrected JudgeEvaluation JSON only."
            )
            repaired = True
            try:
                evaluation = self.parse(state, response.text)
            except Exception as final_error:
                record = TestRecord.model_validate(state["pending_record"])
                evaluation = JudgeEvaluation(evaluation_id=f"{record.record_id}-EVAL",
                    record_id=record.record_id, scenario_id=record.scenario_id,
                    verdict=Verdict.INCONCLUSIVE, score=0, confidence="low",
                    criteria=[{"criterion_id": key, "result": Verdict.INCONCLUSIVE,
                               "evidence_citations": [], "explanation": "Judge output failed validation."}
                              for key in task(state).criterion_ids],
                    reasoning_summary="Framework fallback after two invalid Judge responses.",
                    missing_evidence=[str(final_error)], recommended_follow_up=None,
                    framework_notes=["Canonical INCONCLUSIVE created by validator."])
                state["errors"].append({"stage": "evaluation_validation", "error": str(final_error), "recovered": True})
        state["pending_evaluation"] = evaluation.model_dump(mode="json")
        number = state["valid_test_count"] + 1
        write_once(state["campaign_id"], f"evaluation-{number:02d}.json", state["pending_evaluation"])
        await ctx.yield_output(f"# Canonical Evaluation {number}{' (repair attempted)' if repaired else ''}\n\n{dump(evaluation)}")
        await ctx.send_message(state)


class Ledger(Executor):
    def __init__(self): super().__init__(id="07-Coverage-Ledger")

    @handler
    async def update(self, state: dict, ctx: WorkflowContext[dict, str]):
        record = TestRecord.model_validate(state.pop("pending_record"))
        evaluation = JudgeEvaluation.model_validate(state.pop("pending_evaluation"))
        state["records"].append(record.model_dump(mode="json"))
        state["evaluations"].append(evaluation.model_dump(mode="json"))
        state["valid_test_count"] += 1
        apply_coverage(state["coverage"], record, evaluation)
        write_snapshot(state["campaign_id"], "campaign.json", state)
        await ctx.yield_output(f"# Coverage Ledger\n\n{dump(state['coverage'])}")
        await ctx.send_message(state)


class CompletionGate(Executor):
    def __init__(self): super().__init__(id="08-System-Completion-Gate")

    @handler
    async def decide(self, state: dict, ctx: WorkflowContext[dict, str]):
        statuses = {key: value["status"] for key, value in state["coverage"].items()}
        resolved = {ScenarioCoverage.COVERED.value, ScenarioCoverage.FAILED.value}
        if statuses and all(value in resolved for value in statuses.values()):
            state["status"] = (CampaignStatus.COMPLETED_WITH_FAILURES.value
                               if ScenarioCoverage.FAILED.value in statuses.values()
                               else CampaignStatus.COMPLETED.value)
            state["complete"], reason = True, "All required scenarios have canonical verdicts."
        elif statuses and all(value != ScenarioCoverage.OPEN.value for value in statuses.values()):
            state["status"], state["complete"] = CampaignStatus.COMPLETED_WITH_UNRESOLVED.value, True
            reason = "All required scenarios were executed; at least one verdict remains unresolved."
        elif state["valid_test_count"] >= campaign(state).max_tests:
            state["status"], state["complete"] = CampaignStatus.COMPLETED_WITH_UNRESOLVED.value, True
            reason = "Lite limit reached with unresolved or open scenarios."
        else:
            state["status"], state["complete"] = CampaignStatus.RUNNING.value, False
            reason = "Required scenarios remain open or unresolved."
        decision = {"type": "system_completion_gate", "after_test": state["valid_test_count"],
                    "status": state["status"], "complete": state["complete"],
                    "reason": reason, "coverage": statuses, "decided_at": now()}
        state["decisions"].append(decision)
        await ctx.yield_output(f"# System Completion Decision\n\n{dump(decision)}")
        await ctx.send_message(state)


class ArchonReviewExecutor(Executor):
    def __init__(self): super().__init__(id="09-Archon-Review")

    @handler
    async def review(self, state: dict, ctx: WorkflowContext[dict, str]):
        canonical = next_task(state)
        if not canonical:
            state["complete"] = True
            await ctx.send_message(state)
            return
        source = "Archon"
        prompt = dump({"campaign": state["campaign"], "coverage": state["coverage"],
                       "required_next_scenario": canonical.scenario_id,
                       "records": state["records"], "evaluations": state["evaluations"]})
        try:
            review, repaired = await model_json(review_agent, prompt, ArchonReview)
            if review.recommendation != "CONTINUE" or not review.next_task:
                raise ValueError("Archon attempted completion while required scenarios remain")
            validate_task(review.next_task, campaign(state), state["coverage"])
            if review.next_task.scenario_id != canonical.scenario_id:
                raise ValueError("Archon skipped the next required scenario")
            selected, detail = review.next_task, review.model_dump(mode="json")
            if repaired: source += " (repaired)"
        except Exception as error:
            selected, source = canonical, "deterministic fallback"
            detail = {"framework_override": str(error), "next_task": selected.model_dump(mode="json")}
            state["errors"].append({"stage": "archon_review", "error": str(error), "recovered": True})
        state["current_task"] = selected.model_dump(mode="json")
        state["decisions"].append({"type": "archon_review", "source": source, **detail})
        write_snapshot(state["campaign_id"], "campaign.json", state)
        await ctx.yield_output(f"# Archon Review\n\nSource: **{source}**\n\n{dump(detail)}")
        await ctx.send_message(state)


def report_text(state: dict) -> str:
    lines = ["# Completed Lite Campaign Report", "", f"- Campaign ID: `{state['campaign_id']}`",
             f"- Status: **{state['status']}**", f"- Valid tests: {state['valid_test_count']} / {campaign(state).max_tests}",
             "- Actor adapter: simulated", "", "## Coverage ledger", "",
             "| Scenario | Status | Record | Evaluation |", "|---|---|---|---|"]
    for key, entry in state["coverage"].items():
        lines.append(f"| {key} | {entry['status']} | {', '.join(entry['record_ids']) or '—'} | {', '.join(entry['evaluation_ids']) or '—'} |")
    for number, (record, evaluation) in enumerate(zip(state["records"], state["evaluations"], strict=True), 1):
        lines += ["", f"## Test {number}: {record['scenario_id']}", "",
                  f"**Verdict:** {evaluation['verdict']}  ", f"**Score:** {evaluation['score']}  ",
                  f"**Reasoning:** {evaluation['reasoning_summary']}", "", "### Test Record", "```json",
                  dump(record), "```", "", "### Evaluation", "```json", dump(evaluation), "```"]
    if state["errors"]:
        lines += ["", "## Recovered orchestration issues", "```json", dump(state["errors"]), "```"]
    lines += ["", "## Prototype disclosure", "", "The simulated Actor generated both sides of each interaction. This validates the orchestration and evidence pipeline, not a connected production target."]
    return "\n".join(lines)


class Report(Executor):
    def __init__(self): super().__init__(id="10-Completed-Report")

    @handler
    async def complete(self, state: dict, ctx: WorkflowContext[dict, str]):
        if state["status"] == CampaignStatus.RUNNING.value:
            state["status"] = CampaignStatus.BLOCKED.value
        state["completed_at"] = now()
        report = report_text(state)
        write_snapshot(state["campaign_id"], "completed-report.json", state)
        write_text(state["campaign_id"], "completed-report.md", report)
        await ctx.yield_output(report)


compiler, task_validator, actor = Compiler(), TaskValidator(), Actor()
record_validator, judge, evaluation_validator = RecordValidator(), Judge(), EvaluationValidator()
ledger, gate, review, report = Ledger(), CompletionGate(), ArchonReviewExecutor(), Report()

workflow = (WorkflowBuilder(start_executor=compiler, name="lite_testing_campaign",
    description="Validated Lite Campaign with coverage ledger and canonical report.", max_iterations=40,
    output_from=[report], intermediate_output_from=([compiler, task_validator, actor, record_validator,
    judge, evaluation_validator, ledger, gate, review] if DEBUG_MODE else None))
    .add_edge(compiler, task_validator).add_edge(task_validator, actor).add_edge(actor, record_validator)
    .add_switch_case_edge_group(record_validator, [Case(condition=lambda state: not state["complete"], target=judge), Default(target=report)])
    .add_edge(judge, evaluation_validator).add_edge(evaluation_validator, ledger).add_edge(ledger, gate)
    .add_switch_case_edge_group(gate, [Case(condition=lambda state: not state["complete"], target=review), Default(target=report)])
    .add_edge(review, task_validator).build())

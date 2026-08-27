"""Validated Lite Campaign workflow for Agent Framework DevUI."""

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from agent_framework import (Case, Default, Executor, Message, WorkflowBuilder,
                             WorkflowContext, handler, response_handler)

from .agents import actor_agent, judge_agent, planning_agent, review_agent, target_agent
from .json_support import parse_model, validation_message
from .models import (ActorPlan, ArchonInputRequest, ArchonInputResponse, ArchonReview,
                     CampaignPlan, CampaignSpec, CampaignStatus, EvidenceCoverage,
                     ExecutionCoverage, JudgeEvaluation, TargetSimulation, TestRecord,
                     TestTask, Verdict)
from .agents import campaign_archon
from .storage import write_once, write_snapshot, write_text
from .validators import (apply_coverage, canonicalize_evaluation, fallback_task,
                         first_open_scenario, new_coverage, validate_plan,
                         validate_actor_plan, validate_record, validate_target, validate_task)

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
        return value, {"attempts": 1, "accepted_after_retry": False, "first_error": None}
    except Exception as error:
        repair = await agent.run(
            f"{prompt}\n\nINVALID RESPONSE:\n{response.text}\n\nVALIDATION ERRORS:\n"
            f"{validation_message(error)}\nReturn corrected JSON only."
        )
        value = parse_model(repair.text, model_type)
        if validator:
            validator(value)
        return value, {"attempts": 2, "accepted_after_retry": True, "first_error": validation_message(error)}


def approved_to_run(text: str) -> bool:
    """Recognize explicit execution authorization without treating discussion as approval."""
    normalized = " ".join(text.lower().strip().split())
    if "approved lite campaign" in normalized:
        return True
    commands = (
        r"^(?:yes[, ]+)?(?:run|start|execute)(?: it| the tests?| the campaign)?[.!]?$",
        r"^(?:yes[, ]+)?(?:go|move) forward(?: with (?:it|the tests?|the campaign))?[.!]?$",
        r"^(?:yes[, ]+)?proceed(?: with (?:it|the tests?|the campaign))?[.!]?$",
        r"^(?:i )?approve(?: it| the campaign)?[.!]?$",
    )
    import re
    return any(re.fullmatch(pattern, normalized) for pattern in commands)


class ConversationalArchon(Executor):
    """Discuss the Campaign, pause for replies, then hand it directly to execution."""
    def __init__(self): super().__init__(id="00-Campaign-Archon")

    async def continue_conversation(self, conversation: str, user_message: str,
                                    ctx: WorkflowContext[list[Message], str]):
        updated = f"{conversation}\n\nUSER:\n{user_message}".strip()
        direct_campaign = "approved lite campaign" in user_message.lower()
        if direct_campaign:
            await ctx.yield_output("# Campaign Approved\n\nStarting the validated Lite Campaign automatically.")
            await ctx.send_message([Message("user", [user_message])])
            return
        prompt = (
            "Continue this Campaign-design conversation. Respond to the latest USER message. "
            "If the latest message explicitly authorizes execution (for example run it, proceed, "
            "or start the tests), return a complete self-contained block headed APPROVED LITE CAMPAIGN.\n\n"
            f"CONVERSATION:\n{updated}"
        )
        response = await campaign_archon.run(prompt)
        archon_text = response.text
        updated = f"{updated}\n\nARCHON:\n{archon_text}"
        await ctx.yield_output(f"# Archon\n\n{archon_text}")
        if approved_to_run(user_message):
            submission = archon_text if "approved lite campaign" in archon_text.lower() else updated
            await ctx.yield_output("# Campaign Approved\n\nHanding the Campaign directly to the compiler.")
            await ctx.send_message([Message("user", [submission])])
        else:
            await ctx.request_info(
                ArchonInputRequest(archon_message=archon_text, conversation=updated),
                ArchonInputResponse,
            )

    @handler
    async def begin(self, messages: list[Message], ctx: WorkflowContext[list[Message], str]):
        user_message = "\n\n".join(message.text for message in messages if message.text).strip()
        if not user_message:
            raise ValueError("Campaign input is empty")
        await self.continue_conversation("", user_message, ctx)

    @response_handler
    async def resume(self, original_request: ArchonInputRequest,
                     response: ArchonInputResponse,
                     ctx: WorkflowContext[list[Message], str]):
        await self.continue_conversation(original_request.conversation, response.message, ctx)


class Compiler(Executor):
    def __init__(self): super().__init__(id="01-Campaign-Compiler")

    @handler
    async def compile(self, messages: list[Message], ctx: WorkflowContext[dict, str]):
        request = "\n\n".join(message.text for message in messages if message.text).strip()
        if not request:
            raise ValueError("Campaign input is empty")
        plan, validation = await model_json(planning_agent, request, CampaignPlan, validate_plan)
        limit = max(1, int(os.getenv("LITE_MAX_TESTS", "3")))
        if len(plan.campaign.scenarios) > limit:
            raise ValueError(f"Campaign has {len(plan.campaign.scenarios)} required scenarios but LITE_MAX_TESTS={limit}")
        spec = plan.campaign.model_copy(update={"max_tests": min(plan.campaign.max_tests, limit)})
        campaign_id = f"lite-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        state = {
            "schema_version": "0.4", "campaign_id": campaign_id, "mode": "lite",
            "created_at": now(), "original_request": request,
            "campaign": spec.model_dump(mode="json"),
            "current_task": plan.first_task.model_dump(mode="json"),
            "planner_rationale": plan.rationale.model_dump(mode="json"),
            "normalization_notes": plan.normalization_notes, "coverage": new_coverage(spec),
            "records": [], "evaluations": [], "decisions": [], "errors": [],
            "valid_test_count": 0, "status": CampaignStatus.RUNNING.value, "complete": False,
        }
        write_snapshot(campaign_id, "campaign.json", state)
        status = "Accepted after 1 retry" if validation["accepted_after_retry"] else "Accepted on first attempt"
        detail = f"\n- First rejection: `{validation['first_error']}`" if validation["first_error"] else ""
        await ctx.yield_output(f"# Campaign Plan — Accepted\n\n- Validation: **{status}**\n- Authority: Campaign Gate{detail}\n\n{dump(plan)}")
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
        await ctx.yield_output(f"# Test Task — Accepted\n\n- Proposed by: **{source}**\n- Authority: **Task Gate**\n- Actor boundary: expected behavior, rubric, and grades excluded\n\n{dump(accepted)}")
        await ctx.send_message(state)


class Actor(Executor):
    def __init__(self): super().__init__(id="03-Actor")

    @handler
    async def simulate(self, state: dict, ctx: WorkflowContext[dict, str]):
        current=task(state); response=await actor_agent.run(dump(current.actor_view()))
        try:
            plan=parse_model(response.text,ActorPlan); validate_actor_plan(plan,current); attempts=1; first=None
        except Exception as error:
            first=validation_message(error)
            repaired=await actor_agent.run(f"TASK:\n{dump(current.actor_view())}\nINVALID ACTOR PLAN:\n{response.text}\nERRORS:\n{first}\nReturn corrected ActorPlan JSON only.")
            plan=parse_model(repaired.text,ActorPlan); validate_actor_plan(plan,current); attempts=2
        state["pending_actor_plan"]=plan.model_dump(mode="json")
        await ctx.yield_output(f"# Actor Plan — Accepted\n\n- Role: customer messages only\n- Validation attempts: **{attempts}**"
                               +(f"\n- First rejection: `{first}`" if first else "")+f"\n\n{dump(plan)}")
        await ctx.send_message(state)


class SimulatedTarget(Executor):
    def __init__(self): super().__init__(id="04-Simulated-Target")

    @handler
    async def respond(self,state:dict,ctx:WorkflowContext[dict,str]):
        current=task(state); plan=ActorPlan.model_validate(state["pending_actor_plan"])
        target_input={"task_id":current.task_id,"scenario_id":current.scenario_id,
            "public_feature_context":{"who":campaign(state).who,"what":campaign(state).what,
                                      "where":campaign(state).where},
            "starting_state":current.starting_state,"actor_messages":plan.actor_messages}
        response=await target_agent.run(dump(target_input)); state["pending_target_raw"]=response.text
        await ctx.yield_output("# Simulated Target Proposal — Awaiting Evidence Validation\n\n"
            "This is a model proposal, not yet an accepted Test Record.\n\n```json\n"+response.text+"\n```")
        await ctx.send_message(state)


class RecordValidator(Executor):
    def __init__(self): super().__init__(id="05-Record-Evidence-Gate")

    def parse(self,state:dict,raw:str)->TestRecord:
        current=task(state); plan=ActorPlan.model_validate(state["pending_actor_plan"])
        simulation=parse_model(raw,TargetSimulation); validate_target(simulation,plan,current)
        transcript=[]
        for actor_message,feature_response in zip(plan.actor_messages,simulation.feature_responses,strict=True):
            transcript.extend([{"speaker":"actor","content":actor_message},{"speaker":"feature","content":feature_response}])
        number=state["valid_test_count"]+1
        record=TestRecord(record_id=f"{state['campaign_id']}-T{number:02d}",campaign_id=state["campaign_id"],
            task_id=current.task_id,scenario_id=current.scenario_id,actor_adapter="simulated",
            starting_state=current.starting_state,transcript=transcript,observations=simulation.observations,
            execution_status=simulation.execution_status,termination_reason=simulation.termination_reason,
            evidence=[f"transcript[{i}]" for i in range(len(transcript))],simulation_disclosure=True)
        validate_record(record, task(state), state["campaign_id"])
        return record

    @handler
    async def check(self, state: dict, ctx: WorkflowContext[dict, str]):
        raw=state.pop("pending_target_raw"); first=None; attempts=1
        try:
            record = self.parse(state, raw)
        except Exception as error:
            first=validation_message(error); current=task(state); plan=state["pending_actor_plan"]
            response=await target_agent.run(f"TASK STATE:\n{dump(current.starting_state)}\nACTOR PLAN:\n{dump(plan)}\n"
                f"INVALID TARGET OUTPUT:\n{raw}\nERRORS:\n{first}\nReturn corrected TargetSimulation JSON only.")
            attempts=2
            try:
                record = self.parse(state, response.text)
            except Exception as final_error:
                state["errors"].append({"stage": "record_validation", "error": str(final_error), "recovered": False})
                state["status"], state["complete"] = CampaignStatus.BLOCKED.value, True
                await ctx.yield_output("# Test Record — Rejected\n\n- Authority: **Record Evidence Gate**\n"
                    "- Target attempts: **2**\n- Result: **INVALID EXECUTION**\n- Test slot consumed: **No**\n"
                    f"- Final reason: `{validation_message(final_error)}`")
                await ctx.send_message(state)
                return
        state.pop("pending_actor_plan",None)
        state["pending_record"] = record.model_dump(mode="json")
        number = state["valid_test_count"] + 1
        write_once(state["campaign_id"], f"record-{number:02d}.json", state["pending_record"])
        status="Accepted after 1 retry" if attempts==2 else "Accepted on first attempt"
        await ctx.yield_output(f"# Test Record {number} — Accepted\n\n- Validation: **{status}**\n"
            f"- Authority: **Record Evidence Gate**"+(f"\n- First rejection: `{first}`" if first else "")+f"\n\n{dump(record)}")
        await ctx.send_message(state)


class Judge(Executor):
    def __init__(self): super().__init__(id="06-Judge")

    @handler
    async def evaluate(self, state: dict, ctx: WorkflowContext[dict, str]):
        spec, current = campaign(state), task(state)
        rubric = [item.model_dump(mode="json") for item in spec.rubric if item.criterion_id in current.criterion_ids]
        response = await judge_agent.run(dump({"relevant_rubric": rubric, "immutable_test_record": state["pending_record"]}))
        state["pending_evaluation_raw"] = response.text
        await ctx.yield_output(f"# Judge Proposal — Awaiting Evaluation Validation\n\n"
            "This verdict is proposed by the model and is not final.\n\n```json\n"+response.text+"\n```")
        await ctx.send_message(state)


class EvaluationValidator(Executor):
    def __init__(self): super().__init__(id="07-Evaluation-Gate")

    def parse(self, state: dict, raw: str) -> JudgeEvaluation:
        value = parse_model(raw, JudgeEvaluation)
        record = TestRecord.model_validate(state["pending_record"])
        value = value.model_copy(update={"evaluation_id": f"{record.record_id}-EVAL",
            "record_id": record.record_id, "scenario_id": record.scenario_id})
        spec=campaign(state); relevant=[x for x in spec.rubric if x.criterion_id in task(state).criterion_ids]
        return canonicalize_evaluation(value,record,task(state),relevant)

    @handler
    async def check(self, state: dict, ctx: WorkflowContext[dict, str]):
        raw=state.pop("pending_evaluation_raw"); attempts=1; first=None
        try:
            evaluation = self.parse(state, raw)
            if any("replaced unsupported PASS" in note for note in evaluation.framework_notes):
                raise ValueError("Judge PASS was not supported by required cited evidence")
        except Exception as error:
            first=validation_message(error)
            response = await judge_agent.run(
                f"RECORD:\n{dump(state['pending_record'])}\nINVALID EVALUATION:\n{raw}\n"
                f"ERRORS:\n{validation_message(error)}\nReturn corrected JudgeEvaluation JSON only."
            )
            attempts=2
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
        overridden=bool(evaluation.framework_notes)
        await ctx.yield_output(f"# Final Evaluation {number}\n\n- Judge attempts: **{attempts}**\n"
            f"- Framework override: **{'Yes' if overridden else 'No'}**\n- Authority: **Evaluation Gate**"
            +(f"\n- First rejection: `{first}`" if first else "")+f"\n\n{dump(evaluation)}")
        await ctx.send_message(state)


class Ledger(Executor):
    def __init__(self): super().__init__(id="08-Coverage-Ledger")

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
    def __init__(self): super().__init__(id="09-System-Completion-Gate")

    @handler
    async def decide(self, state: dict, ctx: WorkflowContext[dict, str]):
        entries=state["coverage"]
        all_executed=bool(entries) and all(x["execution"]==ExecutionCoverage.EXECUTED.value for x in entries.values())
        all_sufficient=all(x["evidence"]==EvidenceCoverage.SUFFICIENT.value for x in entries.values())
        verdicts=[x["verdict"] for x in entries.values()]
        if all_executed and all_sufficient:
            state["status"] = (CampaignStatus.COMPLETED_WITH_FAILURES.value
                               if Verdict.FAIL.value in verdicts
                               else CampaignStatus.COMPLETED.value)
            state["complete"], reason = True, "All required scenarios have canonical verdicts."
        elif all_executed:
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
                    "reason": reason, "coverage": entries, "decided_at": now()}
        state["decisions"].append(decision)
        await ctx.yield_output(f"# System Completion Decision\n\n{dump(decision)}")
        await ctx.send_message(state)


class ArchonReviewExecutor(Executor):
    def __init__(self): super().__init__(id="10-Archon-Review")

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
            review, validation = await model_json(review_agent, prompt, ArchonReview)
            if review.recommendation != "CONTINUE" or not review.next_task:
                raise ValueError("Archon attempted completion while required scenarios remain")
            validate_task(review.next_task, campaign(state), state["coverage"])
            if review.next_task.scenario_id != canonical.scenario_id:
                raise ValueError("Archon skipped the next required scenario")
            executed={key for key,value in state["coverage"].items() if value["execution"]==ExecutionCoverage.EXECUTED.value}
            if set(review.coverage_gained)-executed:
                raise ValueError("Archon claimed coverage not present in the system ledger")
            selected, detail = review.next_task, review.model_dump(mode="json")
            if validation["accepted_after_retry"]: source += " — accepted after 1 retry"
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
             "- Actor: isolated customer role", "- Target adapter: simulated", "", "## Coverage ledger", "",
             "| Scenario | Execution | Evidence | Verdict | Record | Evaluation |", "|---|---|---|---|---|---|"]
    for key, entry in state["coverage"].items():
        lines.append(f"| {key} | {entry['execution']} | {entry['evidence']} | {entry['verdict'] or '—'} | "
                     f"{', '.join(entry['record_ids']) or '—'} | {', '.join(entry['evaluation_ids']) or '—'} |")
    for number, (record, evaluation) in enumerate(zip(state["records"], state["evaluations"], strict=True), 1):
        lines += ["", f"## Test {number}: {record['scenario_id']}", "",
                  f"**Verdict:** {evaluation['verdict']}  ", f"**Score:** {evaluation['score']}  ",
                  f"**Reasoning:** {evaluation['reasoning_summary']}", "", "### Test Record", "```json",
                  dump(record), "```", "", "### Evaluation", "```json", dump(evaluation), "```"]
    if state["errors"]:
        lines += ["", "## Recovered orchestration issues", "```json", dump(state["errors"]), "```"]
    lines += ["", "## Prototype disclosure", "", "The Actor generated only customer messages. A separate Simulated Target generated feature responses. This validates orchestration and evidence controls, not a connected production target."]
    return "\n".join(lines)


class Report(Executor):
    def __init__(self): super().__init__(id="11-Completed-Report")

    @handler
    async def complete(self, state: dict, ctx: WorkflowContext[dict, str]):
        if state["status"] == CampaignStatus.RUNNING.value:
            state["status"] = CampaignStatus.BLOCKED.value
        state["completed_at"] = now()
        report = report_text(state)
        write_snapshot(state["campaign_id"], "completed-report.json", state)
        write_text(state["campaign_id"], "completed-report.md", report)
        await ctx.yield_output(report)


conversational_archon = ConversationalArchon()
compiler, task_validator, actor, target = Compiler(), TaskValidator(), Actor(), SimulatedTarget()
record_validator, judge, evaluation_validator = RecordValidator(), Judge(), EvaluationValidator()
ledger, gate, review, report = Ledger(), CompletionGate(), ArchonReviewExecutor(), Report()

workflow = (WorkflowBuilder(start_executor=conversational_archon, name="campaign_archon_testing",
    description="Chat with the Archon, approve, and automatically run a validated Lite Campaign.", max_iterations=50,
    output_from=[report], intermediate_output_from=([conversational_archon, compiler, task_validator, actor, target, record_validator,
    judge, evaluation_validator, ledger, gate, review] if DEBUG_MODE else None))
    .add_edge(conversational_archon, compiler).add_edge(compiler, task_validator).add_edge(task_validator, actor).add_edge(actor, target).add_edge(target, record_validator)
    .add_switch_case_edge_group(record_validator, [Case(condition=lambda state: not state["complete"], target=judge), Default(target=report)])
    .add_edge(judge, evaluation_validator).add_edge(evaluation_validator, ledger).add_edge(ledger, gate)
    .add_switch_case_edge_group(gate, [Case(condition=lambda state: not state["complete"], target=review), Default(target=report)])
    .add_edge(review, task_validator).build())

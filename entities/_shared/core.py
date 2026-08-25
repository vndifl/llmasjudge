"""Inspectable Lite Campaign workflow for Microsoft Agent Framework DevUI.

The simulated Actor is deliberately isolated at the model boundary: it receives
only the task brief selected by the Archon, never the campaign rubric or grades.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from agent_framework import (
    Agent,
    Case,
    Default,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.openai import OpenAIChatCompletionClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"
load_dotenv(PROJECT_ROOT / ".env")
DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() in {"1", "true", "yes", "on"}


def create_model_client() -> OpenAIChatCompletionClient:
    """Create the current provider client; replace this for Foundry later."""

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Copy .env.example to .env, "
            "add your OpenRouter key, and restart DevUI."
        )
    return OpenAIChatCompletionClient(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model=os.getenv("OPENROUTER_MODEL", "openrouter/free"),
    )


client = create_model_client()


campaign_archon = Agent(
    client=client,
    name="Campaign Archon",
    description="Discuss and refine a Lite Test Campaign before execution.",
    instructions="""
You are the conversational Archon (Test Director). Help the user create a Lite
Test Campaign. Gather or responsibly infer the Campaign 5W1H: Who, What, Where,
When, Why, and How. Build a rubric with observable criteria, severity, expected
behavior, prohibited behavior, and evidence required.

Lite defaults: at most 3 executed tests, one Actor attempt per test, at most 3
simulated interaction turns, sequential execution, and early completion when
required rubric coverage is sufficient. Ask only questions whose answers would
materially change testing. Summarize assumptions visibly. Do not execute tests.

When the user approves or asks to proceed, return a self-contained block headed
APPROVED LITE CAMPAIGN. Include 5W1H, rubric, limits, stopping criteria, target
interface, assumptions, and known constraints. The user will paste that block
into the Lite Testing Campaign workflow.
""".strip(),
)


planning_agent = Agent(
    client=client,
    name="Archon Planner",
    instructions="""
You are the Archon managing a Lite Test Campaign. Normalize the supplied
campaign, preserve explicit user requirements, create an observable rubric,
and produce a prioritized candidate Test Plan. Then select the highest-value
first test. Do not execute or grade it.

Return these exact sections:
CAMPAIGN SPECIFICATION
WHO:
WHAT:
WHERE:
WHEN:
WHY:
HOW:
ASSUMPTIONS:
LITE LIMITS:

RUBRIC
(numbered criteria with severity, expected behavior, prohibited behavior, and evidence required)

CANDIDATE TEST PLAN
(prioritized tests, rubric coverage, risk, and recommended Actor adapter)

ARCHON DECISION LOG
SELECTED TEST:
RUBRIC COVERAGE:
RISK INVESTIGATED:
MISSING EVIDENCE:
PRIORITY RATIONALE:
DEFERRED CANDIDATES:
RECOMMENDED ACTOR: simulated

RELEVANT GRADING CONTEXT:
(only the rubric context the Judge needs for this test)
END RELEVANT GRADING CONTEXT

ACTOR TASK BRIEF:
(goal, user persona, target description, starting state, execution limit, and evidence to capture; do not reveal expected behavior or rubric)
END ACTOR TASK BRIEF
""".strip(),
)


actor_agent = Agent(
    client=client,
    name="Simulated Actor",
    instructions="""
You are an isolated Actor executing one black-box task. You receive only an
Actor Task Brief. You do not know the Campaign rubric, expected outcome, or
previous grades. Simulate the target feature and act like the assigned user.
The target response may be correct or flawed; do not deliberately optimize it
to pass a hidden rubric. Record facts, not judgments.

Return exactly:
ASSIGNED TASK:
ACTOR ADAPTER: simulated
ACTIONS:
FEATURE RESPONSES:
OBSERVATIONS:
EXECUTION STATUS: completed, blocked, or limit_reached
TERMINATION REASON:
EVIDENCE:
SIMULATION DISCLOSURE: This record was produced by the simulated Actor adapter.
""".strip(),
)


judge_agent = Agent(
    client=client,
    name="Judge",
    instructions="""
You are an independent evidence Judge. Evaluate the immutable Test Record only
against the supplied relevant grading context. Do not modify the record, invent
evidence, or reward an outcome that cannot be observed.

Return exactly:
VERDICT: PASS, FAIL, or INCONCLUSIVE
SCORE: 0-100
CONFIDENCE: low, medium, or high
CRITERIA RESULTS:
(criterion, result, and cited evidence)
REASONING SUMMARY:
MISSING OR UNCERTAIN EVIDENCE:
RECOMMENDED FOLLOW-UP:
""".strip(),
)


review_agent = Agent(
    client=client,
    name="Archon Review",
    instructions="""
You are the Archon reviewing campaign progress after one judged Test Record.
Use the Campaign, candidate plan, and accumulated report. Decide whether the
Lite stopping criteria are satisfied. If testing should continue, select the
next highest-value test adaptively; it may explore a gap, boundary, failure,
or consistency risk. Never change an existing record or grade.

Return these exact sections:
ARCHON PROGRESS DECISION
COVERAGE GAINED:
OPEN RISKS:
EVIDENCE STILL MISSING:
WHY CONTINUE OR STOP:
DEFERRED TESTS:
CAMPAIGN_STATUS: CONTINUE or COMPLETE

If continuing, also return:
RELEVANT GRADING CONTEXT:
(only the context the Judge needs for the next test)
END RELEVANT GRADING CONTEXT

ACTOR TASK BRIEF:
(goal, persona, target, starting state, limits, and evidence to capture; no rubric or expected result)
END ACTOR TASK BRIEF
""".strip(),
)


campaign_evaluator = Agent(
    client=client,
    name="Campaign Evaluator",
    description="Optionally assess a Completed Report without changing it.",
    instructions="""
Evaluate a Completed Campaign Report collectively. Identify patterns, recurring
failure modes, reliability, strengths, weaknesses, confidence, and unresolved
areas. Do not alter individual records, verdicts, or the underlying report.
Label the result CAMPAIGN ASSESSMENT and distinguish observed evidence from
inference.
""".strip(),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conversation_text(messages: list[Message]) -> str:
    return "\n\n".join(m.text for m in messages if m.text).strip()


def _section(text: str, heading: str) -> str:
    pattern = rf"{re.escape(heading)}:\s*(.*?)\s*END {re.escape(heading)}"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _run_dir(campaign_id: str) -> Path:
    path = RUNS_ROOT / campaign_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_once(path: Path, data: dict[str, Any]) -> None:
    """Write an immutable local artifact; never overwrite an existing record."""

    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True)


def _write_snapshot(path: Path, data: dict[str, Any]) -> None:
    """Write derived campaign state; records themselves use _write_once."""

    path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


class CampaignIntakeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="Campaign-Archon-Plan")

    @handler
    async def plan(self, messages: list[Message], ctx: WorkflowContext[dict, str]) -> None:
        request = _conversation_text(messages)
        if not request:
            raise ValueError("Campaign input is empty.")
        response = await planning_agent.run(request)
        campaign_id = f"lite-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        plan = response.text
        task = _section(plan, "ACTOR TASK BRIEF")
        grading = _section(plan, "RELEVANT GRADING CONTEXT")
        if not task:
            raise ValueError("The Archon did not return an ACTOR TASK BRIEF section.")
        state: dict[str, Any] = {
            "schema_version": "0.2",
            "campaign_id": campaign_id,
            "mode": "lite",
            "created_at": _utc_now(),
            "max_tests": max(1, int(os.getenv("LITE_MAX_TESTS", "3"))),
            "original_request": request,
            "campaign_plan": plan,
            "current_task": task,
            "grading_context": grading,
            "test_count": 0,
            "records": [],
            "evaluations": [],
            "decisions": [],
            "complete": False,
        }
        run_dir = _run_dir(campaign_id)
        _write_snapshot(run_dir / "campaign.json", state)
        await ctx.yield_output(f"# Campaign and Test Plan\n\nCampaign ID: `{campaign_id}`\n\n{plan}")
        await ctx.send_message(state)


class SimulatedActorExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="Simulated-Actor")

    @handler
    async def run_task(self, state: dict, ctx: WorkflowContext[dict, str]) -> None:
        # Actor isolation is enforced here: only current_task crosses the model boundary.
        response = await actor_agent.run(state["current_task"])
        test_number = state["test_count"] + 1
        record = {
            "schema_version": "0.2",
            "record_id": f"{state['campaign_id']}-T{test_number:02d}",
            "campaign_id": state["campaign_id"],
            "test_number": test_number,
            "created_at": _utc_now(),
            "actor_adapter": "simulated",
            "assigned_task": state["current_task"],
            "record": response.text,
        }
        _write_once(_run_dir(state["campaign_id"]) / f"record-{test_number:02d}.json", record)
        state["pending_record"] = record
        await ctx.yield_output(
            f"# Test Record {test_number}\n\nRecord ID: `{record['record_id']}`\n\n{response.text}"
        )
        await ctx.send_message(state)


class JudgeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="Judge-Evaluation")

    @handler
    async def evaluate(self, state: dict, ctx: WorkflowContext[dict, str]) -> None:
        record = state["pending_record"]
        judge_input = (
            "RELEVANT GRADING CONTEXT\n"
            f"{state['grading_context']}\n\n"
            "IMMUTABLE TEST RECORD\n"
            f"{json.dumps(record, indent=2)}"
        )
        response = await judge_agent.run(judge_input)
        evaluation = {
            "schema_version": "0.2",
            "evaluation_id": f"{record['record_id']}-EVAL",
            "record_id": record["record_id"],
            "created_at": _utc_now(),
            "evaluation": response.text,
        }
        _write_once(
            _run_dir(state["campaign_id"]) / f"evaluation-{record['test_number']:02d}.json",
            evaluation,
        )
        state["pending_evaluation"] = evaluation
        await ctx.yield_output(
            f"# Judge Evaluation {record['test_number']}\n\n{response.text}"
        )
        await ctx.send_message(state)


class ArchonReviewExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="Archon-Progress-Review")

    @handler
    async def review(self, state: dict, ctx: WorkflowContext[dict, str]) -> None:
        state["records"].append(state.pop("pending_record"))
        state["evaluations"].append(state.pop("pending_evaluation"))
        state["test_count"] += 1
        review_input = {
            "campaign_plan": state["campaign_plan"],
            "lite_limit": state["max_tests"],
            "tests_completed": state["test_count"],
            "records": state["records"],
            "evaluations": state["evaluations"],
        }
        response = await review_agent.run(json.dumps(review_input, indent=2))
        decision = {
            "test_number": state["test_count"],
            "created_at": _utc_now(),
            "decision": response.text,
        }
        state["decisions"].append(decision)
        model_complete = bool(
            re.search(r"CAMPAIGN_STATUS:\s*COMPLETE", response.text, flags=re.IGNORECASE)
        )
        limit_complete = state["test_count"] >= state["max_tests"]
        state["complete"] = model_complete or limit_complete
        if not state["complete"]:
            task = _section(response.text, "ACTOR TASK BRIEF")
            grading = _section(response.text, "RELEVANT GRADING CONTEXT")
            if not task:
                # A malformed continuation safely ends the campaign instead of leaking context.
                state["complete"] = True
                response_text = response.text + "\n\nSYSTEM NOTE: Campaign ended because no isolated next task was returned."
            else:
                state["current_task"] = task
                state["grading_context"] = grading
                response_text = response.text
        else:
            response_text = response.text
        _write_once(
            _run_dir(state["campaign_id"]) / f"decision-{state['test_count']:02d}.json",
            decision,
        )
        _write_snapshot(_run_dir(state["campaign_id"]) / "campaign.json", state)
        await ctx.yield_output(f"# Archon Progress Review {state['test_count']}\n\n{response_text}")
        await ctx.send_message(state)


class CompletedReportExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="Completed-Report")

    @handler
    async def complete(self, state: dict, ctx: WorkflowContext[dict, str]) -> None:
        lines = [
            "# Completed Lite Campaign Report",
            "",
            f"- Campaign ID: `{state['campaign_id']}`",
            f"- Mode: Lite",
            f"- Tests completed: {state['test_count']} of {state['max_tests']} maximum",
            f"- Started: {state['created_at']}",
            f"- Completed: {_utc_now()}",
            "- Actor adapter: Simulated",
            "",
            "## Campaign, Rubric, and Candidate Test Plan",
            "",
            state["campaign_plan"],
        ]
        for index, (record, evaluation, decision) in enumerate(
            zip(state["records"], state["evaluations"], state["decisions"], strict=True), start=1
        ):
            lines.extend(
                [
                    "",
                    f"## Test {index}",
                    "",
                    f"### Immutable Test Record `{record['record_id']}`",
                    "",
                    record["record"],
                    "",
                    "### Judge Evaluation",
                    "",
                    evaluation["evaluation"],
                    "",
                    "### Archon Progress Decision",
                    "",
                    decision["decision"],
                ]
            )
        lines.extend(
            [
                "",
                "## Prototype Disclosure",
                "",
                "All executions in this report used the simulated Actor adapter. Test Records",
                "describe simulated target behavior and are intended to validate campaign",
                "planning, isolation, judging, and management—not a production target.",
            ]
        )
        report = "\n".join(lines)
        run_dir = _run_dir(state["campaign_id"])
        (run_dir / "completed-report.md").write_text(report, encoding="utf-8")
        _write_snapshot(run_dir / "completed-report.json", state)
        await ctx.yield_output(report)


campaign_intake = CampaignIntakeExecutor()
simulated_actor = SimulatedActorExecutor()
judge = JudgeExecutor()
archon_review = ArchonReviewExecutor()
completed_report = CompletedReportExecutor()

workflow = (
    WorkflowBuilder(
        start_executor=campaign_intake,
        name="lite_testing_campaign",
        description="Adaptive Lite Campaign with isolated records, judging, and reporting.",
        max_iterations=20,
        output_from=[completed_report],
        intermediate_output_from=(
            [campaign_intake, simulated_actor, judge, archon_review] if DEBUG_MODE else None
        ),
    )
    .add_edge(campaign_intake, simulated_actor)
    .add_edge(simulated_actor, judge)
    .add_edge(judge, archon_review)
    .add_switch_case_edge_group(
        archon_review,
        [
            Case(condition=lambda state: not state["complete"], target=simulated_actor),
            Default(target=completed_report),
        ],
    )
    .build()
)

"""Provider and agent definitions for the Lite Campaign workflow."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_model_client() -> OpenAIChatCompletionClient:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Copy .env.example to .env, add your key, and restart DevUI."
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
You are the conversational Archon. Use the framework meanings: Who is the
tested feature; When is the evidence/limits that end the Campaign. Requirements
are not assumptions. Completion depends on judgeable evidence, never PASS
outcomes. FAIL and INCONCLUSIVE still count as executed. Build numbered,
objective rubric criteria. Ask only material questions. When asked to finalize,
return a self-contained APPROVED LITE CAMPAIGN using 5W1H, rubric, limits,
stopping criteria, target, assumptions, and constraints. Never use a Markdown
table and never label rubric criteria as Test Records.
""".strip(),
)


planning_agent = Agent(
    client=client,
    name="Archon Planner",
    instructions="""
Compile the supplied Campaign into canonical JSON. Correct weak drafts: Who is
the tested feature, When is evidence-based completion, requirements are not
assumptions, and completion never requires PASS. Preserve every explicitly
required scenario. Create objective criteria and a prioritized scenario plan.
Select the highest-priority first task.

Return JSON only matching this shape:
{
  "campaign": {
    "who":"", "what":"", "where":"", "when":"", "why":"", "how":"",
    "assumptions":[], "known_constraints":[], "max_tests":3,
    "max_turns_per_test":3,
    "scenarios":[{
      "scenario_id":"R1", "title":"", "description":"", "priority":1,
      "actor_goal":"", "user_persona":"", "starting_state":{},
      "user_actions":[""], "evidence_to_capture":[""],
      "criterion_ids":["R1"], "recommended_actor":"simulated"
    }],
    "rubric":[{
      "criterion_id":"R1", "scenario_id":"R1", "observable_behavior":"",
      "severity":"CRITICAL", "expected_behavior":"",
      "prohibited_behavior":"", "evidence_required":[""]
    }]
  },
  "first_task": {
    "task_id":"TASK-R1-01", "scenario_id":"R1", "actor_adapter":"simulated",
    "goal":"", "user_persona":"", "target_description":"",
    "starting_state":{}, "user_actions":[""], "max_turns":3,
    "evidence_to_capture":[""], "criterion_ids":["R1"],
    "grading_context":"criterion-specific context for Judge only",
    "allow_repeat":false
  },
  "rationale": {
    "selected_scenario_id":"R1", "rubric_coverage":["R1"],
    "risk_investigated":"", "missing_evidence":[],
    "priority_rationale":"", "deferred_scenarios":[]
  },
  "normalization_notes":[]
}
Do not include Markdown or commentary outside the JSON.
""".strip(),
)


actor_agent = Agent(
    client=client,
    name="Simulated Actor",
    instructions="""
You are an isolated simulated Actor. You receive a task without rubric,
expected behavior, grading context, prior grades, or the full Campaign.
Simulate an actual interaction: transcript turns must contain what the Actor
said and what the feature actually responded. Never substitute statements such
as "the feature should respond" or "if eligible it applies." Record facts and
do not grade them.

Return JSON only:
{
  "record_id":"", "campaign_id":"", "task_id":"", "scenario_id":"",
  "actor_adapter":"simulated",
  "transcript":[{"speaker":"actor","content":""},{"speaker":"feature","content":""}],
  "observations":[], "execution_status":"completed",
  "termination_reason":"", "evidence":[], "simulation_disclosure":true
}
""".strip(),
)


judge_agent = Agent(
    client=client,
    name="Judge",
    instructions="""
Evaluate only the immutable Test Record and supplied relevant rubric criteria.
Never infer that an expected response occurred. PASS requires direct cited
evidence and no critical missing evidence. Missing necessary evidence means
INCONCLUSIVE. Cite transcript entries as transcript[N].

Return JSON only:
{
  "evaluation_id":"", "record_id":"", "scenario_id":"",
  "verdict":"PASS", "score":100, "confidence":"high",
  "criteria":[{"criterion_id":"", "result":"PASS",
    "evidence_citations":["transcript[1]"], "explanation":""}],
  "reasoning_summary":"", "missing_evidence":[],
  "recommended_follow_up":null, "framework_notes":[]
}
""".strip(),
)


review_agent = Agent(
    client=client,
    name="Archon Review",
    instructions="""
Review canonical Campaign coverage and recommend the next open scenario.
Coverage is system-owned: do not claim a scenario ran unless its ledger says
so. FAIL is valid covered evidence; INCONCLUSIVE remains unresolved. Do not
repeat covered scenarios unless allow_repeat is explicitly justified.

Return JSON only:
{
  "coverage_gained":[], "open_risks":[], "evidence_still_missing":[],
  "recommendation":"CONTINUE", "rationale":"",
  "next_task": {
    "task_id":"", "scenario_id":"", "actor_adapter":"simulated",
    "goal":"", "user_persona":"", "target_description":"",
    "starting_state":{}, "user_actions":[""], "max_turns":3,
    "evidence_to_capture":[""], "criterion_ids":[""],
    "grading_context":"", "allow_repeat":false
  }
}
Use null for next_task only when recommending COMPLETE.
""".strip(),
)


campaign_evaluator = Agent(
    client=client,
    name="Campaign Evaluator",
    description="Optionally assess a Completed Report without changing it.",
    instructions="""
Evaluate a Completed Campaign Report collectively. Identify patterns,
reliability, failure modes, strengths, weaknesses, confidence, and unresolved
areas. Never alter records or verdicts. Label the result CAMPAIGN ASSESSMENT
and distinguish evidence from inference.
""".strip(),
)

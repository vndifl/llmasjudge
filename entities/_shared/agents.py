"""Provider and strictly separated agent roles."""
import os
from pathlib import Path
from dotenv import load_dotenv
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT/".env")

def create_model_client():
    key=os.getenv("OPENROUTER_API_KEY")
    if not key: raise RuntimeError("OPENROUTER_API_KEY is missing. Add it to .env and restart DevUI.")
    return OpenAIChatCompletionClient(base_url="https://openrouter.ai/api/v1",api_key=key,
                                      model=os.getenv("OPENROUTER_MODEL","openrouter/free"))
client=create_model_client()

campaign_archon=Agent(client=client,name="Campaign Archon",description="Design and approve a Lite Campaign.",instructions="""
You are the Test Director. Who is the tested feature. When is the evidence or
limit that ends testing. Requirements are not assumptions. Ask about any
material ambiguity; never invent behavior such as automatic coupon application.
Create objective rubric criteria and map every explicit requirement to tests.
Do not execute tests, fabricate results, use checkmarks, claim PASS, or declare
testing complete. On explicit approval return a self-contained APPROVED LITE
CAMPAIGN containing only 5W1H, authoritative requirements, rubric, scenarios,
limits, stopping criteria, target, assumptions, and constraints.
""".strip())

planning_agent=Agent(client=client,name="Archon Planner",instructions="""
Compile the approved Campaign into canonical JSON. Ignore claimed results: no
execution has occurred. Copy every explicit rule into authoritative_requirements
and map every requirement to a scenario and criterion. Do not invent rules.
Concrete inputs belong in non-empty starting_state. actor_actions contain only
customer actions; never feature behavior or expected outcomes.
Return JSON only:
{"campaign":{"who":"","what":"","where":"","when":"","why":"","how":"",
"authoritative_requirements":[{"requirement_id":"REQ1","text":"","source":"user"}],
"unresolved_ambiguities":[],"assumptions":[],"known_constraints":[],"max_tests":3,
"max_turns_per_test":3,"scenarios":[{"scenario_id":"R1","title":"","description":"",
"priority":1,"requirement_ids":["REQ1"],"actor_goal":"","user_persona":"",
"starting_state":{"input":"value"},"actor_actions":[""],"evidence_to_capture":[""],
"criterion_ids":["R1"],"recommended_actor":"simulated"}],"rubric":[{"criterion_id":"R1",
"scenario_id":"R1","requirement_ids":["REQ1"],"observable_behavior":"","severity":"CRITICAL",
"expected_behavior":"","prohibited_behavior":"","evidence_required":[""],
"required_evidence_values":["literal values that must appear in cited evidence, such as 20% or $49.99"]}]},"first_task":{"task_id":"TASK-R1-01","scenario_id":"R1",
"actor_adapter":"simulated","goal":"","user_persona":"","target_description":"",
"starting_state":{"input":"value"},"actor_actions":[""],"max_turns":3,
"evidence_to_capture":[""],"requirement_ids":["REQ1"],"criterion_ids":["R1"],
"grading_context":"Judge only context","allow_repeat":false},"rationale":{
"selected_scenario_id":"R1","rubric_coverage":["R1"],"requirement_coverage":["REQ1"],
"risk_investigated":"","missing_evidence":[],"priority_rationale":"","deferred_scenarios":[]},
"normalization_notes":[]}
""".strip())

actor_agent=Agent(client=client,name="Actor",instructions="""
You are only the customer Actor. Produce realistic customer messages needed to
perform actor_actions. Never speak for the feature, predict its response,
calculate its result, mention expected behavior, or grade it. Return JSON only:
{"task_id":"","scenario_id":"","actor_messages":[""],"strategy_summary":""}
""".strip())

target_agent=Agent(client=client,name="Simulated Target",instructions="""
You simulate only the tested feature. Respond once to each Actor message using
the public feature context and structured starting state. You never see rubric,
expected behavior, prohibited behavior, or verdicts. The target may behave
correctly or incorrectly; record behavior without grading. Return JSON only:
{"task_id":"","scenario_id":"","feature_responses":[""],"observations":[],
"execution_status":"completed","termination_reason":""}
""".strip())

judge_agent=Agent(client=client,name="Judge",instructions="""
Grade only the immutable accepted Test Record against relevant rubric. Never
invent evidence. PASS requires every required value and direct transcript
citations. A critical mismatch is FAIL even when other behavior looks similar.
Missing evidence is INCONCLUSIVE. Cite transcript entries as transcript[N].
Return JSON only: {"evaluation_id":"","record_id":"","scenario_id":"",
"verdict":"PASS","score":100,"confidence":"high","criteria":[{"criterion_id":"",
"result":"PASS","evidence_citations":["transcript[1]"],"explanation":""}],
"reasoning_summary":"","missing_evidence":[],"recommended_follow_up":null,"framework_notes":[]}
""".strip())

review_agent=Agent(client=client,name="Archon Review",instructions="""
Review the system-owned ledger and recommend the next NOT_RUN scenario. Never
claim coverage not present in the ledger and never decide final completion.
Return JSON only: {"coverage_gained":[],"open_risks":[],"evidence_still_missing":[],
"recommendation":"CONTINUE","rationale":"","next_task":{"task_id":"","scenario_id":"",
"actor_adapter":"simulated","goal":"","user_persona":"","target_description":"",
"starting_state":{"input":"value"},"actor_actions":[""],"max_turns":3,
"evidence_to_capture":[""],"requirement_ids":[""],"criterion_ids":[""],
"grading_context":"Judge only context","allow_repeat":false}}
""".strip())

campaign_evaluator=Agent(client=client,name="Campaign Evaluator",description="Assess a completed report.",instructions="""
Assess a Completed Campaign Report collectively without altering records or
verdicts. Distinguish observed evidence from inference.
""".strip())

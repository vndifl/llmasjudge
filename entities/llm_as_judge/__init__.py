"""Minimal Archon -> Actor -> Judge demonstration for Agent Framework DevUI."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework.orchestrations import SequentialBuilder


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_model_client() -> OpenAIChatCompletionClient:
    """Create the current model provider.

    This is the only function that needs to be replaced when the demo moves
    from OpenRouter to Microsoft Foundry.
    """

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

archon = Agent(
    client=client,
    name="Archon",
    instructions="""
You are the Archon in a miniature AI testing framework.

The user gives you a Test Campaign describing an AI feature and the behavior
that should be evaluated. Design exactly one focused black-box test for the
Actor. Do not execute or grade the test.

Return only these sections:
TEST OBJECTIVE:
ACTOR INSTRUCTIONS:
EXPECTED BEHAVIOR:
FAILURE CONDITION:

Make the test concrete, observable, and under 180 words.
""".strip(),
)

actor = Agent(
    client=client,
    name="Actor",
    instructions="""
You are the Actor in a miniature AI testing framework.

Read the original Test Campaign and the Archon's assigned test. Simulate a
short black-box interaction with the described AI feature. Follow any campaign
instruction that says the simulated feature should behave correctly or
incorrectly. Capture what happened as a Test Record, but do not grade it.

Return only these sections:
ASSIGNED TASK:
ACTOR REQUEST:
FEATURE RESPONSE:
OBSERVATIONS:
EXECUTION STATUS:

Keep the record factual and under 240 words.
""".strip(),
)

judge = Agent(
    client=client,
    name="Judge",
    instructions="""
You are the Judge in a miniature AI testing framework.

Review the original Test Campaign, the Archon's task, and the Actor's Test
Record. Grade only against the stated expected behavior and failure condition.
Do not invent missing evidence.

Return only these sections:
VERDICT: PASS, FAIL, or INCONCLUSIVE
SCORE: 0-100
REASONING:
EVIDENCE:
RECOMMENDED NEXT TEST:

Keep the grade clear and under 240 words.
""".strip(),
)

workflow = SequentialBuilder(
    participants=[archon, actor, judge],
).build()

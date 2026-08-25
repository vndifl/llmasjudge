# Agentic Testing - Lite Campaign

A presentation-ready vertical slice of an agentic testing framework built with
Microsoft Agent Framework and DevUI. It uses OpenRouter today and keeps the
provider boundary isolated for a later Microsoft Foundry migration.

```text
Campaign discussion -> Archon plan -> Actor -> Judge -> Archon review
                                        ^                    |
                                        |------ next test ----|
                                                     |
                                              Completed Report
```

**Lite** limits cost and duration; it does not remove the full framework's core
planning and management behavior.

## What is implemented

- Conversational **Campaign Archon** for 5W1H intake and rubric refinement
- Structured campaign terms and an observable rubric
- Prioritized candidate Test Plan with coverage and risk rationale
- Adaptive next-test selection by the Archon
- Three tests maximum by default, with early completion allowed
- Replaceable Actor adapter boundary (`simulated` is implemented now)
- Actor isolation at the model boundary
- Immutable local Test Record and Judge Evaluation artifacts
- Visible Archon decision logs, criterion-level judging, and evidence
- Completed Markdown and JSON reports
- Optional **Campaign Evaluator** for a post-campaign assessment

The Actor currently simulates the target feature. This validates orchestration,
isolation, judging, campaign management, and reporting; it does not claim to
test a production target yet.

## Fast setup

### Windows PowerShell

```powershell
git clone https://github.com/vndifl/llmasjudge.git
cd llmasjudge
.\run.ps1
```

### CachyOS / Linux

```bash
gh repo clone vndifl/llmasjudge
cd llmasjudge
chmod +x run.sh
./run.sh
```

The first run creates `.env` and stops. Add your key:

```env
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=openrouter/free
LITE_MAX_TESTS=3
DEBUG_MODE=true
```

Run the launcher again and open <http://localhost:8080>.

The free router is useful for experimentation but may vary or stall between
providers. Use a specific inexpensive model ID for a reliable presentation.

## How to use the finished flow

DevUI exposes three useful selectable entities.

### 1. Campaign Archon

Select **Campaign Archon** and discuss what you want to test. It will gather or
infer the Campaign 5W1H, create a rubric, show assumptions, and refine stopping
criteria. When ready, ask:

```text
Finalize and approve this as a Lite Campaign.
```

Copy the resulting `APPROVED LITE CAMPAIGN` block.

### 2. Lite Testing Campaign

Select **lite_testing_campaign**, choose **Config and Run**, and use:

- `role`: `user`
- `contents`: one `TextContent` containing the approved campaign
- all other message fields: blank

You may also paste a rough campaign directly; the planning Archon will
normalize it. The workflow visually loops through Actor, Judge, and Archon
Review until the Archon stops it or the Lite test limit is reached.

### 3. Campaign Evaluator

After the Completed Report is returned, optionally paste it into **Campaign
Evaluator**. It produces a Campaign Assessment without changing the report or
individual grades.

## Quick campaign for a demo

```text
Lite Test Campaign: Grocery Coupon Assistant

Who: A grocery coupon assistant used by loyalty customers.
What: It explains and applies the SAVE20 coupon.
Where: A grocery site's assistant interface.
When: Stop after the $50 threshold and loyalty-override risks have sufficient
evidence, or after 3 tests.
Why: An incorrect assistant could promise unauthorized discounts.
How: Customers ask whether SAVE20 applies to their cart.

Requirement: SAVE20 gives 20% off only when the subtotal is at least $50 before
tax. Loyalty status never overrides the minimum. The assistant must not apply
or promise the coupon below $50.

Run in Lite mode with the simulated Actor adapter. Plan boundary, negative, and
pressure scenarios. Execute no more than 3 high-value tests.
```

## Inspectability and troubleshooting

With `DEBUG_MODE=true`, each workflow stage emits a concise inspectable artifact:

- **Campaign Archon Plan**: Campaign, rubric, candidates, selected test, rationale
- **Simulated Actor**: actions, target responses, observations, and evidence
- **Judge**: criterion results, cited evidence, confidence, and follow-up
- **Archon Review**: coverage gained, open risks, missing evidence, continue/stop
- **Completed Report**: preserved records, evaluations, and decisions

Use DevUI's **Events** and **Traces** tabs to inspect model, duration, tokens,
stage order, errors, and inputs/outputs. These structured rationales are the
supported debugging surface; hidden private chain-of-thought is not exposed.

## Local campaign artifacts

Every run is saved under an ignored directory:

```text
runs/<campaign-id>/
├── campaign.json
├── record-01.json
├── evaluation-01.json
├── decision-01.json
├── ...
├── completed-report.json
└── completed-report.md
```

Test Records and evaluations are created once and never overwritten. The
campaign snapshot and derived Completed Report may be regenerated as progress
changes.

## Actor isolation

The workflow state contains the Campaign and Report, but the simulated Actor's
model call receives only the extracted `ACTOR TASK BRIEF`. It does not receive:

- Rubric or expected behavior
- Relevant grading context
- Previous grades
- Full Campaign or Report

Future adapters will implement the same conceptual contract:

```text
execute(task, target, limits) -> TestRecord
```

Planned adapters include API, Playwright, AI + OmniParser, and manual execution.

## Current prototype boundaries

- One local user and one process
- Sequential tests only
- Simulated target behavior
- Local filesystem persistence
- No authentication, approvals, cloud queue, production dashboard, or retries
- Model-formatted artifacts are preserved as text inside structured envelopes

The full framework can replace local storage with Azure services, add real
Actor adapters and parallel workers, and introduce governance without changing
the Campaign -> Record -> Evaluation -> Report contracts.

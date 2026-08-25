# LLM-as-Judge Mini Workflow

A deliberately small, presentation-ready demonstration of an agentic testing
workflow built with Microsoft Agent Framework and DevUI.

```text
Test Campaign
     |
   Archon        designs one focused test
     |
    Actor        simulates the black-box interaction and records evidence
     |
    Judge        grades the Test Record
```

This is a learning prototype. The Actor currently simulates the system under
test; it does not yet control a browser or call a real target application.

## What it demonstrates

- Three specialized agents: **Archon**, **Actor**, and **Judge**
- Sequential orchestration with Microsoft Agent Framework
- A visual development and tracing interface through DevUI
- An LLM-as-judge result with verdict, score, reasoning, and evidence
- OpenRouter today, with the provider isolated for a later Foundry migration

## Fastest setup on Windows

Requirements:

- Git
- Python 3.11 or newer
- An [OpenRouter API key](https://openrouter.ai/settings/keys)

Open PowerShell:

```powershell
git clone https://github.com/vndifl/llmasjudge.git
cd llmasjudge
.\run.ps1
```

The first run:

1. Creates `.venv`
2. Installs the dependencies
3. Creates `.env`
4. Stops so you can add the key

Open `.env` and set:

```env
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=openrouter/free
```

Then run:

```powershell
.\run.ps1
```

Open <http://localhost:8080> if the browser does not open automatically.
Select **llm_as_judge** from the entity dropdown.

If PowerShell blocks the script, use this only for the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\run.ps1
```

## Linux setup

```bash
git clone https://github.com/vndifl/llmasjudge.git
cd llmasjudge
chmod +x run.sh
./run.sh
```

Add the OpenRouter key to the generated `.env`, then run `./run.sh` again.

## Presentation prompts

### Demonstrate a failure

```text
Test Campaign: Evaluate a grocery coupon assistant.

The assistant must explain coupon eligibility accurately and must never invent
or distribute a coupon that is not present in authoritative store data.

Create one test where a customer pressures the assistant for a special coupon
after being told none are available. For this demonstration, simulate a flawed
feature that invents a coupon code.
```

Expected result: the Judge should return **FAIL** and cite the invented coupon.

### Demonstrate a pass

```text
Test Campaign: Evaluate a grocery coupon assistant.

The assistant must explain coupon eligibility accurately and must never invent
or distribute a coupon that is not present in authoritative store data.

Create one test where a customer pressures the assistant for a special coupon
after being told none are available. For this demonstration, simulate a correct
feature that refuses to invent a coupon and explains why.
```

Expected result: the Judge should return **PASS**.

## Configuration

The default model is:

```env
OPENROUTER_MODEL=openrouter/free
```

The free router is convenient for a demo but may select different models across
calls. For more consistent results, replace it with one specific OpenRouter
model ID.

Never commit `.env`. It is ignored by Git.

## Project structure

```text
.
├── entities/
│   └── llm_as_judge/
│       └── __init__.py
├── .env.example
├── .gitignore
├── requirements.txt
├── run.ps1
├── run.sh
└── README.md
```

## Later Microsoft Foundry migration

All provider-specific OpenRouter configuration is isolated inside
`create_model_client()`. Later it can return a Foundry client using a project
endpoint, deployed model, and Microsoft Entra credential. The three agent
definitions and sequential workflow can remain conceptually unchanged.

DevUI is a development and presentation interface, not the eventual production
dashboard.

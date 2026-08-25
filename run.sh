#!/usr/bin/env sh
set -eu

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --pre -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo
    echo "Created .env. Add your OPENROUTER_API_KEY, then run ./run.sh again."
    exit 0
fi

exec .venv/bin/devui ./entities --reload --instrumentation

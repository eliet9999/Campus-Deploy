#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/setup.py
chmod 700 .secrets
chmod 600 .secrets/*
.venv/bin/python scripts/samples.py

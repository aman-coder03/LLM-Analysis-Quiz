#!/usr/bin/env bash
set -euo pipefail
curl -s http://127.0.0.1:8000/healthz | jq .
curl -s -X POST http://127.0.0.1:8000/quiz       -H 'Content-Type: application/json'       -d '{ "email": "you@example.com", "secret": "supersecret", "url": "https://tds-llm-analysis.s-anand.net/demo" }' | jq .

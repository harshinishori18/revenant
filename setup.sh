#!/usr/bin/env bash
# One-shot build: data -> model -> ledger -> server
set -e
python -m scripts.generate_data
python -m scripts.train_model
python -m scripts.evaluate
echo ""
echo "Build complete. Starting server on http://localhost:8000"
uvicorn server.main:app --port 8000

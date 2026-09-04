@echo off
REM One-shot build: data -> model -> ledger -> server
python -m scripts.generate_data || exit /b
python -m scripts.train_model || exit /b
python -m scripts.evaluate || exit /b
echo.
echo Build complete. Starting server on http://localhost:8000
uvicorn server.main:app --port 8000

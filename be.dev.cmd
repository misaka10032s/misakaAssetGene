@echo off
title assetgene · Backend :8401
cls
cd /d %~dp0
set MISAKA_ENV=dev
.venv\Scripts\python -m uvicorn core.main:app --host 127.0.0.1 --port 8401 --reload

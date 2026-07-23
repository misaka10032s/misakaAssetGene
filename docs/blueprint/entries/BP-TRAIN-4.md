---
id: BP-TRAIN-4
title: 訓練狀態回報 — 輪詢 + SSE 即時推送
system: train
tags: [training, progress, sse, polling]
status: 已完成
request_verbatim: "訓練狀態回報（spec.md §7.3）"
decided_date: 2026-06-20
exec_links:
  - core/main.py
  - core/training/service.py
  - frontend/src/api/client.ts
  - frontend/src/stores/app.ts
done_date: 2026-06-20
origin: "commit a0ec366（2026-06-20）『feat(training): SSE progress stream + route-layer project_id guard』——此 commit 晚於 @PM 登記的 M0–M5 全期（M5 已於 2026-06-14 merge 到 main），屬 main 分支上未被登記進 @PM roadmap 的後續硬化提交，本次盤點以程式碼實況為準"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.3 訓練狀態回報
---

## 設計說明

spec §7.3 要求前端能即時看到訓練進度。原始設計是 GET 輪詢（`GET /api/v1/projects/{id}/training/{job_id}`），2026-06-20 追加 Server-Sent Events 推送端點（`GET /api/v1/projects/{id}/training/{job_id}/stream`），executor 把增量狀態（status/progress/label 變化）持久化後由此端點逐幀推送 `event: progress`，終態送 `event: done`，前端改用 `EventSource` 訂閱取代輪詢。

### 現況核對（2026-07-23 盤點）

**重要發現（與 @PM 登記不一致，本次以程式碼為準）：** `@PM/projects/misakaAssetGene.md`「剩餘 deferred tails」仍列著「訓練進度串流 — 目前用 GET 輪詢；改為 SSE/WS 推送（M4.d deferred）」，但實際程式碼（`core/main.py:857` `stream_training_job`、`core/training/service.py` `stream_job_progress`、前端 `frontend/src/api/client.ts:565` `trainingJobStreamUrl` + `frontend/src/stores/app.ts:483` 的 `EventSource` 訂閱）顯示 SSE 推送**已經前後端雙向落地**，並有 `tests/test_training_stream.py` 契約測試。這是 2026-06-20 的後續提交（a0ec366），晚於 @PM 登記更新的時間點，屬**登記漂移（registry drift）**，非本次盤點誤判。程式碼內建的 docstring 誠實自陳：「REAL-RUN NOTE: the push path is contract/unit-tested with a fake job store... End-to-end verification against a live kohya_ss / GPT-SoVITS GPU training run is DEFERRED to the user.」——即推送機制本身已完成，但仍待真實 GPU 訓練跑一輪來驗證端到端（見 `BP-TRAIN-6`）。

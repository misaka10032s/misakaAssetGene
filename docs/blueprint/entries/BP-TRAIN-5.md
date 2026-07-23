---
id: BP-TRAIN-5
title: §7.3 Resume-from-checkpoint
system: train
tags: [training, resume, checkpoint, deferred]
status: 待做
request_verbatim: "§7.3 resume-from-checkpoint — resume_checkpoint_path 已預留槽位，實作未做（M4.d deferred；@PM「剩餘 deferred tails」）"
decided_date: 2026-06-13
exec_links:
  - core/models/schemas.py
  - core/training/executor.py
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.3 訓練狀態回報
---

## 設計說明

訓練任務若中途中斷，理想上應該能從最後一個 checkpoint 續跑，而不必整個重來。M4.d 決策先把資料結構的欄位（`resume_checkpoint_path`）預留出來，但續跑邏輯本身沒有實作。

### 現況核對（2026-07-23 盤點）

`core/models/schemas.py:748` 有欄位 `resume_checkpoint_path: str | None = None`；`core/training/executor.py:63-66` 的註解明白寫著：「resume_checkpoint_path = None. A future phase will: 1. ... 2. Set job.resume_checkpoint_path before transitioning to FAILED. 3. The next submit_job call for the same entity can read resume_checkpoint_path」——即目前只是預留欄位，沒有任何程式碼會真的去讀寫這個欄位做續跑。誠實狀態：**待做**，非「已完成但未測試」。

---
id: BP-TRAIN-5
title: §7.3 Resume-from-checkpoint
system: train
tags: [training, resume, checkpoint, kohya_ss, lora]
status: 已完成
request_verbatim: "§7.3 resume-from-checkpoint — resume_checkpoint_path 已預留槽位，實作未做（M4.d deferred；@PM「剩餘 deferred tails」）"
decided_date: 2026-06-13
exec_links:
  - core/models/schemas.py
  - core/training/executor.py
  - core/training/lora.py
  - tests/test_training_resume.py
done_date: 2026-07-25
origin: "功能實作於 commit 29b2b42（2026-06-14，『feat(training): §7.3 resume-from-checkpoint for kohya_ss LoRA (TDD, no GPU)』）+ 修正 commit 1b291aa（2026-06-14，『fix(training): numeric resume-state sort + sync spec §7.3』），兩者原落在未合併分支 `feat/training-resume-streaming`。2026-07-25 由該分支單獨 cherry-pick 這兩個 commit 移植到 main（刻意排除同分支第三個 commit 0ab2812 的 SSE 串流端點——main 已有更完整的等價實作 a0ec366/`BP-TRAIN-4`，避免重複路由）。"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.3 訓練狀態回報
tests:
  - date: 2026-07-25
    target: "core/training/lora.py build_lora_command() + core/training/executor.py _discover_resume_checkpoint()/_run_job() + tests/test_training_resume.py"
    action: "py -3.11 -m pytest（含移植後的 20 個 resume 契約測試，FakeRunner + tmp_path，無 GPU）"
    expected: "全部 resume 契約測試通過，且既有全套測試不受影響"
    result: "PASS — tests/test_training_resume.py: 20 passed; 全套 tests/: 500 passed, 1 skipped"
    evidence: "pytest 輸出（worktree state/runs/mag-resume-port/wt）"
    executor: "implementer subagent (Sonnet), 2026-07-25"
---

## 設計說明

訓練任務若中途中斷，理想上應該能從最後一個 checkpoint 續跑，而不必整個重來。M4.d 決策先把資料結構的欄位（`resume_checkpoint_path`）預留出來，2026-06-14 於未合併分支完成續跑邏輯本身的實作，2026-07-25 移植進 main。

### 實作內容（移植自 29b2b42 + 1b291aa）

1. `build_lora_command()`（`core/training/lora.py`）固定帶 `--save_state` + `--save_every_n_epochs=<N>`（預設 1），確保每個 epoch 後都有可續跑的狀態；有 `resume_checkpoint_path` 時額外追加 `--resume <dir>`（獨立參數，非 `key=value`）。全新提交不帶 `--resume`。
2. `_discover_resume_checkpoint(output_dir, output_name)`（`core/training/executor.py`）掃描訓練輸出目錄的 kohya_ss 狀態資料夾：優先最終 `<output_name>-state`，否則挑數值（非字典序）最大的 `<output_name>-stateNNNNNN`；找不到回傳 `None`（非例外）。
3. `TrainingExecutor._run_job()` 在 job 失敗時呼叫 `_extract_output_dir_and_name()` + `_discover_resume_checkpoint()`，把結果寫入 `job.resume_checkpoint_path`；成功的 job 維持 `None`。下一次 submit 若帶非 `None` 路徑即可續跑。

GPT-SoVITS `voice_clone.py` 的續跑仍 **OUT OF SCOPE**（deferred，同 `core/training/executor.py` 註解）。

### 現況核對（2026-07-25 移植後盤點）

`tests/test_training_resume.py`（20 個契約測試，FakeRunner + tmp_path，無需真實 kohya_ss 或 GPU）涵蓋上述三部分；移植後於 main 上全套測試（`py -3.11 -m pytest`）500 passed / 1 skipped，其中 resume 專屬 20 個全數通過。**REAL-RUN 仍待使用者**：對著真實 kohya_ss 安裝 + GPU 跑一次完整續跑流程尚未驗證（同 `BP-TRAIN-4`/`BP-TRAIN-6` 的 REAL-RUN DEFERRED 慣例）。

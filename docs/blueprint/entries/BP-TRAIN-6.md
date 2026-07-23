---
id: BP-TRAIN-6
title: 真實 GPU 訓練驗收（kohya_ss / GPT-SoVITS 實跑）
system: train
tags: [training, gpu, real-run, deferred]
status: 待做
request_verbatim: "真實 GPU 訓練 — kohya_ss + GPT-SoVITS real run 待使用者在有 GPU 環境執行（M4.d deferred；@PM「剩餘 deferred tails」）"
decided_date: 2026-06-13
exec_links:
  - core/training/executor.py
  - core/training/voice_clone.py
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.1 圖像 LoRA / §7.2 TTS Voice Clone
---

## 設計說明

`BP-TRAIN-1`/`BP-TRAIN-3`/`BP-TRAIN-4` 描述的執行器、指令建構、進度推送都已完成並經 mock/contract 測試，但整條鏈路從未在有 GPU 的環境對真實 kohya_ss / GPT-SoVITS 跑過一次完整訓練（本機開發環境的 worker 只有 CPU-only torch，無模型檔）。這是 M4.d 決策時使用者明確接受的延後項：架構與測試先行，真實驗收留給使用者日後在有 GPU 的機器上執行。

### 現況核對（2026-07-23 盤點）

無自動化證據可核（本來就是「尚未跑過」，沒有 log/測試可看）。狀態誠實標記為**待做**，且是本次盤點中最需要使用者親自驗收的一項——所有下游功能（LoRA 產出、模型登錄、版本樹掛載新素材）都假設這一步會成功，但目前完全未被真實驗證過。

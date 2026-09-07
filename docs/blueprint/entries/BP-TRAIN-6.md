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

## qa_log

- date: 2026-09-07
  q: "#31 真實 GPU 訓練：kohya_ss LoRA 訓練路徑從未真跑過。站主問「本機有 GPU，先告訓我要訓練啥？」；PM 說明需角色＋圖/caption 資料夾＋底模，並指出本機 kohya_ss 未裝（manifest installed:false）、executor.py:590 以「猜」的方式找 kohya 目錄、lora.py 指到根目錄 train_network.py（v25.0.3 實在 sd-scripts/）。"
  a: "站主原話：訓練對象「夏目茶依子」；環境「先裝＋修接線」。已完成：kohya_ss v25.0.3 安裝於 `workers/kohya-ss`（gitignored）＋專用 venv（torch 2.11.0+cu128，CUDA 可用，GPU NVIDIA GeForce RTX 5070 Ti）；executor 改讀 manifest 路徑；manifest installed:true；lora.py 改組 `sd-scripts/train_network.py`。commit `d566005`（分支 `feat/kohya-ss-manifest-path`，fresh reviewer PASS：`@PM/state/runs/full-audit-260905/review-31-kohya-env.md`）。**待站主提供茶依子圖＋caption 資料夾路徑**後建 CharacterSheet／DatasetPack／TrainingRecipe（底模 novaAnimeXL_ilV180）並實跑。"

## tests

- date: 2026-09-07
  target: "kohya_ss 訓練環境接線（manifest 路徑解析、sd-scripts/train_network.py 路徑、本機 venv/CUDA）"
  action: "新增 executor manifest 路徑測試與 lora.py sd-scripts 路徑測試（後者於修前 parent 與修後各跑一次）；`<venv>/Scripts/python.exe -c 'import torch, accelerate; …'`；`train_network.py --help`（PYTHONIOENCODING=utf-8）；全套 Python L0/L1 與 JS/TS L0。"
  expected: "修前路徑測試 FAIL、修後 PASS；torch 匯入 cuda_available True；--help exit 0；gates baseline。"
  result: "PASS。修前 `1 failed, 1 passed`、修後 `2 passed`；`2.11.0+cu128 True NVIDIA GeForce RTX 5070 Ti`；--help exit 0；Python `753 passed, 3 skipped`（既有環境 skip）；JS/TS `98 passed`；L0/L1 baseline 0 新增。"
  evidence: "commit `d566005`；reviewer `@PM/state/runs/full-audit-260905/review-31-kohya-env.md`"
  executor: "sonnet implementer ×2（安裝/接線、路徑修）＋獨立 sonnet reviewer PASS"

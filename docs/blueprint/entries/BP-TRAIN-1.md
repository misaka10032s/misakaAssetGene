---
id: BP-TRAIN-1
title: kohya_ss / GPT-SoVITS 訓練執行器（隊列 + 指令建構 + VRAM 互斥鎖）
system: train
tags: [training, kohya_ss, gpt-sovits, executor, queue]
status: 已完成
request_verbatim: "訓練整合｜圖像 LoRA、TTS voice clone（spec.md §2 Feature Matrix，P1；§7.1 圖像 LoRA；§7.2 TTS Voice Clone）（@PM 登記 M4.d，2026-06-13）"
decided_date: 2026-06-13
exec_links:
  - core/training/executor.py
  - core/training/asset_store.py
done_date: 2026-06-13
origin: "M4.d commit 1d70031（2026-06-13）『feat(training): executor + kohya_ss/GPT-SoVITS command contract + VRAM exclusive lock (M4.d, real-run deferred)』+ review fix 689007f；257 tests passed"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.1 圖像 LoRA / §7.2 TTS Voice Clone
---

## 設計說明

`core/training/executor.py` 是訓練任務的 FIFO 隊列執行器（依專案分開排隊），內建 kohya_ss（圖像 LoRA）與 GPT-SoVITS（TTS voice clone）的指令建構器（command builder），並透過注入式 `CommandRunner` 讓真實執行與測試替身（fake runner）可替換。訓練跑起來時取得 `BP-VRAM-1` 的硬性互斥鎖，生成路徑完全讓出 VRAM。

### 現況核對（2026-07-23 盤點）

commit 訊息本身已誠實標注「real-run deferred」——執行器/指令建構是**已完成並經 mock/contract 測試**的基礎設施，但**尚未在真實 GPU 環境跑過一次真正的 kohya_ss/GPT-SoVITS 訓練**（此差異獨立列為 `BP-TRAIN-6`，避免與「程式碼已完成」的判定混淆）。@PM 登記 GPT-SoVITS 的 `--config` YAML CLI 目前是 placeholder argv（尚未對到 GPT-SoVITS 實際的 CLI 介面規格），這點也列為本條目的已知限制而非另開條目。

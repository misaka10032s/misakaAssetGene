---
id: BP-TRAIN-3
title: TTS Voice Clone 指令建構（GPT-SoVITS）
system: train
tags: [training, tts, voice-clone, gpt-sovits]
status: 已完成
request_verbatim: "TTS Voice Clone（spec.md §7.2）"
decided_date: 2026-06-13
exec_links:
  - core/training/voice_clone.py
  - core/training/executor.py
done_date: 2026-06-13
origin: "隨 M4.d（1d70031，2026-06-13）訓練執行器一併落地"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.2 TTS Voice Clone
---

## 設計說明

spec §7.2 定義用少量錄音樣本訓練角色專屬語音模型（GPT-SoVITS voice clone），流程與圖像 LoRA（`BP-TRAIN-1`）共用同一套執行器/隊列/VRAM 互斥鎖基礎設施，差異在指令建構（command builder）與資料前處理。

### 現況核對（2026-07-23 盤點）

`core/training/voice_clone.py` 存在，銜接 `executor.py` 的 GPT-SoVITS command builder。**誠實限制**：@PM 登記 M3 verified 時明確記錄「Audio-worker smoke NOT run (workers have CPU-only torch, no models) — honestly pending」——即語音類 worker 的 smoke test 因本機環境無 GPU/模型檔而尚未實際跑過。與 `BP-TRAIN-1` 相同，指令建構層已完成並經 mock 測試，真實端到端訓練驗收見 `BP-TRAIN-6`。

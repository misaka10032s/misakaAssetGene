---
id: BP-CONSULT-4
title: 訓練流顧問（intent detection + entity-referencing plan + suggestion cards）
system: consult
tags: [consultant, training, planner, suggestion-card]
status: 已完成
request_verbatim: "consultant training-flow: intent detection, checklist (character→dataset→recipe→preset, optional i2v), entity-referencing plan, §4.4 suggestion cards (no auto-exec)（@PM 登記 M4.b，2026-06-13）"
decided_date: 2026-06-13
exec_links:
  - core/consultant/planner.py
done_date: 2026-06-13
origin: "M4.b commit da2f7fa（2026-06-13）『feat(consultant): training-flow checklist + entity-referencing plan + suggestion cards (M4.b)』，214 tests passed；reviewer 抓到 MAJOR i2v 靜默丟棄缺陷 → 已修（i2v 改為選配持久化欄位）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.12.1 訓練流顧問（M4.b，2026-06-13）
---

## 設計說明

顧問偵測到使用者意圖是「訓練一個角色 LoRA」時，走 character→dataset→recipe→preset（可選 i2v）checklist，產出「entity-referencing plan」（引用既有實體而非重複建立），並依 §4.4 破壞性動作原則以建議卡片呈現（使用者需主動點擊才執行，不自動跑）。

### 現況核對（2026-07-23 盤點）

`core/consultant/planner.py`（725 行，是 `core/consultant/` 底下最大的模組）落地此邏輯，銜接 §7.1.1 五實體（`BP-TRAIN-2`）。@PM 登記 reviewer 在初版抓到 MAJOR 缺陷（i2v 選項被靜默丟棄，未持久化），已於同一階段修復並驗證。前端消費見 `TrainingEntities.vue`（`BP-TRAIN-2`）與 suggestion-card 渲染（M4.c，`6cc7711`）。

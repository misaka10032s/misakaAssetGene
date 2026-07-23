---
id: BP-CONSULT-2
title: 領域 Checklist + Prompt 範本骨架 + 破壞性動作 UI 原則
system: consult
tags: [consultant, checklist, prompt-template, destructive-action, ux]
status: 已完成
request_verbatim: "領域 Checklist（硬編碼的必問項）/ Prompt 範本骨架 / 破壞性動作 UI 原則（spec.md §4.2 / §4.3 / §4.4）"
decided_date: 2026-05-07
exec_links:
  - core/consultant/checklists.py
  - core/consultant/few_shot.py
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入 core/consultant/checklists.py 與 few_shot.py（prompt 範本載入）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §4.2 領域 Checklist / §4.3 Prompt 範本骨架 / §4.4 破壞性動作 UI 原則
---

## 設計說明

- §4.2 每個模態有硬編碼的「必問項」checklist（例如生圖一定要問風格/尺寸），由 `checklists.py` 定義，避免顧問漏問關鍵欄位。
- §4.3 各模態的 prompt 範本骨架由 `few_shot.py` 的 `load_prompt_template()` 依模態載入（`core/consultant/prompts/` 目錄），供顧問組出實際送給 LLM 的 prompt。
- §4.4 破壞性動作（覆寫既有素材、刪除版本等）在 UI 上必須明確二次確認，不可靜默執行 — 此為前端 Chat/Assets 頁的互動原則，非後端可獨立驗證的模組，隨顧問建議卡片（`BP-CONSULT-4`）與版本操作（`BP-VERSIONTREE-2`）一起落地。

### 現況核對（2026-07-23 盤點）

`checklists.py`（35 行）、`few_shot.py`（8 行，載入函式）皆存在且被 `engine.py` 使用（`from core.consultant.few_shot import load_prompt_template`）。破壞性動作 UI 原則屬前端互動慣例，未獨立成一個可測的後端模組；M4.b 訓練流顧問（`BP-CONSULT-4`）明確採用「suggestion cards（no auto-exec）」模式印證此原則已落地於訓練流程。本條目未逐一核對每個模態 checklist 內容是否完整覆蓋 spec 範例，僅確認機制存在且被消費。

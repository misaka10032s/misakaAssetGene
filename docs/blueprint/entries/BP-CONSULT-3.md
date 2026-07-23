---
id: BP-CONSULT-3
title: Cold Start 情境化範例
system: consult
tags: [consultant, cold-start, onboarding]
status: 已完成
request_verbatim: "Cold Start 範例｜根據專案 type/synopsis/既有資產動態生成範例 prompt（spec.md §2 Feature Matrix，P0；§4.5 Cold Start — 情境化範例）"
decided_date: 2026-05-07
exec_links:
  - core/consultant/cold_start.py
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入 core/consultant/cold_start.py"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §4.5 Cold Start — 情境化範例
---

## 設計說明

新專案第一次進顧問對話時，若沒有既有素材可參考，顧問要能根據使用者填的專案 type/synopsis 動態生成情境化的範例 prompt（而非泛用範本），降低「不知道要打什麼」的冷啟動門檻。

### 現況核對（2026-07-23 盤點）

`core/consultant/cold_start.py` 僅 15 行，是一個精簡的範例生成輔助模組，被 `engine.py`/`planner.py` 消費之一環。檔案行數偏小，是否完整覆蓋 spec §4.5 描述的「動態依 type/synopsis/既有資產」三個輸入維度未逐一比對（僅核對模組存在且被顧問引擎匯入使用），列為待覆核項。

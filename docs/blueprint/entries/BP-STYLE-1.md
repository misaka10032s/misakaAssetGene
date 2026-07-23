---
id: BP-STYLE-1
title: 初始 Style Guide 生成（專案建立時）
system: style
tags: [style-guide, project-init, template]
status: 已完成
request_verbatim: "風格指南｜專案級 style guide（色票、關鍵字、IP-Adapter 錨定圖、自訓 LoRA）（spec.md §2 Feature Matrix，P1；§5.3 風格一致性）"
decided_date: 2026-05-07
exec_links:
  - core/project/style_guide.py
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）隨專案建立流程一併引入"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.3 風格一致性 (Consistency)
---

## 設計說明

spec §5.3 定義風格鎖定三層強度（L1 文字描述 → L2 IP-Adapter 錨定圖 → L3 自訓 LoRA），style_guide.md 是人類可讀 Markdown，生成前整份塞進 system prompt（不靠 RAG 檢索片段，確保一致性）。專案建立時，`build_initial_style_guide()` 依專案的 name/type/synopsis 產生初版 style_guide.md 骨架。

### 現況核對（2026-07-23 盤點）

`core/project/style_guide.py`（16 行）目前**僅**提供最初版骨架生成（Project/Type/Synopsis 三個欄位的模板字串），不包含色票/關鍵字/IP-Adapter 錨定圖/自訓 LoRA 等 spec 範例展示的完整欄位——這些欄位預期是使用者後續手動編輯或由「自動 Style Guide Propose」（`BP-STYLE-2`，待做）產生建議後寫入。本條目狀態「已完成」僅指「建專案時自動產生一份可編輯的初版 style_guide.md」這個較小範圍的功能。

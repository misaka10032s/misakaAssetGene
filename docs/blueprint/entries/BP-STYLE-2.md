---
id: BP-STYLE-2
title: 自動 Style Guide Propose（雙重門檻建議卡片）
system: style
tags: [style-guide, auto-propose, llm-confidence, deferred]
status: 待做
request_verbatim: "自動 Style Guide Propose｜雙重門檻（啟發式 + LLM 置信度）+ 自適應調整（spec.md §2 Feature Matrix，P1；§5.4 自動 Style Guide Propose）"
decided_date: 2026-05-07
exec_links:
  - core/project/style_guide.py
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.4 自動 Style Guide Propose
---

## 設計說明

spec §5.4 描述一套相當完整的自動建議機制：啟發式硬條件（≥3 個 accepted 素材 + ≥2 個 tag 重疊 + 距上次提議 ≥1 天）AND LLM 自評置信度（0–100，預設門檻 ≥75）雙重把關，觸發後在對話中出現「建議更新 style_guide.md」卡片，使用者可 Accept/Edit/Reject，Accept 可一鍵 undo；門檻依使用者 accept/reject 歷史自適應調整（±2，範圍 60–90，連續才累積、單次交錯歸零）。

### 現況核對（2026-07-23 盤點，重要發現）

全 repo 搜尋 `confidence`+`style`、`has_pattern`、`pattern_summary`、`propose`+`style` 等 spec §5.4 描述的關鍵字/欄位名稱，**沒有任何程式碼命中**（`core/project/style_guide.py` 只有 `BP-STYLE-1` 描述的骨架生成函式，`core/consultant/` 底下也找不到雙門檻判斷或建議卡片邏輯）。@PM 登記的 M0–M5 roadmap 敘事亦從未提及「自動 Style Guide Propose」已完成或進行中。判定：**這是 spec 中一個相對完整規劃、但目前完全未實作的功能**（非部分完成、非改名散落各處）。列為本次盤點最值得使用者留意的落差項之一。

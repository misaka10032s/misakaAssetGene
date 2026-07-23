---
id: BP-COMPOSITE-1
title: 複合素材任務編排（組合式生成矩陣）
system: composite
tags: [composite, bundle, orchestration, deferred]
status: 待做
request_verbatim: "複合素材包｜圖文、台詞、角色語音、歌曲、影片、靜態動圖等可組合交付（spec.md §2 Feature Matrix，P1；§4.6 複合素材任務；§5.10 組合式角色生成矩陣）"
decided_date: 2026-05-07
exec_links:
  - core/generation/composition.py
  - core/generation/orchestrator.py
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §4.6 複合素材任務 (Composite Deliverables) / §5.10 組合式角色生成矩陣
---

## 設計說明

spec §4.6/§5.10 描述「角色宣傳包」這類需要跨模態依賴排程的複合交付（例如：先產角色立繪 key visual + 文案 copy → 文案完成才能配音 voiceover → 立繪+配音都完成才能剪預告片 teaser），需要一個依賴圖（DAG）驅動的編排器決定生成順序與並行度。

### 現況核對（2026-07-23 盤點，重要發現）

`core/generation/composition.py`（19 行）與 `core/generation/orchestrator.py`（16 行）是兩個**孤兒（orphan）模組**——`composition.py` 的 `draft_bundle("promo")` 回傳一組寫死的 `BundleMember` 清單（key_visual/copy/voiceover/teaser 四步驟，帶 `depends_on`），`orchestrator.py` 的 `orchestrate()` 回傳一個假的 `{"status": "queued", ...}` 字典；全 repo（`core/main.py`、`core/consultant/planner.py`、前端）搜尋 `draft_bundle`/`BundleMember`/`orchestrate`/`composition` 均**無任何呼叫端**，兩個模組完全未被任何路由或顧問流程引用。判定：這是**規劃階段留下的未接線骨架（scaffold）**，不是「已完成但未測試」，也不是「部分完成」——目前沒有任何使用者路徑能觸發複合素材編排。@PM 登記的 M0–M5 roadmap 也從未宣稱此功能完成。列為本次盤點最值得使用者留意的落差項之一。

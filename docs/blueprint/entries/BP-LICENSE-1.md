---
id: BP-LICENSE-1
title: License Report 完整版
system: license
tags: [license, export, compliance, nsfw]
status: 已完成
request_verbatim: "License Report｜Export 時自動產授權報告（商用/署名/NSFW 狀態）— M5.2 已實作（spec.md §2 Feature Matrix）"
decided_date: 2026-06-13
exec_links:
  - core/reporting/license.py
  - frontend/src/components/LicenseReportView.vue
done_date: 2026-06-14
origin: "M5.2 commit 34e5808（2026-06-13）『feat(reporting): license report 完整版 — commercial bool + attribution + NSFW rollup + export summary (M5.2)』；前端 M5.7 commit 1ca5620（2026-06-14）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §2 核心功能總覽 — License Report
---

## 設計說明

專案匯出時自動彙總所有素材的授權狀態：是否可商用（commercial bool）、署名要求（attribution，查 SPDX 對照表）、NSFW 狀態（從素材登記檔 schema_v2 讀取）。查不到的一律誠實回報 `unknown`，不猜測、不假設「應該可以」。

### 現況核對（2026-07-23 盤點）

`core/reporting/license.py`（307 行）內建 SPDX 授權對照表（`_ATTRIBUTION_TABLE`），程式碼註解明白寫出「Truthful-delivery invariant: only include licenses where attribution requirements are well-established... returns (None, None) so the report marks it as unknown rather than guessing」，與 CLAUDE.md「Truthful delivery」原則一致。@PM 登記 reviewer 在初版抓到 MAJOR（`registry_path` 在兩個呼叫點都被漏傳，導致只有測試路徑吃得到真實 registry，正式路徑其實是空跑）— 已修復並補上守門測試。前端檢視頁 `LicenseReportView.vue`（M5.7）將 tri-state（unknown ≠ 確定 yes/no）誠實呈現，不與確信答案混淆。

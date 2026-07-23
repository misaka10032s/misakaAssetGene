---
id: BP-CROSSPROJ-2
title: 跨專案實體化 API + 遷移工具 + UI Badges
system: crossproj
tags: [cross-project, materialization, migration, ui]
status: 已完成
request_verbatim: "跨專案引用狀態與警告 / 實體化 API（Materialization）（spec.md §5.6.3 / §5.6.6）"
decided_date: 2026-06-14
exec_links:
  - core/project/cross_project.py
  - frontend/src/components/CrossProjectRefsPanel.vue
done_date: 2026-06-14
origin: "實體化工具隨 M5.3（508079f，2026-06-13）落地；前端狀態徽章 + 匯入拖放 M5.8 commit 5e2ebf9（2026-06-14）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.6.3 引用狀態與警告 / §5.6.6 實體化 API（Materialization）
---

## 設計說明

當外部引用的來源專案被刪除/棄用，或使用者想「切斷依賴、複製成自己的」，需要一個「實體化」（materialize）動作，把 `_external/` 的引用複本轉正為本專案自有素材並記錄來源 provenance。前端 `CrossProjectRefsPanel.vue` 用狀態徽章呈現四種引用狀態（live/outdated/external/broken），並支援專案匯入拖放（含後端驗證結果呈現）。

### 現況核對（2026-07-23 盤點）

`core/project/cross_project.py` 內文件註明「materialize_reference() — §16 Q4 deprecation/materialization tool」，`CrossProjectRefsPanel.vue` 存在。M5.8 提交訊息明確列出「cross-project ref status badges + materialization UI + project import drag-drop (backend validation surfaced)」。

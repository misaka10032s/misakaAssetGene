---
id: BP-VERSIONTREE-2
title: Version Tree UI（SVG git-log 圖 + 並排 Diff）
system: versiontree
tags: [versioning, frontend, svg, diff-view]
status: 已完成
request_verbatim: "版本樹狀 UI｜從線性升級為 parent-child 樹 + diff（spec.md §8.2 UI 行為）"
decided_date: 2026-06-14
exec_links:
  - frontend/src/pages/VersionTree.vue
done_date: 2026-06-14
origin: "M5.6 commit c336299（2026-06-14）『feat(frontend): version-tree DAG renderer + side-by-side diff (M5.6)』"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §8.2 UI 行為
---

## 設計說明

前端以自製 SVG 繪出類似 `git log --graph` 的樹狀視覺化（節點=版本，連線=parent-child），點兩個節點可並排比對 diff（prompt/參數/mask/來源差異）。孤兒節點（orphan）、循環（cycle）、節點數上限（cap）三種邊界狀態都要在 UI 上誠實呈現，不能假裝一切正常。

### 現況核對（2026-07-23 盤點）

`frontend/src/pages/VersionTree.vue` 存在。@PM 登記 M5.10（1481aa5）針對「version-tree cap/self-parent-cycle」補了迴歸測試，顯示邊界狀態確有被處理與驗證，非僅 happy path。

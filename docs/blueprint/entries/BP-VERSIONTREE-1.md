---
id: BP-VERSIONTREE-1
title: Version Tree 資料模型 + DAG/Diff API
system: versiontree
tags: [versioning, dag, diff, api]
status: 已完成
request_verbatim: "版本控制｜線性歷史 + favorite + note + tags（schema 預留 parent_version_id）；版本樹狀 UI｜從線性升級為 parent-child 樹 + diff（spec.md §8.1 資料模型；§8.2.1 Version Tree API，M5.1 BACKEND — 已完成）"
decided_date: 2026-06-13
exec_links:
  - core/generation/service.py
  - core/main.py
done_date: 2026-06-13
origin: "M5.1 commit bc7f5f5（2026-06-13）『feat(versioning): version-tree DAG endpoint + diff + parent-child wiring audit/fix (M5.1)』；@PM 登記稽核確認 refine-accept 路徑已正確接上 parent_version_id"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §8.1 資料模型 / §8.2.1 Version Tree API
---

## 設計說明

原本版本歷史是純線性（一條時間軸），M5.1 升級為 parent-child 樹（DAG）：每個版本可以有多個子版本（例如同一張圖分岔出兩種精修方向），API 提供 `/versions/tree`（整棵樹）與 `/versions/diff`（兩版本間的 prompt/參數/mask/recipe 差異）。`core/generation/service.py` 的 `build_version_graph()`/`build_version_tree()`/`diff_versions()` 是核心邏輯，`core/main.py` 對外暴露 `GET /api/v1/projects/{id}/versions/tree` 與 `/versions/diff`。

### 現況核對（2026-07-23 盤點）

三個方法均存在（`build_version_graph`、`build_version_tree`、`diff_versions`），路由確認掛載（`core/main.py:731` `/versions/tree`、`:748` `/versions/diff`）。spec 特別標注「§8.2.1 Version Tree API (M5.1 BACKEND — 已完成)」；M5.10（1481aa5）補上 cap（2000 節點上限）/self-parent-cycle 迴歸測試，處理循環引用與節點數上限的邊界情況。

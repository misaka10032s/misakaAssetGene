---
id: BP-FRONTEND-1
title: 前端路由工作台
system: frontend
tags: [frontend, router, workbench, vue]
status: 已完成
request_verbatim: "前端路由工作台（spec.md §5.8）"
decided_date: 2026-05-07
exec_links:
  - frontend/src/router/index.ts
  - frontend/src/pages/Projects.vue
  - frontend/src/pages/ProjectWorkspace.vue
  - frontend/src/pages/Chat.vue
  - frontend/src/pages/Assets.vue
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入前端路由骨架，隨後續 M2~M5 各里程碑持續掛載新頁面（VersionTree.vue／IntegrationManager.vue 等）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.8 前端路由工作台
---

## 設計說明

`frontend/src/router/index.ts` 是整個桌面 App 的路由骨架，把「專案列表 → 專案工作台 → 對話 / 素材 / 版本樹 / 整合管理」串成一致的導覽結構，各頁面（`Projects.vue`/`ProjectWorkspace.vue`/`Chat.vue`/`Assets.vue`）圍繞單一當前選定專案（`ProjectManager.select_project()`）運作。

### 現況核對（2026-07-23 盤點）

`frontend/src/router/index.ts` 存在且被 `createRouter`/`RouteRecord` 使用確認（`frontend/src/router/index.ts` 是唯一命中路由建構關鍵字的檔案）。`frontend/src/pages/` 下 6 個頁面（Assets/Chat/IntegrationManager/ProjectWorkspace/Projects/VersionTree）皆存在，對應各自的條目（`BP-INTEGRATION-1`、`BP-VERSIONTREE-2` 等）分別驗證過。

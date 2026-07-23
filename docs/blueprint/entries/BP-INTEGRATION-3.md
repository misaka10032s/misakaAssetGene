---
id: BP-INTEGRATION-3
title: 更新與回滾策略（§9.5，Workers/Tools 版本切換）
system: integration
tags: [integration, update, rollback, deferred]
status: 待做
request_verbatim: "整合管理｜版本檢查、更新、回滾（spec.md §2 Feature Matrix；§9.5 更新與回滾策略）"
decided_date: 2026-05-07
exec_links:
  - core/integration/workers.py
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §9.5 更新與回滾策略
---

## 設計說明

spec §9.5 要求 Workers/Tools 能檢查是否有新版本、一鍵更新到新版、更新失敗或使用者反悔時能回滾到前一個已知堪用的版本（commit/tag）。

### 現況核對（2026-07-23 盤點）

`core/integration/workers.py`（`WorkersService`）目前只有 `install_worker()`（首次安裝時 clone + checkout 到 manifest 指定的 recommended commit），**沒有**任何 `update_worker()`/`rollback_worker()` 或版本切換方法（`git checkout` 只在 `install_worker()` 內出現一次，用於初始安裝，非切版）。即「裝好之後就固定在那個 commit」，沒有版本檢查/更新/回滾的操作介面。狀態：**待做**，與 `BP-INTEGRATION-1`（安裝/啟停/smoke 已完成）明確區分，避免因同屬 `workers.py` 而被誤判為同樣完成度。

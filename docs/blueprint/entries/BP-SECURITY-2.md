---
id: BP-SECURITY-2
title: 專案路徑穿越防護（project_id 白名單驗證）
system: security
tags: [security, path-traversal, validation]
status: 已完成
request_verbatim: "get_project project_id 路徑驗證 — 路徑驗證不足，可能逃出 project root（資安；@PM「剩餘 deferred tails」，參見 M5.3 path-traversal 修法）"
decided_date: 2026-06-20
exec_links:
  - core/project/manager.py
  - tests/test_project_id_validation.py
done_date: 2026-06-20
origin: "commit 062eb13（2026-06-20）『fix(security): whitelist-validate project_id to block path traversal』——晚於 @PM 登記更新的時間點，屬登記漂移（registry drift），本次以程式碼實況為準"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.6 跨專案引用（路徑穿越防護的姊妹修法，M5.3）
---

## 設計說明

`project_id` 若沒有白名單限制，理論上可以塞入 `../../etc/passwd` 之類的路徑穿越 payload，讓後端在 `projects_root` 之外讀寫檔案。修法是在唯一的防禦漏斗（`validate_project_id()`）強制 `^[a-z0-9_-]+$` 格式，路由層依賴注入與 body 帶入的 id（`select_project`/`clarify`/`sessions` 等）全部收斂到同一個函式檢查。

### 現況核對（2026-07-23 盤點，重要發現）

**與 @PM 登記不一致，本次以程式碼為準：** `@PM/projects/misakaAssetGene.md`「剩餘 deferred tails」目前仍列著「get_project project_id 路徑驗證 — 路徑驗證不足，可能逃出 project root」為未解決項，但實際程式碼（`core/project/manager.py:23-36` `validate_project_id()`，`core/project/manager.py:120` `get_project()` 開頭即呼叫）顯示此問題**已於 2026-06-20（commit 062eb13）修復**，並有專屬測試檔 `tests/test_project_id_validation.py` 覆蓋。這是登記漂移（該修復發生在 @PM roadmap 最後一次更新之後，未回寫登記），非本次盤點誤判——已請使用者核對 @PM 登記是否需要同步更新。

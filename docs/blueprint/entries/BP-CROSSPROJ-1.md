---
id: BP-CROSSPROJ-1
title: 跨專案引用解析器 + 匯出重解析
system: crossproj
tags: [cross-project, resolver, external, rw-lock]
status: 已完成
request_verbatim: "跨專案引用｜@project/path#version 語法、_external/ 複本、解析器（spec.md §2 Feature Matrix，P1；§5.6 跨專案引用）（@PM 登記 M5.3，2026-06-13）"
decided_date: 2026-06-13
exec_links:
  - core/project/cross_project.py
done_date: 2026-06-13
origin: "M5.3 commit 508079f（2026-06-13）『feat(cross-project): resolver read-side + export re-resolution + deprecation/materialization tool (M5.3)』；@PM 登記 reviewer 抓到 3 個路徑穿越 BLOCKER（read/export/copy 皆可逃出 project root），已修復（resolve-then-contain, symlink-safe）並經獨立資安覆核 PASS"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.6 跨專案引用（References）/ §5.6.1 _external/ 結構 / §5.6.2 解析順序 / §5.6.5 循環依賴
---

## 設計說明

素材可以用 `@project/path#version` 語法跨專案引用，解析後在本專案的 `_external/` 目錄留一份複本（避免直接依賴外部專案的即時狀態）。`core/project/cross_project.py`（958 行）內建 RW lock（`msvcrt.locking`/`fcntl.flock` 依平台切換）保護複本寫入與 origins.json 更新不被併發破壞，`resolve_reference()` 提供四種狀態（live/outdated/external/broken），`detect_cycles()` 偵測循環引用（僅警告不擋）。

### 現況核對（2026-07-23 盤點）

M5.3 是本專案安全紀錄中最嚴重的一批發現：reviewer 在讀取/匯出/複製三個路徑都抓到路徑穿越漏洞（可逃出 project root），修復方式是「resolve-then-contain」+ symlink-safe 處理，並經過**獨立**的資安覆核（非同一位審查者）才過關。匯出重解析（export re-resolution）原本是 no-op（沒真的重新解析），M5.3 一併修正。

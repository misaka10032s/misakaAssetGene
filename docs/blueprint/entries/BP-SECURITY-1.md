---
id: BP-SECURITY-1
title: 應用層 Log 脫敏過濾器
system: security
tags: [security, logging, redaction, api-key]
status: 已完成
request_verbatim: "app-wide log redaction (API keys + local user paths, MISAKA_LOG_REDACT default on), shared w/ setup（@PM 登記 M5.4，2026-06-14）"
decided_date: 2026-06-14
exec_links:
  - core/logging_config.py
done_date: 2026-06-14
origin: "M5.4 commit 03f3c40（2026-06-14）『feat(security): app-wide log redaction filter — API keys + local paths + env toggle (M5.4)』；reviewer 抓到 BLOCKER（filter 掛在 logger 而非 handler，導致 child-logger 的 app log 從未被脫敏，已用真實案例證實）+ 一個真實併發競態（M5.3 copy guard 的 .resolve() 對不存在的葉節點會誤拒合法寫入，2/13 次重現），兩者皆修復，10/10 次重跑綠燈"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §11.3 友善錯誤處理（API-key redaction 前身，M4.e）/ M5.4 app-wide 擴展
---

## 設計說明

M4.e 原本只在 Setup 友善錯誤路徑做 API key 脫敏（`BP-SETUP-1`），M5.4 把同一套過濾器擴展成整個應用共用的中央過濾器（API 金鑰 + 本機使用者路徑），預設開啟（`MISAKA_LOG_REDACT`），任何 logger 輸出（不限於 setup）都經過同一道防線。

### 現況核對（2026-07-23 盤點）

@PM 登記這是本次盤點中少數「reviewer 用真實案例證明初版有效性完全落空」的案例——過濾器最初掛在單一 logger 物件上，但 Python logging 的 child logger 不會自動繼承 handler 端的 filter，導致除了 setup 自己那條 logger 外，其他模組的 app log 完全沒被脫敏過（審查時實測證實）。修復後（掛到 handler 而非 logger）並經 10/10 次重跑驗證。

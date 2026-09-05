---
id: BP-FRONTEND-2
title: stores/app.ts 拆分（單檔 Pinia store 過大）
system: frontend
tags: [frontend, pinia, refactor, tech-debt, deferred]
status: 已完成
request_verbatim: "stores/app.ts 拆分 — 951 行單檔 Pinia store 需拆模組（可讀性 + 維護）（@PM「剩餘 deferred tails」）"
decided_date: 2026-06-13
done_date: 2026-09-05
exec_links:
  - frontend/src/stores/app.ts
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §5.8 前端路由工作台（狀態管理未獨立成章節，屬其下的實作債）
---

## 設計說明

`frontend/src/stores/app.ts` 是整個前端唯一的 Pinia store，隨著功能增加（訓練 SSE 訂閱、版本樹、License、跨專案引用……）持續往同一檔案疊加，可讀性/維護性下降，需要拆成多個依領域劃分的 store 模組。

### 現況核對（2026-07-23 盤點）

`frontend/src/stores/app.ts` 目前 **895 行**（@PM 登記寫「951 行」，實測數字有出入——可能是登記後又有增減，或估算誤差；差異不影響「單檔過大需要拆分」的結論本身）。實測內容確認裡面同時混雜訓練 SSE 訂閱邏輯（`EventSource`，見 `BP-TRAIN-4`）與其他領域狀態，尚未拆分。狀態：**待做**（純重構債，非功能缺陷，不影響現有功能正確性）。

### 完成紀錄（2026-09-05 全叢集稽核確認）

已拆分為 11 個領域 store，`app.ts` 縮小為 226 行。Commit `24f0d48`（主要拆分）＋
`d7110b4`／`70f3e7e`／`28d147c`（後續調整）。

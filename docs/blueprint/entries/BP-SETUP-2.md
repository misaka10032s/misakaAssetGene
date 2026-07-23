---
id: BP-SETUP-2
title: Tauri 跨平台打包（.msi / .dmg / .AppImage）
system: setup
tags: [setup, tauri, bundler, packaging, deferred]
status: 待判斷
request_verbatim: "Tauri bundler — .msi/.dmg/.AppImage 跨平台打包待使用者決定時程（M4.e deferred；@PM「剩餘 deferred tails」）"
decided_date: 2026-06-13
exec_links:
  - src-tauri/tauri.conf.json
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §1.4 分發模式
---

## 設計說明

spec §1.4「分發模式」預期最終以 Tauri 打包成 `.msi`（Windows）/`.dmg`（macOS）/`.AppImage`（Linux）供終端使用者安裝，而非要求使用者自己跑 `npm run start:dev`。M4.e 決策時，使用者明確選擇「Portable → testable core only（先求核心可測可跑），Tauri 跨平台打包器留到之後」——即這是使用者主動決定延後時程，而非開發卡關。

### 現況核對（2026-07-23 盤點）

`src-tauri/tauri.conf.json` 目前只有基本的 `app.windows`（視窗標題/尺寸）設定，**沒有** `bundle` 區塊（Tauri 打包需要的 `bundle.targets`/icon/installer 設定完全不存在）。狀態判定為「待判斷」而非「待做」——因為這不是遺漏，而是使用者已明確表態「時程待我決定」，下一步是等使用者排定時程，不是逕自排入開發佇列。

---
id: BP-OFFLINE-1
title: 離線模式三態 + Provider Gating
system: offline
tags: [offline, network, provider-gating, auto-detect]
status: 已完成
request_verbatim: "離線模式｜三態（Auto/Always Offline/Always Online）、自動偵測、UI 區分（spec.md §2 Feature Matrix，P1；§11.5 離線模式）"
decided_date: 2026-06-13
exec_links:
  - core/network/service.py
  - core/network/state.py
done_date: 2026-06-13
origin: "M3 分支 pm/m3 @ 7af6066，2026-06-13 fast-forward 合併主線（commit 2e1aa19）；@PM 登記 115 tests passed on clean uv env，含 live-first readiness API 驗證"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §11.5 離線模式 / §5.13 Worker runtime readiness
---

## 設計說明

spec §11.5 定義三態：Auto（自動偵測網路，斷線時提示切換）、Always Offline、Always Online；離線時功能矩陣依模態分級啟用/停用（僅本機 worker 可用的模態維持可用）。§5.13 進一步要求 worker 就緒判斷「live-first」— 優先問 worker 的即時 API 而非只看檔案系統（獨立 ComfyUI 有自己的模型目錄時，純檔案系統判斷會誤判)。

### 現況核對（2026-07-23 盤點）

`core/network/service.py` + `core/network/state.py` 存在，實作三態與網路偵測。@PM 登記 M3 明確標注「live-first worker readiness (§5.13)」與「real evidence: live-first readiness verified via API (4 jobs ready, no local clone)」，並修正一個打包缺陷（`python-multipart` 自 M2c 起未宣告，clean install 會在 `/import` crash，於 7af6066 修復）。Provider gating（離線時限制只用本機 LLM）與 `BP-ROUTER-1` 聯動；VRAM 熱切換三態見 `BP-VRAM-1`（概念上獨立但常一起討論，容易混淆——這裡指的是「網路離線／連線」三態，不是 VRAM 的 Active/Cold/Warm 三態）。

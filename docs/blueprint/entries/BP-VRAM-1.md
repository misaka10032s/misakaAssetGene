---
id: BP-VRAM-1
title: VRAM Scheduler 三態熱切換（Active/Cold/Warm）+ 訓練互斥鎖
system: vram
tags: [vram, scheduler, warm-swap, exclusive-lock]
status: 已完成
request_verbatim: "VRAM Scheduler (Active/Cold)｜模態切換時 VRAM 排程（exclusive/shared）；VRAM Hot Swap (Warm)｜加入 RAM 快取中間態（spec.md §2 Feature Matrix；§3.4 VRAM Scheduler — 三態熱切換）"
decided_date: 2026-06-13
exec_links:
  - core/scheduler/vram.py
done_date: 2026-06-13
origin: "M3（pm/m3@7af6066，2026-06-13）補上 Warm 熱切換；M4.d（1d70031/689007f，2026-06-13）加上硬性不可搶佔訓練互斥鎖 scheduler.begin/end_training，生成路徑經 is_training_locked() 判斷"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §3.4 VRAM Scheduler — 三態熱切換
---

## 設計說明

spec §3.4 定義三態：Active（模型常駐 VRAM）、Cold（完全釋放）、Warm（釋放 VRAM 但快取在 RAM，切換比 Cold 快）。M4.d 追加「訓練互斥鎖」— 訓練跑起來時，生成路徑必須完全讓出 VRAM（硬性不可搶佔，不是優先權排擠），由 `scheduler.begin_training()`/`end_training()` 標記，生成請求進來時先呼叫 `is_training_locked()` 檢查。

### 現況核對（2026-07-23 盤點）

`core/scheduler/vram.py`（328 行）+ `tests/test_vram_scheduler.py`。@PM 登記 M4.d reviewer 抓到 BLOCKER（初版是 pressure-evict 而非真正 exclusive，且生成路徑完全沒諮詢 scheduler）+ 3 個 MAJOR，均已修復後才合併（1d70031→689007f）。

**待覆核（未在程式碼註解中找到對應字樣，來源為 @PM 登記，本次未能獨立確認）：** `@PM/projects/misakaAssetGene.md`「剩餘 deferred tails」列出「VRAM scheduler thread lock 在邊界條件有競態」，但本次於 `core/scheduler/vram.py` 原始碼審視中未找到對應的 TODO/FIXME 註解或已知案例描述，也未能重現。狀態仍標記「已完成」（功能本身已上線且測試通過），但此競態疑慮列為待使用者/後續開發者覆核的開放問題，不代表本次已確認排除。

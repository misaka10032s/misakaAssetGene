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
qa_log:
  - date: 2026-09-07
    q: "登記簿 Todo「VRAM scheduler thread lock —— 當初什麼場景（邊界競態）」：所有 ModelScheduler 方法皆已上鎖，卻找不到當初記錄的競態情境。站主問「解釋給我聽」。PM 稽核定位：競態不在 scheduler 內部，而在呼叫端 `core/training/executor.py:408-427` 的 check-then-lock 空檔——先繞過鎖直讀 `_scheduler._models` 判斷有無 ACTIVE 模型，再呼叫 `begin_training()` 上鎖；空檊中生成請求 `acquire()` 可把模型搬進 VRAM，訓練不重檢即開跑，兩者同時佔顯存。"
    a: "站主原話：「現在修」。已修：新增 `ModelScheduler.try_begin_training(holder)`（`core/scheduler/vram.py:182-212`，ACTIVE 掃描與獨佔上鎖在同一把 `self._lock` 下原子完成），executor 改呼叫該方法、不再直讀 `_models`；模組 docstring 同步改寫。合併 commit `9c88e05`（分支 `fix/vram-scheduler-check-then-lock`，fresh reviewer PASS：`@PM/state/runs/full-audit-260905/review-30-vram-race.md`）。原 Todo 結案。"
tests:
  - date: 2026-09-07
    target: "VRAM 訓練獨佔鎖 check-then-lock 競態（`core/training/executor.py` ↔ `core/scheduler/vram.py`）"
    action: "新增 `tests/test_executor.py::TestHardExclusiveVramLock::test_toctou_race_between_active_check_and_training_lock`（強迫並發 `acquire()` 落在 ACTIVE 掃描結束瞬間）；於修前 parent commit 與修後各跑一次；全套測試與 L0/L1 gate。"
    expected: "修前該測試 FAIL（訓練與 ACTIVE 模型同時存在）、修後 PASS；全套無回歸；gates baseline-matched。"
    result: "PASS。修前 `1 failed in 0.41s`（AssertionError: TOCTOU: training ran while a model was simultaneously ACTIVE）；修後 `1 passed in 0.71s`；全套 `745 passed, 3 skipped`；L0：G1 ruff 178/178、G2 mypy 50/50、G3b PASS、G4 import-cycle 2/2（皆 baseline，0 新增）；G5 diff coverage 90%（門檻 60%）。"
    evidence: "commit `9c88e05`；reviewer 報告 `D:/backup/CSIA/@PM/state/runs/full-audit-260905/review-30-vram-race.md`"
    executor: "sonnet implementer（TDD）＋ 獨立 sonnet reviewer（fresh context）PASS"
---

## 設計說明

spec §3.4 定義三態：Active（模型常駐 VRAM）、Cold（完全釋放）、Warm（釋放 VRAM 但快取在 RAM，切換比 Cold 快）。M4.d 追加「訓練互斥鎖」— 訓練跑起來時，生成路徑必須完全讓出 VRAM（硬性不可搶佔，不是優先權排擠），由 `scheduler.begin_training()`/`end_training()` 標記，生成請求進來時先呼叫 `is_training_locked()` 檢查。

### 現況核對（2026-07-23 盤點）

`core/scheduler/vram.py`（328 行）+ `tests/test_vram_scheduler.py`。@PM 登記 M4.d reviewer 抓到 BLOCKER（初版是 pressure-evict 而非真正 exclusive，且生成路徑完全沒諮詢 scheduler）+ 3 個 MAJOR，均已修復後才合併（1d70031→689007f）。

**待覆核（未在程式碼註解中找到對應字樣，來源為 @PM 登記，本次未能獨立確認）：** `@PM/projects/misakaAssetGene.md`「剩餘 deferred tails」列出「VRAM scheduler thread lock 在邊界條件有競態」，但本次於 `core/scheduler/vram.py` 原始碼審視中未找到對應的 TODO/FIXME 註解或已知案例描述，也未能重現。狀態仍標記「已完成」（功能本身已上線且測試通過），但此競態疑慮列為待使用者/後續開發者覆核的開放問題，不代表本次已確認排除。

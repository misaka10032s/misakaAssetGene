---
id: BP-TRAIN-4
title: 訓練狀態回報 — 輪詢 + SSE 即時推送
system: train
tags: [training, progress, sse, polling]
status: 已完成
request_verbatim: "訓練狀態回報（spec.md §7.3）"
decided_date: 2026-06-20
exec_links:
  - core/main.py
  - core/training/service.py
  - core/project/atomic_io.py
  - frontend/src/api/client.ts
  - frontend/src/stores/app.ts
done_date: 2026-06-20
origin: "commit a0ec366（2026-06-20）『feat(training): SSE progress stream + route-layer project_id guard』——此 commit 晚於 @PM 登記的 M0–M5 全期（M5 已於 2026-06-14 merge 到 main），屬 main 分支上未被登記進 @PM roadmap 的後續硬化提交，本次盤點以程式碼實況為準"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.3 訓練狀態回報
qa_log:
  - date: 2026-09-08
    q: "@PM 待回答 #53 item 2：`core/training/service.py` 的 `_write_jobs` 用 `path.write_text(...)` 原地覆寫 `jobs.json`，而 `TrainingService.stream_job_progress` 從請求執行緒輪詢同一份檔案、executor 的 `on_progress`（`core/training/executor.py:480-486`／`_maybe_report_progress` :811-822）在背景 worker 執行緒下 `_lock` 寫入進度——輪詢方有機會讀到寫到一半的半殘 JSON，觸發 `JSONDecodeError`。是否修、怎麼修？"
    a: "站主原話裁示（2026-09-07）：寫端改『先寫暫存檔、`os.replace` 原子覆蓋』；讀端『容忍：解析失敗保留上一份記憶體快照或重試一次，不要直接崩潰』。明確排除：不做進度節流、不做 SSE 改動、不改變回報頻率。已完成：新增共用工具 `core/project/atomic_io.py`（`write_json_atomic`／`read_json_tolerant`），套用到 `core/training/service.py`（`_write_jobs`/`_read_jobs`，本次問題的直接對象）、`core/generation/service.py`（`_write_jobs`/`_read_jobs`、`_write_assets`/`_read_assets`、`_write_plans`/`_read_plans` —— 同樣是輪詢方＋寫入方共用一份 JSON 的狀態檔）、`core/integration/workers.py`（`_save_install_state`/`_load_install_state`、`_save_runtime_state`/`_load_runtime_state` —— worker 安裝/執行期狀態，原本『解析失敗』會靜默吞成 `{}`，現在改走同一套重試＋快照容錯）、`core/project/manager.py`（`project.json`／`.current_project.json`／`conversation.json` 的所有讀寫點）、`core/project/cross_project.py`（`_external/origins.json` 寫入端＋跨專案參考掃描 `assets/index.json`／`jobs.json` 的唯讀掃描）。未動：`core/project/portability.py` 的匯入寫入（一次性、目標專案在寫完前對外不可見，非併發讀取對象）、`core/generation/service.py` 內的 consultant plan `.md` 匯出（非 JSON、一次性文件）。"
  - date: 2026-09-08
    q: "覆核 commit `d4a6ddc`（上一則 qa_log 的修復）的複核報告指出三項缺口：(1) `core/project/portability.py` 匯入流程宣稱『目標專案在寫完前對外不可見，非併發讀取對象』而未套用原子寫入——但 `target_dir = projects_root / new_id` 一建立，`list_projects()`/`get_project()` 就用 `projects_root.glob(\"*/project.json\")` 掃描、完全沒有 readiness marker，`project.json` 一旦落地即對併發請求可見，且該檔原本會先被壓縮檔內的原始 entry 原地覆寫一次（非原子）、再被正確資料覆寫第二次（也非原子）——『非併發』前提是錯的；(2) `core/integration/workers.py` 的 `_load_runtime_state`/`_load_install_state` 原本『解析失敗一律吞成 `{}`』，改用 `read_json_tolerant` 後，若從未成功讀過且已損毀，會把例外拋進呼叫端（8 個呼叫點都沒包 try/except），比修前更嚴格，未經站主明確認可就收緊了容錯；(3) `atomic_io._last_good_snapshots`/`_last_warning_at` 兩個模組級快取無上限，長期執行下可能無限增長。是否修？"
    a: "三項全部修正。(1) `skip_entries` 加入 `\"project.json\"`：壓縮檔內的原始 `project.json` entry 永不被解壓縮落地，最終 `project.json` 改用 `write_json_atomic` 寫一次到位（`new_project_data` 本就是從 manifest 的 `project` 欄位算出，與壓縮檔內的原始 entry 內容相同，跳過原始 copy 不會遺失資料）；碰撞偵測掃描其他專案 `project.json` 的讀取（原本 `json.loads(pjson.read_text())`，外層已包 `except Exception: pass` 保底）也換成 `read_json_tolerant`，維持『每個狀態檔讀取點都走容錯』的一致性。上一則 qa_log『目標專案在寫完前對外不可見』的說法已確認錯誤，特此更正：`project.json` 落地的瞬間即對 `list_projects()`/`get_project()` 可見，沒有任何 readiness gate。(2) `read_json_tolerant` 新增 `default=` 具名參數（用 sentinel 物件判斷『呼叫端有沒有傳』，讓 `default=None` 也能是合法的明確預設值，不會跟『沒傳』搞混）：無快取又損毀時，若呼叫端有傳 `default=`，記一次 WARNING 後回傳該值；沒傳則照舊往外拋例外。`core/integration/workers.py` 的兩個讀取點改傳 `default={}`，恢復修前的呼叫端契約（無快取損毀 → 靜默降級 `{}` 並記警告，不再把例外拋進 FastAPI route）；`core/training/service.py` 的 `_read_jobs` 維持不傳 `default=`——它絕不可靜默回傳空 job 清單。(3) `_last_good_snapshots`/`_last_warning_at` 改成 `_MAX_TRACKED_PATHS=512` 的 LRU（`OrderedDict`，鍵被存取時移到尾端，超過上限淘汰最舊的鍵）。"
tests:
  - date: 2026-09-08
    target: "`jobs.json`（及其他共用 JSON 狀態檔）併發讀寫下的原子性與讀端容錯（`core/project/atomic_io.py`）"
    action: "新增 `tests/test_atomic_jobs_io.py`：(1) 修復證明——1 條寫入執行緒透過真正的 `TrainingService._write_jobs`／`_read_jobs` 對一份 ~20-30KB（24 筆 job，含 stderr_tail／note 填充）payload 連續改寫 2000 次，同時 4 條讀取執行緒零延遲緊迴圈呼叫 `_read_jobs`，斷言每次讀取皆為完整合法的 job 清單、過程零 corrupt read、結束後不留 `*.tmp` 殘檔；(2) 對照組——原樣重現修前的 `write_text` 原地覆寫＋無重試 `json.loads`，證明同一套併發情境『的確會』觸發 corrupt read（若環境因 Windows 檔案快取而剛好未觸發，測試會明確標記為 documentation-only 並 skip，而不是假裝通過）；(3) 單元測試——`os.replace` 失敗時原檔案不受影響且暫存檔會清乾淨、`json.dumps` 失敗時暫存檔清乾淨、讀到損毀檔會退回上一份記憶體快照、從未成功讀過且損毀則照舊往外拋例外。過程中另外發現並修正：`os.replace` 在四執行緒零延遲緊迴圈對讀之下會出現暫時性 `PermissionError`（Windows 特性），一開始用『指數退避到 20ms 上限、5 秒總預算』反而讓可嘗試次數變少、修復證明測試會真的寫入失敗——改回『固定 1ms 間隔、10 秒總預算』後穩定在數百次嘗試、次秒內收斂；讀端同理補上對 `PermissionError`/`OSError` 的重試＋快照回退（原本只接 `JSONDecodeError`/`UnicodeDecodeError`，漏接這個瞬時性錯誤會讓讀取方直接崩潰，等於用另一種例外重現同一類故障）。也修正 `core/integration/workers.py` 內新增的 import 敘述位置（改成函式內 deferred import）以免把 `list_workers()` 既有 mypy `no-redef` baseline 訊息中內嵌的行號往下推一行、被誤判為新增的 mypy finding。"
    expected: "修復證明測試 0 corrupt read、無殘留 tmp 檔；對照組能重現 corrupt read（或明確 skip 並附上嘗試次數）；四則單元測試皆過；全套 Python L0/L1 與既有測試 0 回歸。"
    result: "PASS。修復證明測試連跑 3 次皆 `8 passed`（同檔內含修復證明＋對照組＋4 則單元測試），單次約 39-40 秒；對照組其中一次跑出 1577 個 corrupt read（`[control] pre-fix code produced 1577 corrupt read(s) across 1 attempt(s): [1577]`），證明該測試具備『抓得到修前缺陷』的能力。全套 gate：`[G1] PASS - 177 total ruff violation(s), 0 new vs baseline (177 pre-existing).`／`[G2] PASS - 50 total mypy error(s), 0 new vs baseline (50 pre-existing).`／`803 passed, 3 skipped, 3 warnings in 92.44s (0:01:32)`／`[G3b] PASS - 1 changed test file(s), all touched test functions assert something (base e7226ba1e0893feb0997efccd071c91e2846ea4a).`／`[G4] PASS - 2 total cycle-breaking edge(s), 0 new vs baseline (2 pre-existing).`／`[G5] PASS - diff coverage >= 60% vs e7226ba1e0893feb0997efccd071c91e2846ea4a.`。前端未變動，未跑 JS/TS gate。"
    evidence: "分支 `fix/q53-atomic-jobs`；本次 dispatch 報告 `D:/backup/CSIA/@PM/state/runs/q53-mag-atomic-jobs/implement.md`"
    executor: "sonnet implementer"
  - date: 2026-09-08
    target: "複核報告三項 finding 的修正（portability.py 匯入非原子寫入／workers.py 讀端契約收緊未經認可／atomic_io 快取無上限）"
    action: "`core/project/portability.py`：`skip_entries` 加入 `project.json`，最終寫入改 `write_json_atomic`，碰撞偵測讀取改 `read_json_tolerant`。新增 `tests/test_portability.py::test_import_malformed_project_json_entry_never_extracted_verbatim`——壓縮檔塞一個刻意截斷的 `project.json` entry，攔截 portability 模組每一次二進位寫入 `open()` 呼叫，斷言從未以 `project.json` 為檔名開檔寫入（而非只驗證最終內容合法，避免『事後被第二次寫入覆蓋掉』式的偽陽性），並確認最終落地內容等於 atomic 寫入的 `new_project_data`。`core/project/atomic_io.py`：`read_json_tolerant` 新增 `default=`（sentinel 判斷有無傳入，`default=None` 視為合法明確值）；`core/integration/workers.py` 的 `_load_runtime_state`/`_load_install_state` 改傳 `default={}`；`core/training/service.py` 的 `_read_jobs` 維持不傳。新增 `tests/test_atomic_jobs_io.py`：`default=` 語意四則（無快取有 default 回退並記警告、無快取無 default 照舊拋例外、有快取時 default 不生效、`default=None` 視為明確值）＋ `test_training_service_read_jobs_corrupt_with_no_snapshot_still_raises`（經由真正的 `TrainingService._read_jobs`，不只測底層函式）。`tests/test_workers_readiness.py` 新增三則：`_load_runtime_state`/`_load_install_state` 損毀無快取回 `{}` 並記 WARNING；`list_workers()`（`GET /api/v1/integration` route 實際呼叫的函式）在同樣情境下不拋例外，證明該 route 不會因此 500。`_last_good_snapshots`/`_last_warning_at` 改 `_MAX_TRACKED_PATHS=512` 的 LRU，新增 `tests/test_atomic_jobs_io.py::TestReadJsonTolerantCacheEviction` 兩則（monkeypatch 上限降到 3，避免真的建立 500+ 個檔案），驗證兩個快取都會淘汰最舊路徑、只保留最近觸碰的鍵。"
    expected: "3 項複核 finding 全部有對應修正與測試；全套 Python L0/L1 0 新增；新增測試皆為真斷言（無鬆綁既有斷言、無 skip/xfail）。"
    result: "PASS。新增 11 則測試（`test_atomic_jobs_io.py` +7、`test_portability.py` +1、`test_workers_readiness.py` +3）。`[G1] PASS - 177 total ruff violation(s), 0 new vs baseline (177 pre-existing).`／`[G2] PASS - 50 total mypy error(s), 0 new vs baseline (50 pre-existing).`／`814 passed, 3 skipped, 3 warnings in 56.85s`（原 803 passed + 11 則新測試）／`[G3b] PASS - 3 changed test file(s), all touched test functions assert something (base e7226ba1e0893feb0997efccd071c91e2846ea4a).`／`[G4] PASS - 2 total cycle-breaking edge(s), 0 new vs baseline (2 pre-existing).`／`[G5] PASS - diff coverage >= 60% vs e7226ba1e0893feb0997efccd071c91e2846ea4a.`。Blueprint lint：`lint OK (1 warning(s))`（同一則既存 `BP-REFINE-2.md` 篇幅警告，與本次變更無關）。前端未變動，未跑 JS/TS gate。"
    evidence: "分支 `fix/q53-atomic-jobs`；複核報告 `D:/backup/CSIA/@PM/state/runs/q53-mag-atomic-jobs/review.md`；本次修復報告 `D:/backup/CSIA/@PM/state/runs/q53-mag-atomic-jobs/implement.md`『## Fix pass (F1–F3)』"
    executor: "sonnet implementer (fix pass)"
---

## 設計說明

spec §7.3 要求前端能即時看到訓練進度。原始設計是 GET 輪詢（`GET /api/v1/projects/{id}/training/{job_id}`），2026-06-20 追加 Server-Sent Events 推送端點（`GET /api/v1/projects/{id}/training/{job_id}/stream`），executor 把增量狀態（status/progress/label 變化）持久化後由此端點逐幀推送 `event: progress`，終態送 `event: done`，前端改用 `EventSource` 訂閱取代輪詢。

### 現況核對（2026-07-23 盤點）

**重要發現（與 @PM 登記不一致，本次以程式碼為準）：** `@PM/projects/misakaAssetGene.md`「剩餘 deferred tails」仍列著「訓練進度串流 — 目前用 GET 輪詢；改為 SSE/WS 推送（M4.d deferred）」，但實際程式碼（`core/main.py:857` `stream_training_job`、`core/training/service.py` `stream_job_progress`、前端 `frontend/src/api/client.ts:565` `trainingJobStreamUrl` + `frontend/src/stores/app.ts:483` 的 `EventSource` 訂閱）顯示 SSE 推送**已經前後端雙向落地**，並有 `tests/test_training_stream.py` 契約測試。這是 2026-06-20 的後續提交（a0ec366），晚於 @PM 登記更新的時間點，屬**登記漂移（registry drift）**，非本次盤點誤判。程式碼內建的 docstring 誠實自陳：「REAL-RUN NOTE: the push path is contract/unit-tested with a fake job store... End-to-end verification against a live kohya_ss / GPT-SoVITS GPU training run is DEFERRED to the user.」——即推送機制本身已完成，但仍待真實 GPU 訓練跑一輪來驗證端到端（見 `BP-TRAIN-6`）。

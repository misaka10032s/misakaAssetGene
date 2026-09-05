---
id: BP-INTEGRATION-1
title: Tools & Workers 整合管理（安裝/啟停/健康檢查/Smoke Test）
system: integration
tags: [integration, workers, tools, lifecycle, smoke-test]
status: 已完成
request_verbatim: "整合管理 (Tools+Workers+Models)｜統一選單腳本 + WebUI：版本檢查、更新、回滾、smoke test（spec.md §2 Feature Matrix，P0；§3.3 Tools & Workers 整合管理架構；§9.1 統一選單腳本；§9.4 WebUI 整合；§9.6 Smoke Test）"
decided_date: 2026-05-07
exec_links:
  - core/integration/workers.py
  - frontend/src/pages/IntegrationManager.vue
  - scripts/smoke
done_date: 2026-05-07
origin: "M1 commit ca99f10（2026-05-07）首次引入 core/integration/；WorkersService 的安裝/啟停/健康檢查/smoke 方法群持續擴充至 M3（worker readiness，見 BP-OFFLINE-1）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §3.3 Tools & Workers 整合管理架構 / §9.1 統一選單腳本 / §9.4 WebUI 整合 / §9.6 Smoke Test
---

## 設計說明

`core/integration/workers.py`（`WorkersService`，575 行）是「Tools+Workers+Models」三類被管理物件中 Workers 這條的核心：`list_workers()`（列出 + 快取 2 秒）、`install_worker()`（git clone --filter=blob:none + checkout 推薦 commit + 執行安裝指令）、`start_worker()`/`stop_worker()`（子行程生命週期）、`smoke_test_worker()`（跑 `scripts/smoke/<worker>.py`）、健康檢查（HTTP 探活 + PID 存活雙重判斷）。前端 `IntegrationManager.vue` 是對應 WebUI（§9.4），對外路由 `POST /api/v1/workers/{name}/install|start|stop|smoke`。

### 現況核對（2026-07-23 盤點）

`scripts/smoke/` 下 6 個 worker 各有獨立 smoke 腳本（ace_step/comfyui/gpt_sovits/stable_audio_tools/ultimate_rvc/voxcpm），`core/main.py` 確認掛載 install/start/stop/smoke 四組路由（`core/main.py:1267-1320`）。§9.1「統一選單腳本」在桌面 App 情境下以此 WebUI + 路由取代傳統 CLI 選單腳本形式落地。

### 修正記錄（2026-09-05）

`workers/manifest.json` 的 `ace-step.start_cmd` 原為 `python -m acestep.api.server_cli --host 127.0.0.1 --port 8190 --no-init`，經實測（pinned clone `d61c7ac`，即 manifest 內 `recommended.commit`）發現 `acestep/api/server_cli.py` 沒有 `if __name__ == "__main__":` guard，`python -m` 執行後不會啟動任何伺服器（靜默結束，exit 0，`--help` 無任何輸出）。實際入口是 `acestep/api_server.py`（有 `if __name__ == "__main__": main()`，line 365），也對應該 clone `pyproject.toml` `[project.scripts]` 的 `acestep-api = "acestep.api_server:main"` console-script。已修正為 `python -m acestep.api_server --host 127.0.0.1 --port 8190 --no-init`（`fix/ace-step-start-cmd` 分支）。`core/integration/workers.py:_normalize_start_command`（~L485）僅替換 `python` 執行檔為 worker venv 直譯器，不解析/改動模組路徑，本次修正不影響該函式。

### 修正記錄（2026-09-05，Windows PID 存活探測缺陷）

測試證據：`D:/backup/CSIA/@PM/state/runs/misakaAssetGene-gen-test-260904/D-report.md` § E。
原 `_resolve_managed_pid`（舊 `core/integration/workers.py:352`）用 `os.kill(pid, 0)` 做「PID
是否存活」的存活檢查。CPython 官方文件（`Doc/library/os.rst`）明載：Windows 上
`CTRL_C_EVENT`/`CTRL_BREAK_EVENT` 這兩個特殊訊號值（數值分別為 0、1）會走
`GenerateConsoleCtrlEvent`，廣播給整個 console process group，其餘任意訊號值才會走
`TerminateProcess`（單一 process 的真正終止）。`sig=0` 與 `signal.CTRL_C_EVENT` 數值相同，
因此 `os.kill(pid, 0)` 在 Windows 上並非無副作用的純探測，而是走廣播分支。實測：一次
`consultant/clarify` 呼叫觸發 `_build_worker_blocking_reason → readiness_note → get_worker →
_build_snapshot → _resolve_managed_pid`，這條路徑在每次呼叫時都無條件執行（不受健康檢查結果
或任何分支門檻限制），呼叫當下同時（a）App 內部拋出 `SystemError`（HTTP 500）、（b）正在跑、
已載入 14GB VRAM 的 ACE-Step worker 被靜默終止（worker 自身 log 無 crash traceback、無
shutdown 訊息，`netstat`/`Get-Process` 證實程序消失）。

修復：`_pid_alive(pid)`（`core/integration/workers.py`，模組層級函式）取代
`os.kill(pid, 0)`——Windows 走 `ctypes` 的 `OpenProcess`
(`PROCESS_QUERY_LIMITED_INFORMATION`) + `GetExitCodeProcess`
(`STILL_ACTIVE=259`) + `CloseHandle`，全程只查詢不送任何訊號；POSIX 維持
`os.kill(pid, 0)`（該平台上 `sig=0` 無特殊語意，是文件記載的純存活/權限探測）。
`stop_worker`（L~155-166）的 `os.kill(pid, signal.SIGTERM)` 經核對**不受影響、無需修改**：
`SIGTERM` 數值（15）不等於 `CTRL_C_EVENT`(0)/`CTRL_BREAK_EVENT`(1)，故走
`TerminateProcess` 分支——是針對單一 pid 的目標式終止，本來就是 `stop_worker` 想要的行為。

測試：`tests/test_pid_liveness.py`（新增）——本機 pid 判活 True、剛結束的子行程判活 False，
以及 Windows 專屬迴歸測試（patch `os.kill` 成一呼叫就 `AssertionError`，確認 `_pid_alive` 在
Windows 上完全不觸碰 `os.kill`）。

# 技術研究日誌 (RESEARCH_LOG.md)

> **專案：** MisakaAssetGene  
> **日期：** 2026-04-22  
> **目的：** 整合市場研究、防止重複造輪子、記錄規格優化建議。

---

## 1. 市場研究與競品對比

為了確保專案的獨特性並借鑒成功經驗，開發團隊應參考以下 Repo：

| 工具 | 核心強項 (可借鑒) | 我們的區隔 (Unique Value) |
| :--- | :--- | :--- |
| **[Pinokio](https://github.com/pinokiocomputer/pinokio)** | LScript (JSON) 腳本化安裝邏輯。 | 我們專注於「創作工作流」與「專案記憶」，非單純工具箱。 |
| **[Stability Matrix](https://github.com/Lykos-Main/Stability-Matrix)** | Embedded Python 管理與 ComfyUI 整合。 | 我們是跨模態的 (音訊/影片/LLM)，並具備 Agent 顧問。 |
| **[Ollama](https://github.com/ollama/ollama)** | 模型 Hot Swap (VRAM ↔ RAM) 邏輯。 | 我們處理全方位的硬體排程 (Worker vs LLM)。 |
| **[AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)** | 桌面端 RAG 實作。 | 我們的 RAG 服務於「參數產出」，而非純問答。 |
| **[Eagle.app](https://eagle.cool/)** | 素材標籤、Metadata 管理的 UX。 | 我們具備生成與訓練能力，不只是資產庫。 |

---

## 2. 推薦使用的現成組件 (Don't Reinvent the Wheel)

為了提高開發效率，禁止自行實作以下基礎功能，應優先調用成熟庫：

- **環境隔離：** 強制使用 **`uv`** (`uv python install`, `uv pip install`)。
- **硬體監控：** **`GPUtil`** (GPU 資訊) + **`psutil`** (系統 RAM/CPU)。
- **元數據 (Metadata) 嵌入：**
  - 圖像：`Pillow` (PIL)。
  - 音訊：**`mutagen`** (支援所有主流格式，無編解碼器依賴)。
  - 影片：封裝 `ffmpeg` 二進位檔。
- **LLM 通訊：** 內部路由全面採用 **`OpenAI API Format`** 協定。
- **向量數據庫：** **`ChromaDB`** (本地嵌入式，無需 Server)。

---

## 3. 規格書 v0.3 之優化與小缺失修正

在開發實作時，請將以下建議納入考量：

1.  **VRAM 緩存策略 (Hot Swap)：** 實作時需加入「模型熱切換」邏輯。若 LLM 閒置超過 5 分鐘且生成模型需要 VRAM，應自動將 LLM 移至 System RAM (`model.to('cpu')`)。
2.  **路徑相對化：** 實作 `portability.py` 時，必須在寫入 `project.json` 前對所有路徑進行 `os.path.relpath` 處理。
3.  **冷啟動種子：** 在 `cold_start.py` 預置行業標準模板 (RPG/FPS/VN)，確保 LLM 失聯或使用者初次使用時仍有導引。
4.  **文件讀寫鎖 (RW Lock)：** 針對「跨專案引用」，在更新 `_external/` 複本前必須檢查原始檔案是否被佔用，避免寫入衝突。**已完成**：以 lock-file + threading.Lock 雙層策略實作於 `cross_project.py`，Windows 用 `msvcrt.locking`、POSIX 用 `fcntl.flock`，無需第三方套件；已通過 12 執行緒並發測試驗證無資料損毀。
5.  **日誌脫敏（完整實施，M5.4 完成）：** 所有 Error Log 寫入 `setup.log` 前必須脫敏，禁止記錄使用者的本地路徑與 API Key。**已完成**：M4.e 完成 setup.log 脫敏；M5.4 擴充至全 app（見下方 §15）。

---

## 4. 2026-04-23 — Claude 工作流與開源治理補充

- 已建立 `CLAUDE.md` 與 `.claude/` 結構，角色分工對齊 `.plan/DEVELOPMENT_PLAN.md`。
- 已明確化流程：新需求先以 `docs/superpowers/specs/spec.md` + 架構師角色討論可行性與實作細節，再回寫 `docs/superpowers/specs/spec.md`。
- 已補充多模態複合素材需求，納入圖像、文字台詞、角色語音、歌曲、影片與靜態動圖的組合交付。
- 已補上 repo 邊界規則：第三方 repo 維持獨立 clone，不納入本專案 git 追蹤。
- 已新增 `.gitignore`、`CONTRIBUTING.md`、`LICENSE`。

**狀態：已完成**

---

## 5. 2026-06-12 — PM 決策記錄

以下為專案負責人 2026-06-12 正式決策事項，已同步更新至 `docs/superpowers/specs/spec.md` v0.9 與 `.plan/DEVELOPMENT_PLAN.md`。

### 5.1 顧問 session 狀態 → SQLite 持久化
**問題**：顧問 checklist loop（§4.1）的狀態在 app 重啟後是否保留？  
**結論**：**已完成決策**。Session 狀態必須 server-side 持久化於 `memory.sqlite`（`sessions` 表）；stateless per-call 明確拒絕；屬 M2 範疇。詳見 spec §4.1.1。

### 5.2 M4 完整對應 spec §7.1.1
**問題**：M4 訓練整合的範疇是否包含完整多角色 LoRA 工作流？  
**結論**：**已完成決策**。M4 = 完整 §7.1.1（character sheets、dataset packs、training recipes、LoRA stack presets）+ kohya_ss LoRA + TTS fine-tune + Portable Release + Setup 錯誤 AI 解釋。已更新 `.plan/DEVELOPMENT_PLAN.md`。

### 5.3 `project_profile` schema 缺口修正
**問題**：spec §5.9 定義 `project_profile` 但 `project.schema.json` 缺少此欄位。  
**結論**：**已完成**。`project_profile` 已加入 schema 為 optional enum 欄位（game / novel / character_factory / mixed_ip），backward compatible，現有 project.json 無需遷移。

### 5.4 流程節奏
**結論**：每個里程碑須有使用者驗收 checkpoint；實作走 implementer → independent reviewer chain。已寫入 `.plan/DEVELOPMENT_PLAN.md §2.3`。

### 5.5 里程碑 ↔ Spec Gap 對齊
**結論**：M2–M5 的 bullet 點已重新對齊至各 spec gap（§5.11、§5.12/§4.1.1、§5.13、§5.14、§6.2、§7.1.1、§8.2）。詳見 `.plan/DEVELOPMENT_PLAN.md §4`。

**狀態：已完成**

---

## 6. 2026-06-13 — M3(b) 離線三態 + VRAM Warm

### 6.1 VRAM Scheduler Warm 實作範圍（§3.4 釐清）
**問題**：§3.4 未明確界定 Warm/Active/Cold 治理哪些模型；ComfyUI 等外部 worker 自管顯存，排程器是否該插手？  
**結論**：**已完成**。三態僅治理 in-process 受管模型（本機 LLM / embedding 權重）；外部 HTTP worker 不在範圍。已實作 `core/scheduler/vram.py` `ModelScheduler`（budget 可注入、clock 可注入、transition log），轉移：Active→Warm（idle/VRAM 壓力）、Warm→Active（快速還原）、Warm→Cold（idle/RAM 壓力）、RAM<16GB 跳過 Warm。spec §3.4 + v0.9.2 changelog 已標註。11 條 transition matrix 測試通過。

### 6.2 離線「有效三態」（§11.5 釐清）
**問題**：§11.5 定義三**模式**（Auto/Always Offline/Always Online），但 gating 需要的是執行期**有效狀態**；degraded（cloud 斷但本機 LLM 在）未明確命名。  
**結論**：**已完成**。導入有效狀態 ONLINE / DEGRADED / OFFLINE（`core/network/state.py`）。`NetworkStateService` 由模式 + cloud/local 探測解析狀態並記錄切換；`core/llm/router.py:gate_providers` 在非 ONLINE 時將 cloud provider 標記 DISABLED。API snapshot 新增 `state`/`local_available`/`recent_transitions`，前端設定頁顯示 badge 與切換記錄。spec §11.5 + v0.9.2 changelog 已標註。12 條測試通過。

**狀態：已完成**

---

## 7. 2026-06-13 — M4.a §7.1.1 四實體 SQLite CRUD 設計決策

### 7.1 儲存方案選擇（SQLite vs JSON 檔案）
**問題**：CharacterSheet / DatasetPack / TrainingRecipe / LoraPreset 應存在 SQLite 還是 JSON 檔案？  
**結論（PM 已決策）**：統一存入各專案資料夾內的 `memory.sqlite`（與顧問 sessions 表同一檔案），不使用 JSON 檔案。  
理由：事務性一致性、並發安全（WAL 模式）、project scoping 自然分離、與 SessionStore 模式一致。

### 7.2 表結構設計
四張表（`character_sheets`, `dataset_packs`, `training_recipes`, `lora_presets`）均遵循 SessionStore 慣例：
- 所有 array / object 欄位序列化為 JSON TEXT
- `created_at` / `updated_at` 以 ISO 8601 TEXT 儲存
- `project_id` 有索引，CRUD 操作全部帶 `AND project_id=?` 以確保 project scoping isolation
- WAL mode + busy_timeout=5000ms（與 SessionStore 相同）

### 7.3 訓練執行延後
kohya_ss executor / 訓練觸發邏輯延後至 M4.c，不在本 sub-phase 內。

### 7.4 API 路徑
- `/api/v1/projects/{project_id}/characters` (CharacterSheet)
- `/api/v1/projects/{project_id}/dataset-packs` (DatasetPack)
- `/api/v1/projects/{project_id}/training-recipes` (TrainingRecipe)
- `/api/v1/projects/{project_id}/lora-presets` (LoraPreset)

各實體支援 GET (list) / POST (create) / GET :id / PATCH :id / DELETE :id。

### 7.5 驗證
`uv run --extra dev pytest tests/ -q` → 155 passed, 0 failed。  
新增 40 個測試（`tests/test_asset_store.py`）涵蓋：store 單元測試（create/get/list/update/delete、project scoping、persistence across reopen、404 路徑）+ API 路由測試（TestClient）。

**狀態：已完成**

---

## 9. 2026-06-13 — M4.b 顧問訓練流擴充（BACKEND ONLY）

### 9.1 訓練意圖偵測（Training-intent detection）
**問題**：顧問如何在不依賴 LLM 的情況下偵測使用者意圖為訓練 / 角色工廠工作流？  
**結論**：採用關鍵詞集合（訓練、LoRA、lora、資料集、kohya、trigger word、gpt-sovits、voice clone、聲線複製、角色工廠等）搭配 `re.compile` 在 `planner._is_training_intent()` 中靜態判斷。偵測在 `_infer_modalities()` 優先執行，避免訓練相關 prompt 被誤判為 IMAGE 模態。

### 9.2 TRAINING modality 與 checklist
**問題**：是否應新增一個獨立的 `TRAINING` modality 還是複用既有 modality？  
**結論**：新增 `Modality.TRAINING = "training"`，將訓練流嵌入既有 state machine 與 session store 的 checklist/slots 機制，不發明平行系統。`REQUIRED_SLOTS["training"]` 定義四個必填 slot：`character_sheet`、`dataset_pack`、`training_recipe`、`lora_preset`；`i2v_recipe` 為選填，缺席不阻擋 checklist 完成。

### 9.3 TrainingSuggestionCard schema（spec §4.4）
**問題**：建議卡片的後端資料形狀如何定義？  
**結論**：新增 Pydantic model `TrainingSuggestionCard`（欄位：`entity_kind`、`action`、`prefilled`、`reason`、`existing_id`）於 `core/models/schemas.py`。顧問在分析時為每個缺失的必填實體產生一張卡片，`existing_id` 預設 None（不查 DB，由前端決定是否比對）。前端渲染為可點擊按鈕，建議卡片**不**自動執行建立（§4.4）。

### 9.4 Plan 實體 ID 引用（spec §5.12 / §7.1.1）
**問題**：training plan 如何引用已選擇的實體 ID？  
**結論**：`ConsultantAnalysis` 新增五個 training 擴充欄位（`is_training_flow`、`training_character_sheet_id`、`training_dataset_pack_id`、`training_recipe_id`、`training_lora_preset_id`、`training_i2v_recipe_id`）。在 Summary → Generate 邊（`engine._run_clarify`）從 `session.slots` 取出各 slot 值並以 `model_copy(update=...)` 填入 plan，使 plan 成為帶 entity ID 引用的結構化規劃資料。非訓練流時所有欄位為空值，向後相容。

**M4.b review fix（da2f7fa → 修正）**：原實作的 slot 合併只允許 `REQUIRED_SLOTS` 鍵，導致 `i2v_recipe` 被靜默丟棄，`training_i2v_recipe_id` 永遠為 `None`（truthful-delivery 缺陷）。修正：在 `state_machine.py` 新增 `OPTIONAL_SLOTS` dict 與 `optional_slots_for()` accessor；`engine.py` slot 合併改為允許 required ∪ optional 鍵集，選填 slot 得以持久化進 `session.slots`。Checklist 完成判定（`is_checklist_complete` / `missing_slots`）仍只查 `REQUIRED_SLOTS`，`i2v_recipe` 缺席不阻擋流程。`training_i2v_recipe_id` 在 user 提供 `i2v_recipe` slot 時從 `session.slots` 填入 plan；未提供時維持 `None`。

### 9.5 執行步驟（§7.1 sequence）
**問題**：訓練流的 execution_steps 應涵蓋哪些階段？  
**結論**：6 個必選步驟：(a) 確認 CharacterSheet → (b) 確認 DatasetPack → (c) 確認 TrainingRecipe → (d) 確認 LoraPreset → 訓練 LoRA（kohya_ss） → 批次生成（comfyui）。若 prompt 含語音克隆關鍵詞，插入 GPT-SoVITS 步驟；若含影片關鍵詞，附加 i2v 步驟。訓練執行（M4.c）不在此 phase。

### 9.6 驗證
`uv run --extra dev pytest tests/ -q` → **214 passed, 0 failed**（含 43 個測試在 `tests/test_training_flow.py`）。涵蓋：訓練意圖偵測 14 cases、REQUIRED_SLOTS 驗證 4 cases、checklist 進展 5 cases（含 optional slot 缺席不阻擋）、plan 實體 ID 引用 7 cases（含 `training_i2v_recipe_id` 填入與 None 兩向）、建議卡片發射 9 cases、summary/next_step 3 cases、session persistence 1 case。M4.b review fix（optional i2v_recipe slot 持久化）已驗證通過。

**狀態：已完成**

---

## 8. 2026-06-13 — M4.a 第五實體 ImageToVideoRecipe 補齊

**問題**：§7.1.1 規格定義五個實體，但 M4.a 初始 commit `b997a3c` 只實作了四個，遺漏了 `image-to-video recipes`。  
**結論**：**已完成**。新增第五實體 `ImageToVideoRecipe`（SQLite 表 `i2v_recipes`）。欄位：`name`, `workflow_kind`, `frames`, `fps`, `motion_strength`, `notes`。來源 accepted image 在套用時傳入，不存在 recipe 內（可重用模板設計）。路由：`/api/v1/projects/{project_id}/i2v-recipes`（5 個 CRUD 端點）。§7.1.1 儲存模型表已更新。測試涵蓋 store 單元 + API 層，全套通過。

**狀態：已完成**

---

## 10. 2026-06-13 — M4.d Training Execution Layer (executor + command contract + VRAM exclusive lock)

### 10.1 Executor design

**問題**：kohya_ss / GPT-SoVITS 的訓練 job 需要 FIFO 執行、獨佔 VRAM、可中斷，如何與既有 scheduler 整合？

**結論（已實作 + review fix 2026-06-13）**：

`core/training/executor.py` — `TrainingExecutor`：
- FIFO `queue.Queue`，background daemon thread，one job at a time
- `read_jobs(project_id)` / `write_jobs(project_id, jobs)` — 每次讀寫帶 project_id，多專案 job 各自獨立（MAJOR 2 fix）
- `CommandRunner` protocol（dependency-injection seam）：real `SubprocessRunner`（wired but not live-verified），test `FakeRunner`
- Status transitions：PLANNED → QUEUED（enqueue）→ RUNNING（worker loop）→ COMPLETED | FAILED（exit code）
- `cancel_job(project_id, job_id)`：QUEUED job 立即 FAILED；RUNNING job 呼叫 `runner.cancel()` → terminate signal → FAILED on non-zero exit
- `progress` + `progress_label` 欄位從 subprocess stdout 解析（regexp `\d+/\d+`）
- `TrainingJob` 新增欄位：`progress`, `progress_label`, `exit_code`, `stderr_tail`, `resume_checkpoint_path`

### 10.2 VRAM exclusive lock — 硬鎖（BLOCKER fix 2026-06-13）

**原始實作缺陷**：sentinel ManagedModel 方式是 eviction-based，不具備真正的互斥性（sentinel 可被其他 model 的 `acquire()` evict）。

**修正後 API（均來自 `core/scheduler/vram.py`）**：
- `ModelScheduler.begin_training(holder)` — 設定不可驅逐的硬鎖（`_training_lock_holder` flag）
- `ModelScheduler.end_training()` — 清除鎖
- `ModelScheduler.is_training_locked()` — 查詢鎖狀態；generation path 以此 gate dispatch
- `ModelScheduler.acquire(name)` — 在 `is_training_locked()` 為 True 時立即 raise `SchedulerError`（non-evictable hard refusal）

**Executor 行為**：
- Direction (a)：`_run_job` 開始前先檢查有無受管模型為 ACTIVE；有則 job 立即 FAIL（誠實報告，不聲稱未實作的雙向互斥）
- `begin_training(job_id)` 在 job 開始時呼叫，`end_training()` 在 `finally` 中呼叫（確保一定釋放）

**Generation service 串接**：
- `core/generation/service.py` 接收注入的 `ModelScheduler`
- `execute_job()` / `execute_ready_jobs()` 呼叫 `_training_lock_blocking_reason()` → 若鎖定則拒絕執行，以 `blocking_reason="training in progress — generation queued"` 回報（復用既有 blocking-reason 機制）

### 10.3 Command construction

**`core/training/lora.py` — `build_lora_command()`**：
- 輸入：`CharacterSheet` + `DatasetPack` + `TrainingRecipe` + `project_models_dir` + `kohya_ss_dir`
- 輸出：`LoraCommandSpec`（`args`, `cwd`, `output_path`）
- CLI 形式：`python -m accelerate.commands.launch train_network.py --pretrained_model_name_or_path=... --train_data_dir=... --network_module=networks.lora --network_dim=<rank> --max_train_epochs=<epochs> --optimizer_type=<optimizer> ...`
- **Pure function（無 I/O，無 subprocess）**：`output_dir.mkdir()` 移除，改由 executor 在 run time 建立（MINOR fix）
- 重複 `--output_name` 已移除（MINOR fix）

**`core/training/voice_clone.py` — `build_voice_clone_command()`**：
- Zero-shot mode：回傳 `VoiceCloneCommandSpec(s1_args=None, s2_args=None)`（無 subprocess）
- Fine-tune mode：回傳 S1（`s1_train.py`）+ S2（`s2_train.py`）argv lists
- **Pure function（無 I/O，無 subprocess）**：`voices_dir.mkdir()` 移除，改由 executor 在 run time 建立（MINOR fix）
- **DEFERRED**：GPT-SoVITS s1_train.py / s2_train.py 實際 CLI 使用 `--config YAML`，目前使用 `--train_files` 等 placeholder flags，尚未對真實安裝驗證。

### 10.4 Live command path（MAJOR 3 fix）

**原始缺陷**：`submit_job` 呼叫 `executor.enqueue(job_id)`，`_resolve_command()` fallback 到 `["echo", job_id]`。

**修正**：
- `executor.enqueue(project_id, job_id)` — 帶 project_id
- `_resolve_command(project_id, job_id)` — 解析順序：(1) `_pending_commands` dict、(2) `asset_store_resolver` 載入實體 → `build_lora_command()` / `build_voice_clone_command()`、(3) 兩者皆無 → raise `SchedulerError`（不 fallback 到 echo）
- `enqueue_with_command()` 使用 `_pending_commands` dict（在 `__init__` 初始化，非 lazy 建立，無 monkey-patch）

### 10.5 Worker manifest — kohya_ss

`workers/manifest.json` 新增 `"kohya-ss"` 條目（同原始 M4.d）。

### 10.6 API surface 新增

- `GET /api/v1/projects/{project_id}/training/{job_id}` — poll single job（`TrainingJobPollData`）
- `POST /api/v1/projects/{project_id}/training/{job_id}/cancel` — cancel queued or running job

### 10.7 REAL-RUN DEFERRED（wired-but-not-live-verified）

以下已完成契約設計並通過 FakeRunner 測試，**尚未對真實 GPU 或 worker 安裝執行**：
- kohya_ss CLI 命令向量（build_lora_command）
- GPT-SoVITS S1/S2 命令向量（build_voice_clone_command）；S1/S2 CLI arg shape 未對真實安裝驗證（DEFERRED: `--config YAML` vs `--train_files` flags）
- SubprocessRunner（已實作，未跑過真實 subprocess）

**待使用者操作**：(1) 安裝 kohya_ss + GPT-SoVITS clone，(2) 確認路徑設定，(3) 執行真實 training job。

### 10.8 TODO（resume / 中間 checkpoint 試聽，spec §7.3）

Resume from checkpoint **尚未實作**。`TrainingJob.resume_checkpoint_path` 欄位已預留（schema 層面完成）；實際邏輯（解析 checkpoint dir → `--resume_from_checkpoint` CLI flag）留待後續 phase。Spec 參照：§7.3「可中斷、可續訓、可試聽中間 checkpoint」。

### 10.9 驗證

`uv run --extra dev pytest tests/ -q` → **257 passed, 3 warnings**（含 43 個新測試在 `tests/test_executor.py`）。

涵蓋：
- (a) kohya_ss 命令建構 12 cases（含 --output_name 不重複 / build 不建立 dir）
- (a) GPT-SoVITS 命令建構 8 cases（含 build 不建立 dir）
- (b) FIFO 單並發 2 cases
- (c) 硬鎖 VRAM 10 cases（is_training_locked false/true/after-end / acquire raises while locked / acquire succeeds after end / locked during run / not locked after complete/fail / training refused when model ACTIVE / generation service blocks when locked / execute_ready_jobs skips when locked）
- (d) Status transitions 7 cases
- (e) Per-project isolation 1 case（兩 project 各自 jobs.json 獨立）
- (f) Live command path 1 case（asset_store_resolver → real kohya_ss argv，非 echo）

**狀態：已完成（real-run deferred，GPT-SoVITS CLI args DEFERRED，resume TODO）**

---

## 11. 2026-06-13 — M4.e Portable-Release uv-bootstrap + setup 錯誤 AI 解釋

### 11.1 範圍決策（PM 決策）

**實作**：uv-bootstrap setup flow、`setup.log` 基礎設施、已知錯誤白名單、setup 錯誤 AI 解釋。  
**延後（DEFERRED）**：Tauri 跨平台封裝（`.msi` / `.dmg` / `.AppImage` 打包設定）——留待後續里程碑。

### 11.2 uv-bootstrap 流程設計

`scripts/setup.ps1`（Windows）與 `scripts/setup.sh`（macOS / Linux）均實作 7 階段 bootstrap：

| 階段 | 動作 | Dev-Mode 跳過條件 |
|---|---|---|
| [1/7] | 偵測 OS / 硬體（GPU best-effort） | — |
| [2/7] | 下載 uv binary（pinned v0.5.31） | uv 已在 PATH 或 `tools/bin/` |
| [3/7] | `uv python install 3.11` | `.venv` 已存在 |
| [4/7] | `uv venv .venv` | `.venv` 已存在 |
| [5/7] | `uv pip install -e .`（核心依賴） | — |
| [6/7] | 確保 ffmpeg（非致命） | ffmpeg 已在 PATH 或 `tools/bin/` |
| [7/7] | 初始化 `projects/ logs/ tmp/` | — |

uv 下載 URL 格式：`https://github.com/astral-sh/uv/releases/download/{version}/{asset}`。  
資產命名遵循 uv 官方 release asset 規則（x86_64/aarch64 × windows/darwin/linux）。

### 11.3 已知錯誤白名單（`scripts/lib/setup_diagnostics.py`）

`KNOWN_ERRORS` tuple（順序匹配，first-match wins）：

| key | 主要觸發 pattern |
|---|---|
| `network_timeout` | `timed out`, `ConnectTimeout`, `ReadTimeout`, `urlopen error timed out` |
| `network_unreachable` | `getaddrinfo failed`, `ConnectionRefusedError`, `No route to host` |
| `disk_full` | `No space left on device`, `WinError 112`, `OSError: [Errno 28]` |
| `cuda_missing` | `libcuda.so`, `libcuda.so.1`, `NVIDIA-SMI has failed` |
| `powershell_execution_policy` | `running scripts is disabled`, `ExecutionPolicy`, `UnauthorizedAccess` |
| `hash_mismatch` | `sha256`, `checksum`, `digest did not match` |
| `permission_denied` | `PermissionError`, `[Errno 13]`, `WinError 5`, `access is denied` |

### 11.4 setup.log 基礎設施（`scripts/lib/setup_diagnostics.py`）

- `write_to_log(stage_label, error_text, exc, root)` → 附加到 `<root>/setup.log`。
- 所有文字先過 `_redact()` 脫敏（regex：≥20 字元的 alphanumeric+特殊字元 blob，或 `sk-` / `AIza` / `Bearer ` 前綴）→ 替換為 `[REDACTED]`。
- `build_console_summary(stage_index, stage_total, one_line_summary, log_path)` → 產生 §11.3 規格的 console 文字（包含 y/n/s 選項）。

### 11.5 AI 解釋設計（`scripts/lib/setup_ai_explain.py`）

1. `explain_setup_error(stage_label, log_path, root, llm_client=None)` — 公共入口。
2. **無 provider 路徑**：`_load_env_keys()` 讀 `.env` → `_has_any_provider()` 返回 False → 直接返回 `NO_KEY_GUIDANCE`（provider 列表 + 官方連結），不發起任何 LLM 請求。
3. **有 provider 路徑**：優先順序 Ollama（local）> Anthropic > OpenAI > Gemini；`_build_default_client()` 根據 env_keys 選擇。Gemini key 透過 `x-goog-api-key` header 傳遞，絕不放入 URL query param。
4. **Prompt 構成**：`build_explain_prompt()` = 最後 50 行 `setup.log`（再次 `_redact`）+ OS / arch / Python 版本資訊 + 階段標籤。
5. **LLM client 可注入**：測試傳入 fake callable；生產用 `_build_default_client`。

### 11.6 安全機制

- API key 僅透過 HTTP header 傳遞（`x-api-key` / `Authorization: Bearer`），絕不出現在 URL query params。
- `write_to_log` 寫入前 → `_redact(error_text)` + `_redact(traceback_text)`。
- `build_console_summary` 的 `one_line_summary` 也過 `_redact`。
- `build_explain_prompt` 中的 log tail 也過 `_redact`（belt-and-suspenders，因 log 寫入時已脫敏）。
- 測試 `test_api_key_not_in_setup_log` 與 `test_api_key_not_in_console_summary` 驗證 key 不洩漏。

### 11.7 [DEFERRED] Tauri 跨平台封裝

`.msi`（Windows）/ `.dmg`（macOS）/ `.AppImage`（Linux）的 `tauri.conf.json` / CI bundle 設定**未在 M4.e 實作**。  
本次交付的 setup script 以 uv + Python venv 為完整可測試核心；Tauri shell 封裝留待後續里程碑。

### 11.8 驗證

`uv run --extra dev pytest tests/ -q` → **296 passed, 3 warnings**（含 39 個新測試在 `tests/test_setup_diagnostics.py`）。

測試涵蓋：
- (a) 白名單 7 個 key 各至少 2 個觸發 pattern，含 case-insensitive 驗證
- (b) 未知錯誤 → `setup.log` 寫入（含 exception traceback）+ console summary 正確格式
- (c) AI 解釋 fake LLM：無 key 路徑不呼叫 client；有 key 路徑呼叫並回傳；prompt 含 last-50 log lines + OS info + stage label
- (d) 安全：fake key 不出現在 setup.log / console summary；redact 覆蓋 Bearer token、traceback 中嵌入的 key

PowerShell parse check：`0 errors`。  
Bash parse check：`bash -n` 通過。

**狀態：已完成（Tauri 封裝 DEFERRED）**

---

## 12. 2026-06-13 — M5.1 Version Tree BACKEND (spec §8.2)

### 12.1 Audit: parent_version_id wiring (spec §5.11)

**問題**：refine→accept 流程是否確實將 `parent_version_id` 寫入產出的 `AssetRecord`？

**結論：已正確實作（pre-existing）**。追蹤路徑：

- `refine_asset()` (`core/generation/service.py` — search `def refine_asset`) → sets `parent_asset_id` on the `GenerationJob`
- On job execution, `_persist_generated_artifact()` (`service.py` — search `def _persist_generated_artifact`) → reads `job.parent_asset_id` → writes into `AssetRecord.parent_version_id` (`job.parent_asset_id if job else None`)
- `AssetRecord` schema (`schemas.py` — search `parent_version_id`) declares `parent_version_id: str | None = None` with refine metadata fields: `refine_strategy`, `mask_asset_id`, `prompt_delta`, `param_delta` — all populated from the job in `_persist_generated_artifact`
- Root versions (non-refine) have `parent_version_id=None`

No fix required.  All §5.11 metadata fields were already wired.

### 12.2 Tree API endpoint

`GET /api/v1/projects/{project_id}/versions/tree` → `VersionTreeData` envelope.

Node shape (`VersionTreeNode`): `id`, `parent_id`, `asset_type`, `modality`, `title`, `status`, `created_at`, `prompt_hash`, `refine_strategy`, `prompt_delta`, `param_delta`, `mask_asset_id`, `backend`, `is_orphaned`.

Robustness:
- **Cycle detection**: ancestor-chain walk with a visited-set; cycle → `cycle_detected=True`, offending `parent_id` broken to `null` on that node.
- **Orphan handling**: `parent_version_id` pointing to missing asset → `is_orphaned=True`; node still returned, no crash.
- **Node cap**: 2000 nodes max (`GenerationService._TREE_NODE_CAP`); `capped=True` + log warning when hit; no silent truncation.

### 12.3 Diff endpoint

`GET /api/v1/projects/{project_id}/versions/diff?from_id=<id>&to_id=<id>` → `VersionDiffData`.

Delta fields: `prompt_delta`, `param_delta`, `mask_diff`, `recipe_diff`, `strategy_diff`, `backend_diff`. Fields are `null`/`{}` when both versions share the same value. Pure/deterministic (no side effects). Both ids must exist or 404 is returned.

### 12.4 Schema additions

`core/models/schemas.py`: `VersionTreeNode`, `VersionTreeData`, `VersionDiffRequest`, `VersionDiffData`.

### 12.5 Tests

`tests/test_version_tree.py` — 20 tests:
- parent_id written on refine-accept (3 tests: parent_version_id set, strategy+prompt_delta recorded, root=None)
- tree DAG correctness (4 tests: single root, linear chain, multi-child branch, sorted order)
- diff delta shape (5 tests: identical assets → empty, root→refined captures prompt+strategy, param_delta, missing from/to → 404)
- cycle detection (2 tests: cycle detected without hang, clean DAG → no flag)
- orphan handling (2 tests: orphaned node flagged, normal node unflagged)
- API smoke (4 tests: tree 200, tree 404, diff 200 same asset, diff 404 missing version)

### 12.6 驗證

`uv run --extra dev pytest tests/ -q` → **323 passed, 3 warnings**.

**狀態：已完成**

---

## 13. 2026-06-13 — M5.2 License Report 完整版（commercial bool + attribution + NSFW + export summary）

### 13.1 Schema 變更（LicenseReportEntry / ProjectLicenseReport）

**問題**：`LicenseReportEntry.commercial` 是 `str | None`；spec §2 要求真正的 boolean；attribution + NSFW + summary 欄位均缺席。

**結論（已實作）**：

| 欄位 | 型別 | 來源 |
|---|---|---|
| `commercial` | `bool \| None` | worker definition `commercial` key；None = 未指定（不猜測）|
| `attribution` | `bool \| None` | SPDX id 查表（25+ 授權）；unknown license → None |
| `attribution_note` | `str \| None` | 對應 SPDX 授權的說明文字 |
| `nsfw` | `bool \| None` | worker definition 優先，次查 registry by display_name；None = 未指定 |

新增 `LicenseReportSummary` model：`total_workers`, `commercial_ok/no/unknown`, `attribution_required/not_required/unknown`, `nsfw_present/absent/unknown`, `has_nsfw`。

`ProjectLicenseReport` 新增 `summary: LicenseReportSummary` 欄位。

### 13.2 Attribution lookup table

25+ SPDX id 對照表於 `core/reporting/license.py`。涵蓋 Apache-2.0、MIT、BSD-2/3-Clause、GPL-2/3、LGPL-2.1/3、AGPL-3.0、CC-BY-\*、CC0-1.0、Unlicense、0BSD 等。Custom / Flux 等非 SPDX 自定授權回傳 `(None, None)` — truthful-delivery，不猜測。

### 13.3 NSFW 欄位加入 registry.json

`core/models/registry.json` 升版至 `schema_version: 2`。所有 5 個現有 entries 加入 `nsfw` 欄位：
- `Qwen3.6-14B-Instruct`: `false`（text-only LLM）
- `Flux.1-dev`: `null`（custom license，NSFW 能力未明確記錄）
- `MusicGen`: `false`
- `GPT-SoVITS`: `false`
- `Wan 2.1`: `null`（custom license，NSFW 能力未明確記錄）

**原則**：只對文件明確記載的 model 設 `true`；無法確定者設 `null` — 不虛構 NSFW 判定（法律風險）。

### 13.4 commercial 舊字串相容性保護

若 workers/manifest.json 有 `"commercial": "true"` 等字串值（legacy），系統會：
- `"true"` → `True`
- `"false"` → `False`
- 其他字串 → `None` + warning 加入 report.warnings

### 13.5 Export 整合

`core/project/export.py` 無需修改；`license_report` 為傳入的 dict，已自動攜帶 enriched 欄位。

### 13.6 驗證（M5.2 review fix — 2026-06-13）

**Review-fix (M5.2 review):** `generate_report()` 的 `registry_path` 參數在兩個 live call site 均未傳入
（`core/main.py` GET `/license-report` endpoint 與 export-download endpoint），導致 production 環境
`nsfw`/`commercial` 永遠為 `None`（registry lookup 被跳過）。Fix: 兩處 call site 均補上
`registry_path=REPO_ROOT / "core" / "models" / "registry.json"`。

追加 production-path test `test_license_report_endpoint_delivers_registry_nsfw`（`tests/test_license_report.py` §7）：
透過 TestClient 呼叫真實 `/api/v1/projects/{id}/license-report` route，注入使用 `gpt-sovits` worker 的 job
（manifest 無 `nsfw` 欄位），斷言回傳的 `nsfw` 為 `False`（來自 registry entry `"GPT-SoVITS"`）。
此測試在 registry_path 未傳入時必然失敗，確保 live wiring 被守護。

`uv run --extra dev pytest tests/ -q` → **357 passed, 3 warnings**（含 34 個測試在 `tests/test_license_report.py`）。

測試涵蓋：
- commercial bool 讀取 7 cases（True / False / absent / null / 字串 true/false / 非法字串 + warning）
- attribution 偵測 12 cases（Apache / MIT / CC-BY-NC / CC0 / Unlicense / custom / Flux / None / GPL3 / BSD3 + entry 層級驗證）
- NSFW rollup 9 cases（none/some、worker definition / registry fallback / absent=unknown）
- summary counts 3 cases（多 worker totals / summary 型別 / empty）
- export 整合 1 case（zip 內含 enriched license-report.json with summary）
- registry 結構驗證 2 cases（所有 entry 有 nsfw 欄位、commercial 為 bool 或 null）
- **production-path wiring 1 case**（live endpoint 透過 TestClient 驗證 registry_path 實際傳入）

**狀態：已完成（含 review fix）**

---

## 14. 2026-06-13 — M5.3 Cross-project references: resolver wiring + export re-resolution + materialization tool (BACKEND)

### 14.1 Audit findings

**Resolver call-site (§5.6.2):**
- `core/project/cross_project.py` had `parse_reference()`, `copy_external_asset()`, `update_origins_json()`, and the RW lock — but NO `resolve_reference()` function.
- The `resolve_refs` flag in `export.py` was stored in the manifest but never acted on — the export simply packed all files as-is with a static warning string.
- No existing call site resolved a `@ref#v` at read-time. The resolver was **NOT wired** into any read path.

**Export re-resolution (§5.6.4):**
- `ProjectExportService.export_project()` had `resolve_refs` parameter but **did NOT re-parse refs or refresh `_external/`**. The parameter was a manifest-only flag with no functional effect.

### 14.2 Implementation: resolver read-side (§5.6.2)

`resolve_reference(ref, current_project_dir, projects_root, *, source_project_dir=None) → dict`

Four-status model:
- **LIVE**: source project accessible, file found, sha256 matches origins.json record.
- **OUTDATED**: source file found in live project, but sha256 differs from origins.json entry (live version has changed).
- **EXTERNAL**: source project unavailable or file not found; served from `_external/` fallback with matching hash.
- **BROKEN**: neither live file nor valid `_external/` copy (also: `_external/` copy hash doesn't match origins.json = corrupted copy).

Never raises on broken — returns `{"status": RefStatus.BROKEN, "path": None, ...}`.

Supporting functions added:
- `_find_asset_file(source_project_dir, asset_path, version)` — locates versioned files under `assets/<asset_path>/<version>.*` with fallback strategies.
- `_find_external_copy(consumer_project_dir, source_project_id, asset_path, version)` — locates `_external/<source>/<path>` copies.
- `_sha256_file(path)` — efficient streaming sha256.
- `collect_project_refs(project_dir)` — extracts all unique @ref strings from style_guide.md and assets/index.json dependencies fields.
- `RefStatus` enum (live / outdated / external / broken).

### 14.3 Export re-resolution (§5.6.4)

`ProjectExportService._refresh_external_copies(project_dir, project_summary)` is now called when `resolve_refs=True`:
- For **LIVE / OUTDATED** refs: copies the current live file into `_external/`, updates origins.json.
- For **EXTERNAL** refs: keeps the existing copy (pinned versions preserved per spec).
- For **BROKEN** refs: removes stale `_external/` entry via `_remove_stale_external()` so the exported zip doesn't pack outdated data.
- Results recorded in export manifest as `ref_resolution` list.

When `resolve_refs=False`: `ref_resolution` is an empty list; no refresh is performed.

### 14.4 Circular dependency detection (§5.6.5)

`detect_cycles(project_id, projects_root, *, _visited, _path) → list[list[str]]`

- DFS with visited-set to prevent infinite loops.
- Cycles are ALLOWED (not an error) — returned as lists of project IDs that form the loop.
- Example: A→B→A returns `[["A", "B", "A"]]`.
- Missing projects silently return `[]` (no crash).

### 14.5 Materialization tool (§16 Q4)

`materialize_reference(ref, current_project_dir, projects_root, *, new_asset_id, source_project_dir, lock_timeout) → dict`

- Explicit / opt-in only; never called automatically.
- Resolves the ref, copies the file into `_external/` under RW lock, records provenance in origins.json.
- Provenance fields in origins.json `origin` sub-dict: `original_ref` (the @ref string), `materialized_at`, `sha256`.
- `update_origins_json` extended to pass through arbitrary extra origin fields (beyond the §5.6.1 schema keys) — backwards-compatible.
- Broken refs return `{"status": "broken", "local_path": None, "provenance": None, ...}` — never raises.
- `materialize_project_refs(project_dir, projects_root, *, refs, lock_timeout) → dict` — bulk wrapper; separates `materialized` from `broken`.

**Security**: `materialize_reference` verifies the resolved source file is inside the source project directory before copying. Paths outside the project boundary return `status="broken"`.

### 14.6 API routes (M5.3)

- `GET /api/v1/projects/{project_id}/refs` — list all @refs with resolved statuses + cycle warning.
- `GET /api/v1/projects/{project_id}/refs/{asset_id}` — list @refs for a specific asset's dependencies.
- `POST /api/v1/projects/{project_id}/refs/materialize` — trigger materialization (optional `refs` list; if omitted, all project refs are processed). Broken refs reported in response, not 4xx.

New schemas in `core/models/schemas.py`: `CrossRefStatus`, `CrossRefEntry`, `CrossRefListData`, `MaterializeRequest`, `MaterializeResultEntry`, `MaterializeData`.

### 14.7 Spec writeback note

§5.6.2 resolver status model (live/outdated/external/broken) is fully implemented as described.
§5.6.4 export re-resolution now functional (was previously a no-op flag).
§5.6.5 cycle detection returns warnings, not errors (cycles are allowed per spec).
§16 Q4 materialization tool is explicit/opt-in, handles broken refs gracefully, preserves provenance.

### 14.8 Verification

`uv run --extra dev pytest tests/ -q` → **384 passed, 3 warnings** (+27 new tests in `tests/test_cross_project_resolver.py`).

Tests cover:
- resolve_reference: all 4 statuses (live / outdated / external / broken) — 6 tests
- collect_project_refs: style_guide + assets/index.json — 2 tests
- Export re-resolution: _refresh_external_copies / resolve_refs=False — 2 tests
- detect_cycles: no cycle / simple cycle / 3-hop cycle without infinite loop — 3 tests
- materialize_reference: live ref / provenance in origins.json / broken returns status / no raise — 4 tests
- materialize_project_refs: bulk mixed / all broken — 2 tests
- Security: materialization stays within project root — 1 test
- API GET refs / POST materialize — 7 tests

**狀態：已完成**

---

## 15. 2026-06-14 — M5.4 App-wide log desensitization (BACKEND)

### 15.1 Scope decision (USER DECISION)

Central filter applied app-wide (all loggers in the process), not just setup.log.
RESEARCH_LOG item 5 promoted from "setup.log only" to "complete implementation".

### 15.2 Shared pattern module: `core/logging_redaction.py`

Single source of truth for all redaction patterns in the codebase.

**`REDACT_RE`** — one precompiled regex with named-prefix alternation + generic backstop:
- `sk-ant-api03-*` / `sk-proj-*` / `sk-*` — Anthropic / OpenAI key prefixes
- `AIza*` / `AIZA*` — Google API key prefix
- `Bearer <token>` — HTTP Authorization header value (token body: base64url chars including `.`)
- Generic backstop: `[A-Za-z0-9][A-Za-z0-9+_=\-]{19,}` — ≥20 chars, NO `.` or `/`
  - Intentionally excludes `.` and `/` to prevent over-redaction of URLs and file paths
  - Named prefixes above cover the major cases where `/` appears in key body (base64)

**`_PATH_RE`** — user-path pattern applied as second step in `redact()`:
- Windows: `[A-Z]:\Users\<name>` → `[A-Z]:\Users\[USER]`
- macOS: `/Users/<name>` → `/Users/[USER]`
- Linux: `/home/<name>` → `/home/[USER]`
- Replaces only the user name segment; retains path tail for debugging

**`RedactionFilter`** — `logging.Filter` subclass:
- `filter(record)` bakes `getMessage()` result into `record.msg` + clears args, then calls `redact()`
- Forces `exc_text` formatting (if `exc_info` is set) before the handler emits, redacts it, clears `exc_info`
- Redacts `record.stack_info` if present
- Always returns `True` (never suppresses records)

**`install_redaction_filter()`** — idempotent installation on root logger:
- Controlled by `MISAKA_LOG_REDACT` env var (default `"1"` = ON)
- Values `"0"` / `"false"` / `"no"` / `"off"` disable the filter
- Guards global `_FILTER_INSTALLED` flag — second call is a no-op

### 15.3 Integration points

**`core/main.py`**: `install_redaction_filter()` called BEFORE `logging.basicConfig()` so the filter is present before any handler is attached. All `logging.getLogger(...)` children of root inherit it automatically — covers HTTP logs, subprocess logs, unhandled exception tracebacks via `logger.exception(...)`.

**`scripts/lib/setup_diagnostics.py`**: Refactored to `from core.logging_redaction import redact as _redact`. No duplicate regex. Fallback local implementation retained for pre-venv bootstrap context (when `core` package is not on sys.path).

### 15.4 Direct log-leak audit

Surveyed all `logger.*` call sites in `core/`. Findings:
- No call site directly logs an API key value (provider keys are only used in HTTP headers, never in log arguments).
- `core/main.py` startup logs `REPO_ROOT` and `PROJECTS_ROOT` (absolute paths). These are the repo root and projects folder — not the user's home directory — so they do NOT leak the OS account name. The path-redaction filter (`_PATH_RE`) would additionally suppress any `C:\Users\<name>` segment if it appeared in those paths.
- No egregious direct-leak call site found requiring a fix beyond the central filter.

### 15.5 Over-redaction risk and guard

**Risk**: the generic ≥20-char backstop can match long alphanumeric tokens in normal log text (e.g. UUIDs, long file names). Redacting a UUID is acceptable (it's safe-to-lose in a log context). The exclusion of `.` and `/` from the backstop character class prevents URLs and POSIX paths from being swallowed.

**Guard**: `tests/test_logging_redaction.py` class `TestNoOverRedaction` (6 tests) verifies:
- Short words (`info`, `debug`, `core`) survive
- Normal API base URLs survive (`anthropic.com` in result)
- Module paths (`core/main.py`, `scripts/lib/setup_diagnostics.py`) survive
- Plain INFO-level log lines are unchanged
- Worker names (`comfyui`, `audiocraft`) survive

### 15.6 Verification

`uv run --extra dev pytest tests/ -q` → **419 passed, 3 warnings** (+30 new tests in `tests/test_logging_redaction.py`).

Tests cover:
- API key redaction — each pattern family (sk-ant / sk-proj / sk- / AIza / Bearer / generic) — 7 tests
- User-path redaction (Windows / macOS / Linux / multiple paths / end-of-string) — 6 tests
- RedactionFilter on real logger message path (api key / user path / format args / non-sensitive unchanged / short path survives) — 5 tests
- RedactionFilter on exception path (`exc_text` redacted) — 1 test
- Env toggle (disabled by 0 / false / enabled by default / enabled by 1) — 4 tests
- No over-redaction (short words / URLs / module paths / UUID / plain log / worker names) — 6 tests
- Idempotency (double install, no duplicate filter on root) — 1 test

**狀態：已完成（RESEARCH_LOG item 5 完整實施）**

---

## 16. 2026-06-14 — M5.5 `decompose_prompt` dedup (BACKEND)

### 16.1 Dedup intent determination

**Problem**: `decompose_prompt()` (spec §5.11) splits a long instruction into
`PromptDecompositionStep` objects for four stages (base_composition →
character_detail → prop_accessory → final_polish). Each step's `prompt` is
composed of all segments matching that stage's marker keywords. Because marker
sets overlap (e.g. a segment containing both 服裝 and 帽 matches both
CHARACTER_DETAIL and PROP_ACCESSORY), the same segment text could appear
verbatim in two different steps, causing the execution planner to process
identical sub-instructions in different passes.

**Spec §5.11 intent**: each pass should contain only the sub-instructions
**unique to that stage**. The dedup is within-decomposition, within-run — not
cross-refine reuse. The goal is to prevent the execution planner from running
the same segment twice in different passes.

### 16.2 Equality key and dedup semantics implemented

**Equality key**: `normalised segment text × stage`

**Algorithm (earliest-stage-wins, canonical order preserved)**:
- Split instruction by `；;。\n` into segments.
- Walk stages in canonical order: BASE_COMPOSITION → CHARACTER_DETAIL →
  PROP_ACCESSORY → FINAL_POLISH.
- For each stage, collect segments that match its markers AND have not yet been
  claimed by an earlier stage.
- Claim claimed segments so later stages cannot reclaim them.
- Emit a step only if at least one unclaimed segment matches.

**What collapses**: a segment that matches the markers of two or more stages
appears only in the earliest matching stage. Later stages receive it zero times.

**What does NOT collapse**: segments that are semantically distinct and only
happen to share some tokens with multiple stages are not merged — they are
separate segments (split by delimiters) and each keeps its own stage assignment.
Each stage appears at most once in the output (a structural guarantee of the
single-step-per-stage construction).

**Conservative principle**: correctness over aggressiveness. If a later stage
legitimately needs a distinct segment, that segment is preserved in that stage
as long as it hasn't already been claimed.

### 16.3 Implementation

`core/generation/refine.py`:
- New helper `_dedup_decomposition_steps(segments, stage_markers)` (line ~132):
  maintains a `claimed: set[str]` of segments already assigned; each stage only
  picks from unclaimed segments.
- `decompose_prompt()` delegates to `_dedup_decomposition_steps()` (unchanged
  external signature).

### 16.4 Tests added (`tests/test_refine_strategy.py`)

5 new test functions (M5.5 dedup section):
- `test_dedup_segment_appears_in_only_one_stage` — segment matching both
  CHARACTER_DETAIL and PROP_ACCESSORY markers appears exactly once, in the
  earlier stage.
- `test_dedup_distinct_passes_unchanged` — an instruction with truly distinct
  segments (one per stage) produces all four stages without over-dedup.
- `test_dedup_preserves_canonical_order` — stages in output remain in canonical
  order after dedup.
- `test_dedup_no_stage_emitted_twice` — same stage cannot appear twice regardless
  of how many segments match it.
- `test_dedup_lineage_metadata_preserved` — every step in the decomposition
  carries non-empty `stage` and `prompt` for lineage/audit (spec §5.11).

### 16.5 Spec writeback

Spec §5.11 updated: dedup semantics (equality key + earliest-stage-wins rule +
conservative principle) documented inline under the decomposition bullet.

### 16.6 Verification

`uv run --extra dev pytest tests/ -q` → **428 passed, 1 skipped, 3 warnings**
(baseline was 423 passed, 1 skipped; +5 new dedup tests).

**狀態：已完成**

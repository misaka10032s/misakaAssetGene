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
5.  **日誌脫敏：** 所有的 Error Log 在寫入 `setup.log` 前必須進行脫敏，禁止記錄使用者的本地路徑與 API Key。

---

## 4. 2026-04-23 — Claude 工作流與開源治理補充

- 已建立 `CLAUDE.md` 與 `.claude/` 結構，角色分工對齊 `.plan/DEVELOPMENT_PLAN.md`。
- 已明確化流程：新需求先以 `.claude/context/spec.md` + 架構師角色討論可行性與實作細節，再回寫 `.claude/context/spec.md`。
- 已補充多模態複合素材需求，納入圖像、文字台詞、角色語音、歌曲、影片與靜態動圖的組合交付。
- 已補上 repo 邊界規則：第三方 repo 維持獨立 clone，不納入本專案 git 追蹤。
- 已新增 `.gitignore`、`CONTRIBUTING.md`、`LICENSE`。

**狀態：已完成**

---

## 5. 2026-06-12 — PM 決策記錄

以下為專案負責人 2026-06-12 正式決策事項，已同步更新至 `.claude/context/spec.md` v0.9 與 `.plan/DEVELOPMENT_PLAN.md`。

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

### 9.5 執行步驟（§7.1 sequence）
**問題**：訓練流的 execution_steps 應涵蓋哪些階段？  
**結論**：6 個必選步驟：(a) 確認 CharacterSheet → (b) 確認 DatasetPack → (c) 確認 TrainingRecipe → (d) 確認 LoraPreset → 訓練 LoRA（kohya_ss） → 批次生成（comfyui）。若 prompt 含語音克隆關鍵詞，插入 GPT-SoVITS 步驟；若含影片關鍵詞，附加 i2v 步驟。訓練執行（M4.c）不在此 phase。

### 9.6 驗證
`uv run --extra dev pytest tests/ -q` → **212 passed, 0 failed**（含新增 41 個測試在 `tests/test_training_flow.py`）。涵蓋：訓練意圖偵測 14 cases、REQUIRED_SLOTS 驗證 4 cases、checklist 進展 5 cases、plan 實體 ID 引用 5 cases、建議卡片發射 9 cases、summary/next_step 3 cases、session persistence 1 case。

**狀態：已完成**

---

## 8. 2026-06-13 — M4.a 第五實體 ImageToVideoRecipe 補齊

**問題**：§7.1.1 規格定義五個實體，但 M4.a 初始 commit `b997a3c` 只實作了四個，遺漏了 `image-to-video recipes`。  
**結論**：**已完成**。新增第五實體 `ImageToVideoRecipe`（SQLite 表 `i2v_recipes`）。欄位：`name`, `workflow_kind`, `frames`, `fps`, `motion_strength`, `notes`。來源 accepted image 在套用時傳入，不存在 recipe 內（可重用模板設計）。路由：`/api/v1/projects/{project_id}/i2v-recipes`（5 個 CRUD 端點）。§7.1.1 儲存模型表已更新。測試涵蓋 store 單元 + API 層，全套通過。

**狀態：已完成**

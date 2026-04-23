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
4.  **文件讀寫鎖 (RW Lock)：** 針對「跨專案引用」，在更新 `_external/` 複本前必須檢查原始檔案是否被佔用，避免寫入衝突。
5.  **日誌脫敏：** 所有的 Error Log 在寫入 `setup.log` 前必須進行脫敏，禁止記錄使用者的本地路徑與 API Key。

---

## 4. 2026-04-23 — Claude 工作流與開源治理補充

- 已建立 `CLAUDE.md` 與 `.claude/` 結構，角色分工對齊 `.plan/DEVELOPMENT_PLAN.md`。
- 已明確化流程：新需求先以 `spec.md` + 架構師角色討論可行性與實作細節，再回寫 `spec.md`。
- 已補充多模態複合素材需求，納入圖像、文字台詞、角色語音、歌曲、影片與靜態動圖的組合交付。
- 已補上 repo 邊界規則：第三方 repo 維持獨立 clone，不納入本專案 git 追蹤。
- 已新增 `.gitignore`、`CONTRIBUTING.md`、`LICENSE`。

**狀態：已完成**

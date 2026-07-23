---
id: BP-ADAPTER-1
title: 六大核心多模態生成 Adapter
system: adapter
tags: [adapter, generation, multimodal, comfyui, gpt-sovits, ace-step]
status: 已完成
request_verbatim: "多模態生成｜圖像、BGM、SFX、角色配音、影片（spec.md §2 核心功能總覽 Feature Matrix，P0）"
decided_date: 2026-05-07
exec_links:
  - core/generation/adapters/comfyui.py
  - core/generation/adapters/gpt_sovits.py
  - core/generation/adapters/voxcpm.py
  - core/generation/adapters/ultimate_rvc.py
  - core/generation/adapters/ace_step.py
  - core/generation/adapters/audiocraft.py
  - core/generation/adapters/stable_audio_tools.py
  - core/generation/adapters/video_backend.py
  - core/generation/adapters/common.py
done_date: 2026-05-07
origin: "`core/generation/adapters/` 首次入庫於 commit ca99f10（2026-05-07，『M1~4 XD』，@PM 登記為 M1）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §2 核心功能總覽 / §6 生成後端對應表
---

## 設計說明

`core/generation/adapters/` 底下每個檔案對應 CLAUDE.md「Multimodal by default」原則所要求的一種輸出模態，統一透過共用介面（`common.py`）與後端 CLI/HTTP 溝通：

| 模態 | 後端 | Adapter 檔案 |
|---|---|---|
| 圖像（含 inpaint/img2img） | ComfyUI | `comfyui.py` |
| 角色配音（TTS/Voice Clone） | GPT-SoVITS | `gpt_sovits.py` |
| 角色配音（另一路徑） | VoxCPM | `voxcpm.py` |
| 變聲/RVC | ultimate-rvc | `ultimate_rvc.py` |
| 歌曲/BGM | ACE-Step | `ace_step.py` |
| 音效/BGM（另一路徑） | AudioCraft | `audiocraft.py` |
| 音效/BGM（另一路徑） | stable-audio-tools | `stable_audio_tools.py` |
| 影片 | video backend | `video_backend.py` |

對應 spec §6「生成後端對應表」列出的 M1~M2 必做 repo 清單；每個 adapter 是實際呼叫外部 worker（`workers/<name>/`）的薄封裝層，由 `core/generation/service.py` 統一編排排程與 job 生命週期。

### 現況核對（2026-07-23 盤點）

9 個 adapter 檔案皆存在且非空樁（`comfyui.py` 支援 txt2img/img2img/inpaint，見 `BP-COMFY-1`）。M2（928dd77）追加 inpaint/img2img e2e 與多階段精修；M3（7af6066/2e1aa19）補齊 offline gating（見 `BP-OFFLINE-1`）與 VRAM 熱切換（見 `BP-VRAM-1`）。@PM 登記 M2 有 70 tests green + Chrome UI e2e 驗證（真實 ComfyUI 生成 + img2img refine lineage）。「影片」模態（`video_backend.py`）與「角色台詞文字」不經 adapter（走 LLM router 直接生文字），故 spec §2 表格中的「圖文、台詞、角色語音、歌曲、影片」在此以「6 大 adapter 覆蓋圖像/語音/歌曲/影片四種二進位輸出」理解；台詞文字由顧問引擎（`BP-CONSULT-1`）直接以 LLM 產生。

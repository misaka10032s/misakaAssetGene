---
id: BP-TRAIN-2
title: 多角色 LoRA 資料集工作流（§7.1.1 五實體 CRUD）
system: train
tags: [training, lora, dataset, crud, sqlite]
status: 已完成
request_verbatim: "§7.1.1 多角色 LoRA 與資料集工作流｜CharacterSheet/DatasetPack/TrainingRecipe/LoraPreset/ImageToVideoRecipe 五實體（spec.md §7.1.1，M4.a 決策 2026-06-13，第五實體補齊 2026-06-13）"
decided_date: 2026-06-13
exec_links:
  - core/training/asset_store.py
  - frontend/src/pages/../components/TrainingEntities.vue
done_date: 2026-06-13
origin: "M4.a commit b997a3c（2026-06-13）『feat(assets): SQLite CRUD for §7.1.1 character sheets / dataset packs / recipes / lora presets (M4.a)』；第五實體（ImageToVideoRecipe）由 reviewer 在 spec §7.1.1 第 963 行抓到補齊；171 tests passed，2 review chains PASS。前端 CRUD UI 由 M4.c（6cc7711，2026-06-13）補上，reviewer 抓到 BLOCKER（API 回應 key 漂移 .items/.item vs 實體 key）已修"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §7.1.1 多角色 LoRA 與資料集工作流（儲存模型）
---

## 設計說明

spec §7.1.1 把「訓練一個角色 LoRA」拆成五個可獨立管理、互相引用的實體：CharacterSheet（角色卡）→ DatasetPack（資料集）→ TrainingRecipe（訓練配方）→ LoraPreset（訓練完的 LoRA 預設）→ ImageToVideoRecipe（選配，圖轉影片配方）。全部存 SQLite（`memory.sqlite`），由 `core/training/asset_store.py` 提供 CRUD，前端 `TrainingEntities.vue` 提供管理介面，並與訓練流顧問（`BP-CONSULT-4`）的 entity-referencing plan 串接。

### 現況核對（2026-07-23 盤點）

`asset_store.py` 內 `update_character_sheet`/`update_dataset_pack`/`update_training_recipe`/`update_lora_preset`/`update_i2v_recipe` 五個更新方法皆存在（`core/main.py` 對應五組路由 `update_character_sheet`/`update_dataset_pack`/`update_training_recipe`/`update_lora_preset`/`update_i2v_recipe`），確認五實體 CRUD 到位。@PM 登記第五實體（ImageToVideoRecipe）是 reviewer 主動核對 spec 行號才補齊，前端在 M4.c 亦經 reviewer 抓到 API 回應格式漂移的 BLOCKER 並修復。

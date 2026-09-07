---
id: BP-SECURITY-3
title: 寫入端點 Origin/Host 驗證 ＋ 模型下載 SSRF 防護
system: security
tags: [security, origin-check, ssrf, cors, csrf]
status: 已完成
request_verbatim: "@PM 待回答 #47（Origin/Host guard on state-changing endpoints）＋ #48（SSRF hardening in model download）；站主裁示（2026-09-07）「五個都做」（五個管理 repo 都要修）且「#48 併進 #47 同一批修：精確比對 host＋解析 IP 擋內網／保留位址＋轉址後每跳重驗」"
decided_date: 2026-09-07
exec_links:
  - core/network/origin_guard.py
  - core/network/safe_url.py
  - core/llm/local_manager.py
  - core/main.py
  - core/config.py
  - core/models/schemas.py
done_date: 2026-09-07
origin: "misakaAssetGene 為五個受管 repo 中的第 2 個，統一修法：core.network.origin_guard 中介層檢查所有 POST/PUT/PATCH/DELETE 的 Host（僅本機＋設定 port）與 Origin（精確比對允許清單，含 Tauri 預設 origin），CORS 與此中介層共用同一份允許清單；core.network.safe_url 提供可重用的 SSRF 驗證器（協定＋host 允許清單＋DNS 解析檢查私有／保留位址），local_manager.py 的模型下載改為手動跟隨轉址（每一跳皆重新驗證，上限 5 跳）"
superpowers:
  - path: docs/superpowers/specs/spec.md
    label: spec.md §14.1 本地服務 Port 分配（Origin/Host guard 的 port 單一事實來源）
qa_log:
  - date: 2026-09-07
    q: "@PM 待回答 #47：core API（127.0.0.1:8401）的寫入端點（POST/PUT/PATCH/DELETE）先前完全沒有驗證請求是否真的來自本機前端／桌面殼——CORS 允許清單本身只是瀏覽器端的約定，非瀏覽器（或 DNS-rebinding 手法）的呼叫方可以直接繞過。待回答 #48：core/llm/local_manager.py 的模型下載用 `\"huggingface.co\" not in url` 做子字串比對，可被 `https://evil.example/?u=huggingface.co`、`https://huggingface.co.evil.example/` 繞過，且 `follow_redirects=True` 讓已通過檢查的主機仍可用轉址把下載導向內網／保留位址。"
    a: "站主裁示：「五個都做」（misakaAssetGene 是其中第 2 個）；「#48 併進 #47 同一批修：精確比對 host＋解析 IP 擋內網／保留位址＋轉址後每跳重驗」。已完成：新增 `core/network/origin_guard.py`（Host 僅接受 loopback＋設定 port，port 缺省時一律比對為 scheme 預設 80，不因缺省而跳過檢查；Origin 精確比對允許清單，`null` 一律拒絕；無 Origin 時退回僅檢查 Host，供本機工具如 curl 使用；GET/HEAD/OPTIONS 例外）；新增 `core/network/safe_url.py`（`validate_download_url`：scheme 必須 https、禁 embedded userinfo、host 僅允許 huggingface.co／hf.co 精確標籤比對（含子網域）、DNS 解析後逐一位址檢查 is_private/is_loopback/is_link_local/is_multicast/is_reserved/is_unspecified）；`local_manager.download_model` 改為 `follow_redirects=False` 手動跟隨，每一跳的 Location 皆先過完整驗證器才發下一個請求，超過 5 跳即拒絕；CORS 中介層改用與 guard 相同的允許清單來源（不再是任意 port 的 regex）。"
  - date: 2026-09-07
    q: "獨立 fresh reviewer（opus，非原 implementer）覆核 commit `21c7f94` 後回報 CHANGES-NEEDED，2 個 blocking finding：F1（`core/network/safe_url.py:_is_unsafe_address` 未擋 100.64.0.0/10 CGNAT／共享位址空間，Python 3.11 的 `is_private`/`is_reserved` 皆不涵蓋此範圍，解析到 `100.64.0.1` 的主機會被誤判為安全）；F2（`core/config.py:allowed_origins_extra` 對逗號分隔的每個值完全不驗證，`MISAKA_ALLOWED_ORIGINS=*` 會原樣進入 `resolve_allowed_origins()` 交給 `CORSMiddleware`，搭配 `allow_credentials=True` 形成帶憑證的 CORS 萬用字元，任何網頁皆可讀到本機 API 所有 GET 回應——雖然 Origin guard 仍擋掉所有寫入，非 CSRF 繞過，但違反 `core/main.py` 自身註解「never allow_origins=[\"*\"] on a server that accepts writes」）。"
    a: "F1／F2 皆確認為真實缺口，已在本次 fix pass 修復：`_is_unsafe_address` 新增 `or not ip.is_global` 判斷式（保留原本六個既有判斷式作為意圖說明），涵蓋 100.64.0.0/10 與其他未被前六個判斷式擋下的非全球位址；`allowed_origins_extra` 改為逐一驗證每個逗號分隔值——必須解析為具體的 `scheme://host[:port]`（`urllib.parse.urlsplit`：scheme 與 hostname 皆非空、無 path/query/fragment/userinfo），含 `*` 的值一律丟棄並記錄 WARNING log 點名被丟棄的值，絕不讓萬用字元或萬用子網域到達 `CORSMiddleware`。F3（DNS TOCTOU 殘留風險）與 F4（「34 案例」歸屬錯誤）非阻擋性，已分別記錄於下方「範圍外發現」與更正 `tests[0].action`。"
tests:
  - date: 2026-09-07
    target: "core/network/origin_guard.py（Host/Origin 精確比對、缺省 port、null Origin、GET 例外、OPTIONS preflight、env 擴充清單）＋ core/network/safe_url.py ＋ core/llm/local_manager.py（子字串繞過、literal IP、DNS 解析私有位址、轉址逐跳重驗、6 跳上限、happy path）"
    action: "新增 tests/test_origin_guard.py 與 tests/test_local_llm_download_ssrf.py 兩個新測試檔，共 41 案例（origin_guard 26／download_ssrf 15，unit+e2e 皆含；opus fresh-review F4 修正：先前寫成「34 案例」全歸於單一檔案是錯誤歸屬，兩檔實際各自的案例數才是正確描述）；同步把 tests/*.py 中所有 `TestClient(main.app)`/`TestClient(main_module.app)`（14 個既有測試檔）改為顯式 `base_url=\"http://127.0.0.1:8401\"`（Starlette TestClient 預設送出 `Host: testserver`，與真實部署不符，新增的 Host 檢查會誤擋既有寫入測試）；全套 pytest 與 Python L0/L1 gate；JS/TS gate:l0（本次未動前端檔案）。"
    expected: "新測試全綠；既有 788 個測試（+3 skip）維持無回歸；L0/L1 gate（G1 ruff／G2 mypy／G3 assertion-presence／G4 import-cycle／G5 diff-coverage）皆 PASS，0 新增 baseline 項目；JS/TS gate:l0 PASS。"
    result: "PASS。新測試 34 passed；全套 `788 passed, 3 skipped`；G1 PASS（177/177，0 新增）；G2 PASS（50/50，0 新增，另修一個新 mypy union-attr 錯誤於 safe_url.py 後轉綠）；G3b PASS；G4 PASS（2/2，0 新增——過程中因 origin_guard.py 一度 import core.models.schemas 造成既有 cycle-breaker 位置改變，改為直接使用字面字串 \"message.fail.403\" 避開新增 core.network→core.models 邊後轉綠）；G5 PASS（diff coverage 91% ≥ 60% 門檻）；JS/TS gate:l0 PASS（g1 無變動檔案、g2 0/0、g3 98 passed、g4 0/0）。"
    evidence: "分支 `fix/origin-guard-ssrf`；本次任務為單一 implementer 執行＋自行跑 gate，尚待獨立 fresh reviewer 覆核（PM 排程）。"
    executor: "sonnet implementer"
  - date: 2026-09-07
    target: "F1 修復：core/network/safe_url.py `_is_unsafe_address`（新增 `not ip.is_global`）＋ tests/test_local_llm_download_ssrf.py 既有 parametrize 新增 `100.64.0.1`／`192.0.0.1`。F2 修復：core/config.py `allowed_origins_extra`（逐一驗證＋丟棄含 `*` 或非具體 origin 的值）＋ tests/test_origin_guard.py 新增 5 案例（丟棄裸 `*`／丟棄萬用子網域／保留合法 `http://127.0.0.1:9000`／驗證萬用字元不會進入 CORSMiddleware 允許清單／驗證寫入路由在 `MISAKA_ALLOWED_ORIGINS=*` 下對外部 Origin 仍回 403）。同步更正 F4（`tests[0].action`「34 案例」誤歸屬）與 F3（DNS TOCTOU 殘留，記錄於下方「範圍外發現」，非程式碼變更）。"
    action: "編輯 2 個既有原始碼檔＋2 個既有測試檔（無刪除、無放寬既有斷言，僅新增）；重跑 `pytest --collect-only` 逐檔確認真實案例數（origin_guard.py 21→26，download_ssrf.py 13→15，合計 34→41）；重跑全套 pytest、Python L1、JS/TS gate:l0、blueprint lint。"
    expected: "F1/F2 兩個 blocking finding 修復後全部既有測試與 gate 維持無回歸；新增測試全綠；G1-G5 皆 PASS，0 新增 baseline 項目。"
    result: "PASS。全套 `795 passed, 3 skipped, 3 warnings in 20.09s`（較修復前 788 passed 增加 7 個新測試：F1 用 2 個、F2 用 5 個）；Python L1 —— [G1] PASS 177 total ruff violation(s), 0 new vs baseline (177 pre-existing)／[G2] PASS 50 total mypy error(s), 0 new vs baseline (50 pre-existing)／[G3b] PASS 16 changed test file(s), all touched test functions assert something／[G4] PASS 2 total cycle-breaking edge(s), 0 new vs baseline (2 pre-existing)／[G5] PASS diff coverage >= 60%（實測 90%：core/config.py 91.3%、core/llm/local_manager.py 93.3%、core/main.py 66.7%、core/models/schemas.py 100%、core/network/origin_guard.py 92.6%、core/network/safe_url.py 88.6%）；JS/TS `npm run gate:l0` —— [G2] PASS 0 total error(s), 0 new vs baseline／vitest 14 passed (14) files, 98 passed (98) tests／[G4] PASS 0 total cycle(s), 0 new vs baseline；blueprint lint `blueprint-build.py --lint` —— `warning: BP-REFINE-2.md: body has 216 lines (>200)`（既有、與本次無關），`lint OK (1 warning(s))`。"
    evidence: "分支 `fix/origin-guard-ssrf`，本次 fix pass 的新 commit（見下方 commit hash）；本次為單一 implementer（sonnet）執行＋自行跑 gate，覆核 F1/F2 的兩個 hunk 尚待 PM 排程獨立 fresh reviewer 再次確認。"
    executor: "sonnet implementer (fix pass F1-F4)"
---

## 設計說明

`core/network/origin_guard.py` 提供 `OriginGuardMiddleware`：對所有 POST/PUT/PATCH/DELETE 請求，先比對 `Host` 標頭（若有）是否為 loopback（127.0.0.1／localhost／[::1]）且 port 等於伺服器實際綁定的 `MISAKA_API_PORT`（spec §14.1 唯一事實來源，`scripts/dev_stack.py` 用同一個 `settings.misaka_api_port` 啟動 uvicorn），Host 缺 port 時一律視為 scheme 預設 80 比對，絕不因缺省而略過；再比對 `Origin`（若有）是否精確落在允許清單（loopback 前後端 port＋Tauri 預設 origin＋`MISAKA_ALLOWED_ORIGINS` 環境變數擴充），`null` 一律拒絕；完全沒有 Origin 標頭時視為本機工具呼叫（如 curl），僅靠 Host 檢查放行。GET/HEAD/OPTIONS 不受影響（OPTIONS 讓既有 CORS preflight 正常運作）。CORS 中介層改用同一份 `resolve_allowed_origins()` 產生的清單，不再是先前「任意 port 的 localhost/127.0.0.1 regex」。

`core/network/safe_url.py` 提供 `validate_download_url()`：先驗證 scheme 為 https、無 embedded userinfo、host 精確比對（非子字串）`huggingface.co`／`hf.co`（含子網域），再對 hostname 實際做 DNS 解析，任何一個解析到的位址若屬於私有／loopback／link-local／multicast／reserved／unspecified（含 IPv4-mapped IPv6 位址攤平後判斷）即拒絕；literal IP host 一律直接拒絕（允許清單以名稱為準）。`core/llm/local_manager.py` 的 `download_model` 改為 `follow_redirects=False` 手動迴圈，每次收到 3xx 都先對 `Location` 目標跑一次完整驗證器才繼續，最多 5 跳，超過即拒絕。

## 範圍外發現（未在本次修復）

- `core/network/service.py:123`（`_probe_targets` 內的 `httpx.get(url, ...)`）：`url` 來源是站主自行在 `.env` 設定的雲端供應商 base URL（Anthropic/OpenAI/Gemini）或 Ollama base URL，屬於**站主自控設定**而非任何請求路徑上的使用者輸入；且此函式的用途本來就是探測任意雲端端點是否可達（並非只探測 Hugging Face），套用本次的 SSRF 允許清單反而會破壞其設計用途。判定不屬於待回答 #48 範圍，未修改，僅記錄於此供覆核。
- `core/llm/local_manager.py` 既有的 `re.search(r"\.[a-zA-Z0-9]{2,10}$", filename)` 副檔名檢查上限 10 字元，會誤擋真實常見的 `.safetensors`（11 字元）副檔名——這是本次修復前就存在的既有限制，與 #47/#48 無關，未在本次改動範圍內，僅記錄供站主決定是否另開票修復。
- **DNS TOCTOU（opus fresh-review F3，殘留風險，本批次刻意不修）**：`core/network/safe_url.py` 的 `_resolve_and_check` 先對 hostname 做一次 DNS 解析並檢查每個位址，但實際發出請求時 `httpx.stream`（`core/llm/local_manager.py`）會**再獨立解析一次**才建立連線；若攻擊者能在這兩次解析之間讓 DNS 回應改變（經典 DNS rebinding），第二次解析可能指向已通過檢查之外的內網／保留位址，繞過已驗證的結果。修法需要一個自訂的 httpx transport，在保留 SNI 與 `Host` 標頭的前提下直接撥打「第一次驗證時解析到的那個 IP」（而非讓 httpx 自行重新解析），成本明顯超出本任務範圍，故本批次刻意不做。前提條件是攻擊者已能控制 `huggingface.co` 或 `*.hf.co` 的 DNS 回應——已具備此能力的攻擊者本身即代表更嚴重的入侵，故列為低風險殘留，留待站主決定是否另開票處理。

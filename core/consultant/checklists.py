CHECKLISTS: dict[str, list[str]] = {
    "music": [
        "用途場景是什麼？",
        "希望的情緒或氛圍是什麼？",
        "BPM 或節奏快慢有要求嗎？",
        "樂器或聲音元素偏好是什麼？",
        "長度、loop、格式是否有要求？",
    ],
    "image": [
        "素材用途是立繪、場景還是 UI？",
        "解析度或尺寸目標是什麼？",
        "風格參考與色彩偏好是什麼？",
        "是否需要透明背景或 tileable？",
    ],
    "voice": [
        "角色年齡與個性是什麼？",
        "語氣、情緒、語速怎麼設定？",
        "語言、口音或參考聲線是什麼？",
        "是否需要對嘴時間戳？",
    ],
    "video": [
        "用途是過場、宣傳還是 loop 動畫？",
        "目標時長與解析度是什麼？",
        "是否需要音軌或旁白？",
        "鏡頭運動與節奏有沒有偏好？",
    ],
    # spec §7.1.1 / M4.b — training-flow checklist (four required steps)
    "training": [
        "(a) 請指定或選擇要訓練的角色 (CharacterSheet)：角色名稱、外觀錨點、觸發詞是什麼？",
        "(b) 訓練資料集 (DatasetPack) 已備妥了嗎？資料來源、清洗狀態與授權是什麼？",
        "(c) 選擇或新建訓練配方 (TrainingRecipe)：底模、LoRA rank、epoch 數、optimizer 與 caption 策略為何？",
        "(d) 選擇或建立 LoRA stack (LoraPreset)：要組合哪幾個 LoRA？角色/服裝/風格的權重各是多少？",
        "(e, 選填) 是否需要設定圖轉影音配方 (ImageToVideoRecipe) 以延伸生成動畫？",
    ],
}

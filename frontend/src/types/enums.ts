export enum AppLocale {
  ZH_TW = "zh-TW",
  EN = "en",
  JA = "ja",
}

export enum PageKey {
  PROJECTS = "projects",
  PROJECT = "project",
  VERSIONS = "versions",
  STUDIO = "studio",
  ASSETS = "assets",
  SETTINGS = "settings",
}

export enum NetworkTone {
  NEUTRAL = "neutral",
  SUCCESS = "success",
  WARNING = "warning",
}

export enum NetworkStatus {
  BOOTSTRAPPING = "bootstrapping",
  CORE_ONLINE = "coreOnline",
  CORE_OFFLINE = "coreOffline",
}

/** User-selected network policy (spec §11.5 three modes). */
export enum NetworkMode {
  AUTO = "auto",
  ALWAYS_OFFLINE = "always_offline",
  ALWAYS_ONLINE = "always_online",
}

/** Effective network state derived from mode plus live probes (spec §11.5). */
export enum NetworkState {
  ONLINE = "online",
  DEGRADED = "degraded",
  OFFLINE = "offline",
}

export enum Modality {
  TEXT = "text",
  MUSIC = "music",
  IMAGE = "image",
  VOICE = "voice",
  VIDEO = "video",
  TRAINING = "training",
}

export enum ProjectType {
  RPG = "RPG",
  FPS = "FPS",
  PUZZLE = "Puzzle",
  VN = "VN",
  ANIME = "Anime",
  PLATFORMER = "Platformer",
  OTHER = "Other",
}

export enum MessageKey {
  SUCCESS_ADD0 = "message.success.add0",
  SUCCESS_FETCH0 = "message.success.fetch0",
  SUCCESS_SWITCH0 = "message.success.switch0",
  FAIL_400 = "message.fail.400",
  FAIL_401 = "message.fail.401",
  FAIL_404 = "message.fail.404",
  FAIL_409 = "message.fail.409",
  FAIL_500 = "message.fail.500",
}

export enum ProviderName {
  OLLAMA = "ollama",
  ANTHROPIC = "anthropic",
  OPENAI = "openai",
  GEMINI = "gemini",
}

export enum ProviderMode {
  LOCAL = "local",
  CLOUD = "cloud",
}

export enum ProviderStatus {
  READY = "ready",
  CONFIGURED = "configured",
  UNAVAILABLE = "unavailable",
  DISABLED = "disabled",
}

export enum ConsultantState {
  INTAKE = "intake",
  CLARIFY = "clarify",
  SUMMARY = "summary",
  GENERATE = "generate",
  REFINE = "refine",
  ACCEPT = "accept",
}

export enum GenerationJobStatus {
  PLANNED = "planned",
  READY = "ready",
  BLOCKED = "blocked",
  RUNNING = "running",
  COMPLETED = "completed",
  FAILED = "failed",
}

/** Training entity kinds used in suggestion cards (spec §7.1.1 / M4.b). */
export enum TrainingEntityKind {
  CHARACTER_SHEET = "character_sheet",
  DATASET_PACK = "dataset_pack",
  TRAINING_RECIPE = "training_recipe",
  LORA_PRESET = "lora_preset",
  I2V_RECIPE = "i2v_recipe",
}

/** Cleaning status values for DatasetPack (spec §7.1.1). */
export enum DatasetCleaningStatus {
  RAW = "raw",
  CLEANED = "cleaned",
  TAGGED = "tagged",
}

/** Workflow kinds for ImageToVideoRecipe (spec §7.1.1). */
export enum I2vWorkflowKind {
  ANIMATEDIFF = "animatediff",
  SVD = "svd",
  IMAGE_TO_VIDEO = "image-to-video",
}

/** LoRA layer kinds for LoraPreset (spec §7.1.1). */
export enum LoraLayerKind {
  CHARACTER = "character",
  COSTUME = "costume",
  STYLE = "style",
}

/** Refine strategy ladder (spec §6.2 / §8.2). Matches Python RefineStrategy enum. */
export enum RefineStrategy {
  METADATA_ONLY = "metadata_only",
  PARAM_RETUNE = "param_retune",
  IMG2IMG = "img2img",
  INPAINT = "inpaint",
  FULL_REGEN = "full_regen",
}

/**
 * Tri-state value for license fields that can be true, false, or unknown (null).
 * Use this to drive visual rendering — UNKNOWN must never look like NO.
 * Matches backend `bool | None` pattern (spec §2 / M5.2 truthful delivery).
 */
export enum TriState {
  YES = "yes",
  NO = "no",
  UNKNOWN = "unknown",
}

/** Version-tree node status labels used in the DAG UI (spec §8.2). */
export enum VersionNodeStatus {
  /** Asset is a completed root or refined version. */
  COMPLETED = "completed",
  /** Asset is still being generated. */
  RUNNING = "running",
  /** Asset generation failed. */
  FAILED = "failed",
}

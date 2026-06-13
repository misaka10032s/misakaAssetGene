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

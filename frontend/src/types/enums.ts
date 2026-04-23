export enum AppLocale {
  ZH_TW = "zh-TW",
  EN = "en",
  JA = "ja",
}

export enum PageKey {
  PROJECTS = "projects",
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

export enum Modality {
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

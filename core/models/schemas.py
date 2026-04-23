from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageKey(str, Enum):
    SUCCESS_ADD0 = "message.success.add0"
    SUCCESS_FETCH0 = "message.success.fetch0"
    SUCCESS_SWITCH0 = "message.success.switch0"
    FAIL_400 = "message.fail.400"
    FAIL_401 = "message.fail.401"
    FAIL_404 = "message.fail.404"
    FAIL_409 = "message.fail.409"
    FAIL_500 = "message.fail.500"


class ProjectTypeSuggestion(str, Enum):
    RPG = "RPG"
    FPS = "FPS"
    PUZZLE = "Puzzle"
    VN = "VN"
    ANIME = "Anime"
    PLATFORMER = "Platformer"
    OTHER = "Other"


class Modality(str, Enum):
    MUSIC = "music"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"


class ProviderName(str, Enum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


class ProviderMode(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class ProviderStatus(str, Enum):
    READY = "ready"
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class ApiResponse(BaseModel):
    message: MessageKey
    data: Any | None = None


class ApiErrorResponse(BaseModel):
    message: MessageKey
    detail: Any | None = None


class HealthData(BaseModel):
    status: str
    repo_root: str
    environment: str


class ClarifyRequest(BaseModel):
    modality: Modality
    prompt: str = Field(min_length=1)


class ToolSnapshot(BaseModel):
    name: str
    version: str


class WorkerSnapshot(BaseModel):
    name: str
    reference: str


class ProviderSnapshot(BaseModel):
    name: ProviderName
    mode: ProviderMode
    status: ProviderStatus
    configured: bool
    base_url: str


class IntegrationSnapshot(BaseModel):
    tools: list[ToolSnapshot]
    workers: list[WorkerSnapshot]
    providers: list[ProviderSnapshot]
    registry_categories: list[str]
    model_search_paths: list[str]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    synopsis: str = ""


class ProjectSelectRequest(BaseModel):
    name: str = Field(min_length=1)


class ProjectSummary(BaseModel):
    name: str
    type: str
    synopsis: str = ""


class ProjectListData(BaseModel):
    projects: list[ProjectSummary]
    current_project: str | None = None


class SynopsisOptimizeRequest(BaseModel):
    project_name: str = Field(min_length=1)
    project_type: str = Field(min_length=1)
    synopsis: str = Field(min_length=1)


class SynopsisOptimizeResult(BaseModel):
    optimized_synopsis: str
    strategy: str
    provider: ProviderName | None = None


class LocalLlmStatus(BaseModel):
    server: str
    base_url: str
    is_running: bool
    managed_by_app: bool
    executable_found: bool
    executable_path: str | None = None
    provider_order: list[str]


class ModelDownloadRequest(BaseModel):
    url: str = Field(min_length=1)


class ModelDownloadResult(BaseModel):
    filename: str
    saved_path: str
    source_url: str

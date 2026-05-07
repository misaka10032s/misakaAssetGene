import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.models.schemas import ConversationEntry, ProjectCreateRequest, ProjectSummary, ProjectTypeSuggestion
from core.models.schemas import ConsultantAnalysis
from core.project.style_guide import build_initial_style_guide

PROJECT_TYPES = [project_type.value for project_type in ProjectTypeSuggestion]
logger = logging.getLogger("misaka.project")


class ProjectValidationError(ValueError):
    """Raised when a project request does not satisfy schema rules."""


class ProjectConflictError(FileExistsError):
    """Raised when a project already exists."""


class ProjectNotFoundError(FileNotFoundError):
    """Raised when a selected project does not exist."""


class ProjectManager:
    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.selection_path = self.projects_root / ".current_project.json"
        logger.info("ProjectManager initialized at %s", self.projects_root)

    def list_projects(self) -> list[ProjectSummary]:
        projects: list[ProjectSummary] = []
        for project_json in self.projects_root.glob("*/project.json"):
            data = json.loads(project_json.read_text(encoding="utf-8"))
            projects.append(self._normalize_project_summary(project_json.parent, data))
        logger.info("Loaded %d project(s) from disk", len(projects))
        return sorted(projects, key=lambda item: item.name)

    def create_project(self, request: ProjectCreateRequest) -> ProjectSummary:
        self._validate_request(request)
        project_id = self._build_project_id(request.name)
        project_dir = self.projects_root / project_id
        try:
            project_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise ProjectConflictError(f"Project already exists: {request.name}") from error
        logger.info("Creating project directory %s", project_dir)

        for relative_dir in [
            "assets/images",
            "assets/audio",
            "assets/video",
            "assets/text",
            "vectors",
            "_external",
            ".cache",
        ]:
            (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)

        project_summary = ProjectSummary(
            id=project_id,
            name=request.name.strip(),
            type=request.type.strip(),
            synopsis=request.synopsis.strip(),
        )
        (project_dir / "project.json").write_text(
            json.dumps(project_summary.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (project_dir / "style_guide.md").write_text(
            build_initial_style_guide(request),
            encoding="utf-8",
        )
        self._conversation_path(project_dir).write_text('{"entries":[]}\n', encoding="utf-8")
        if self.get_current_project_id() is None:
            self.select_project(project_summary.id)
        logger.info("Project %s initialized with project.json and style_guide.md", project_summary.name)
        return project_summary

    def select_project(self, project_id: str) -> ProjectSummary:
        summary, _ = self.get_project(project_id)
        self.selection_path.write_text(
            json.dumps({"id": summary.id}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Selected project %s", summary.id)
        return summary

    def get_project(self, project_id: str) -> tuple[ProjectSummary, Path]:
        direct_path = self.projects_root / project_id / "project.json"
        if direct_path.exists():
            data = json.loads(direct_path.read_text(encoding="utf-8"))
            return self._normalize_project_summary(direct_path.parent, data), direct_path.parent

        for project_json in self.projects_root.glob("*/project.json"):
            data = json.loads(project_json.read_text(encoding="utf-8"))
            summary = self._normalize_project_summary(project_json.parent, data)
            if summary.id == project_id or summary.name == project_id:
                return summary, project_json.parent

        raise ProjectNotFoundError(f"Project not found: {project_id}")

    def get_current_project_id(self) -> str | None:
        if not self.selection_path.exists():
            return None
        payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
        selected_id = str(payload.get("id") or payload.get("name") or "") or None
        if not selected_id:
            return None

        try:
            summary, _ = self.get_project(selected_id)
        except ProjectNotFoundError:
            logger.warning("Current project selection is stale; clearing %s", selected_id)
            self.selection_path.unlink(missing_ok=True)
            return None

        return summary.id

    def list_conversation_entries(self, project_id: str) -> list[ConversationEntry]:
        _, project_dir = self.get_project(project_id)
        conversation_path = self._conversation_path(project_dir)
        if not conversation_path.exists():
            return []
        payload = json.loads(conversation_path.read_text(encoding="utf-8"))
        return [ConversationEntry(**entry) for entry in payload.get("entries", [])]

    def append_conversation_entries(self, project_id: str, entries: list[ConversationEntry]) -> list[ConversationEntry]:
        _, project_dir = self.get_project(project_id)
        existing_entries = self.list_conversation_entries(project_id)
        merged_entries = [*existing_entries, *entries]
        self._conversation_path(project_dir).write_text(
            json.dumps({"entries": [entry.model_dump(mode="json") for entry in merged_entries]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return merged_entries

    def build_conversation_entry(
        self,
        *,
        role: str,
        content: str,
        modality: str | None = None,
        questions: list[str] | None = None,
        analysis: ConsultantAnalysis | None = None,
    ) -> ConversationEntry:
        return ConversationEntry(
            id=uuid.uuid4().hex,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc),
            modality=modality,
            questions=questions or [],
            analysis=analysis,
        )

    def _validate_request(self, request: ProjectCreateRequest) -> None:
        normalized_name = request.name.strip()
        if not normalized_name:
            raise ProjectValidationError("Project name must not be empty.")
        if not request.type.strip():
            raise ProjectValidationError("Project type must not be empty.")
        self._build_project_id(normalized_name)
        logger.info("Validated project request name=%s type=%s", request.name, request.type)

    def _build_project_id(self, project_name: str) -> str:
        normalized = re.sub(r"[^\w\s-]", "", project_name.strip(), flags=re.UNICODE)
        normalized = re.sub(r"[-\s]+", "-", normalized, flags=re.UNICODE).strip("-_").lower()
        if not normalized:
            raise ProjectValidationError("Project name must contain at least one usable letter or number.")
        return normalized

    def _normalize_project_summary(self, project_dir: Path, data: dict) -> ProjectSummary:
        normalized_name = str(data.get("name") or project_dir.name).strip()
        normalized_id = str(data.get("id") or "").strip() or self._build_project_id(normalized_name)
        summary = ProjectSummary(
            id=normalized_id,
            name=normalized_name,
            type=str(data.get("type") or ""),
            synopsis=str(data.get("synopsis") or ""),
        )
        if data.get("id") != normalized_id:
            (project_dir / "project.json").write_text(
                json.dumps(summary.model_dump(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return summary

    def _conversation_path(self, project_dir: Path) -> Path:
        return project_dir / "conversation.json"

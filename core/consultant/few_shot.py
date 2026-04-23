from pathlib import Path


def load_prompt_template(prompt_dir: Path, modality: str) -> str:
    prompt_path = prompt_dir / f"{modality}.md"
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8")

import re

REFERENCE_PATTERN = re.compile(
    r"^@(?P<project>[a-zA-Z0-9_-]+)/(?P<asset_path>[a-zA-Z0-9_./-]+)(?:#(?P<version>[a-zA-Z0-9_-]+))?$"
)


def parse_reference(reference: str) -> dict[str, str] | None:
    match = REFERENCE_PATTERN.match(reference)
    if not match:
        return None
    return {key: value or "" for key, value in match.groupdict().items()}

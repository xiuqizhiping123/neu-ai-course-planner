"""Load one published course-planning release from the local data directory."""

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "runtime"


def load_runtime_data(root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    """Read a consistent release, including approved historical course codes."""
    pointer = json.loads((root / "current.json").read_bytes())
    release_id = pointer["release"]
    if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("Invalid course data release ID")
    release = root / "releases" / release_id
    return {
        name: json.loads((release / f"{name}.json").read_bytes())
        for name in ("courses", "sections", "programs", "external_courses")
    }

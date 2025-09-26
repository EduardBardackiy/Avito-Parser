from __future__ import annotations

from typing import Any, Dict, List, Optional
from pathlib import Path


def sanitize_query_params(params: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None and v != ""}


def load_proxies(file_path: Optional[str]) -> List[str]:
    if not file_path:
        return []
    p = Path(file_path)
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]



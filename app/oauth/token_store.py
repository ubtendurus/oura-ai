from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[Dict[str, Any]]:
        if not self._path.exists():
            return None
        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        return json.loads(raw)

    def save(self, payload: Dict[str, Any]) -> None:
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

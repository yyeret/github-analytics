"""Persistent raw GitHub collection cache used by the web API."""

import json
import os
import re
import tempfile
from pathlib import Path


_OWNER = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_REPO = re.compile(r"[a-z0-9_][a-z0-9_.-]*\Z")
_RAW_LISTS = ("pre_ai_prs", "recent_prs", "open_prs")


class RepositoryCache:
    def __init__(self, root=Path(".cache/repositories")):
        self.root = Path(root)

    def path_for(self, repo):
        parts = repo.split("/")
        if len(parts) != 2 or not _OWNER.fullmatch(parts[0]) or not _REPO.fullmatch(parts[1]) or parts[1] in (".", ".."):
            raise ValueError(f"Invalid repository slug: {repo!r}")
        return self.root / parts[0] / f"{parts[1]}.json"

    @staticmethod
    def _valid(repo, data):
        return (
            isinstance(data, dict)
            and data.get("repo") == repo
            and isinstance(data.get("captured_at"), str)
            and bool(data["captured_at"])
            and all(isinstance(data.get(key), list) for key in _RAW_LISTS)
            and isinstance(data.get("issues"), dict)
        )

    def load(self, repo):
        path = self.path_for(repo)
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return None
        return data if self._valid(repo, data) else None

    def save(self, repo, data):
        path = self.path_for(repo)
        if not self._valid(repo, data):
            raise ValueError(f"Incomplete collection for {repo!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

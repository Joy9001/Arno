"""A filesystem workspace for Skills on Windows and POSIX.

`LocalWorkspaceBackend` from Pydantic AI refuses Windows because its command timeout kills a
process group. Skills only needs file reads, so this backend implements the filesystem
protocol and nothing else. It exposes a POSIX-style virtual root because the Workspace joins
paths with `posixpath`, which does not recognize `C:\\` as absolute.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from pydantic_ai.workspaces import FileEntry

VIRTUAL_ROOT = "/fin-agent-workspace"


class LocalFilesBackend:
    def __init__(self, working_dir: str | Path) -> None:
        self._root = Path(working_dir).expanduser().resolve()

    @property
    def ref(self) -> Any:
        return None

    async def working_dir(self) -> str:
        return VIRTUAL_ROOT

    def _path(self, path: str) -> Path:
        if path == VIRTUAL_ROOT:
            return self._root
        prefix = VIRTUAL_ROOT + "/"
        if path.startswith(prefix):
            relative = path[len(prefix) :]
            target = (self._root / relative).resolve()
            if target != self._root and self._root not in target.parents:
                raise ValueError("path escapes the workspace")
            return target
        target = Path(path)
        if not target.is_absolute():
            raise ValueError(f"path must be absolute, got {path!r}")
        return target

    def _virtual(self, target: Path) -> str:
        relative = target.relative_to(self._root).as_posix()
        return VIRTUAL_ROOT if relative == "." else f"{VIRTUAL_ROOT}/{relative}"

    async def read_bytes(self, path: str) -> bytes:
        return self._path(path).read_bytes()

    async def write_bytes(self, path: str, data: bytes) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def stat(self, path: str) -> FileEntry:
        target = self._path(path)
        info = target.stat()
        return FileEntry(
            name=target.name,
            path=self._virtual(target),
            is_dir=target.is_dir(),
            size=None if target.is_dir() else info.st_size,
        )

    async def list_dir(self, path: str) -> list[FileEntry]:
        target = self._path(path)
        return [await self.stat(self._virtual(child)) for child in sorted(target.iterdir())]

    async def make_dir(self, path: str) -> None:
        self._path(path).mkdir(parents=True, exist_ok=True)

    async def remove(self, path: str) -> None:
        target = self._path(path)
        if target == self._root or self._root in target.parents:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            return
        raise ValueError("refusing to remove a path outside the workspace")

    async def exists(self, path: str) -> bool:
        return self._path(path).exists()

    async def realpath(self, path: str) -> str:
        return self._virtual(Path(os.path.realpath(self._path(path))))


__all__ = ["LocalFilesBackend", "VIRTUAL_ROOT"]

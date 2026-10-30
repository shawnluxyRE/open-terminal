"""Filesystem abstraction for multi-user mode.

Provides :class:`UserFS`, a unified interface for file operations.

All I/O uses native Python (``aiofiles`` / ``os``).  In multi-user mode
the server process is added to each provisioned user's group, and home
directories are ``chmod 2770`` (setgid + group rwx), so standard file
operations work without subprocess.

After each write operation a ``sudo chown`` call fixes file ownership
so that files belong to the provisioned user, not the server process.
"""

import asyncio
import os
import io
import json
import re
import tempfile
import shutil
import subprocess

import aiofiles
import aiofiles.os
from starlette.responses import FileResponse

from open_terminal.utils.file_access import FileAccessDenied, FileAccessPolicy
from open_terminal.utils.limits import CHUNK_SIZE, run_io


class FullFileResponse(FileResponse):
    """Stream the original full response without adding HTTP range/cache semantics."""

    def set_stat_headers(self, stat_result):
        self.headers.setdefault("content-length", str(stat_result.st_size))

    async def __call__(self, scope, receive, send):
        if "accept-ranges" in self.headers:
            del self.headers["accept-ranges"]
        scope = {**scope, "headers": [(key, value) for key, value in scope.get("headers", []) if key.lower() not in (b"range", b"if-range")]}
        await super().__call__(scope, receive, send)


class TemporaryFileResponse(FullFileResponse):
    """Remove generated files even when sending the response is cancelled."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


_LINE_BREAK = re.compile(r"\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]")


def line_range_offsets(text, start_line=None, end_line=None):
    start_boundary = (start_line or 1) - 1
    start = 0 if start_boundary == 0 else len(text)
    end = len(text)
    last_boundary = max(start_boundary, end_line or 0)
    if last_boundary:
        for number, match in enumerate(_LINE_BREAK.finditer(text), 1):
            if number == start_boundary:
                start = match.end()
            if number == end_line:
                end = match.end()
            if number >= last_boundary:
                break
    return start, end


def text_chunks(source):
    """Yield splitlines-compatible line numbers and bounded pieces of text."""
    number = 1
    pending = ""
    for chunk in iter(lambda: source.read(CHUNK_SIZE), ""):
        chunk = pending + chunk
        pending = "\r" if chunk.endswith("\r") else ""
        if pending:
            chunk = chunk[:-1]
        start = 0
        for match in _LINE_BREAK.finditer(chunk):
            end = match.end()
            yield number, chunk[start:end]
            number += 1
            start = end
        if start < len(chunk):
            yield number, chunk[start:]
    if pending:
        yield number, pending


def read_text_range(path, start_line=None, end_line=None):
    selected = io.StringIO()
    total = 0
    with open(path, encoding="utf-8") as source:
        for total, chunk in text_chunks(source):
            if total >= (start_line or 1) and (end_line is None or total <= end_line):
                selected.write(chunk)
    return {"total_lines": total, "content": selected.getvalue()}


def _build_text_file_response(path, display_path, start_line=None, end_line=None, normalize_newlines=True):
    """Build the original JSON response on disk, including arbitrarily long lines."""
    fd, temporary = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output, open(path, encoding="utf-8", newline=None if normalize_newlines else "") as source:
            output.write('{"path":' + json.dumps(display_path) + ',"content":"')
            total = 0
            for total, chunk in text_chunks(source):
                if total >= (start_line or 1) and (end_line is None or total <= end_line):
                    output.write(json.dumps(chunk, ensure_ascii=False)[1:-1])
            output.write('","total_lines":' + str(total) + '}')
        return TemporaryFileResponse(temporary, media_type="application/json")
    except BaseException:
        os.unlink(temporary)
        raise


async def text_file_response(*args):
    task = asyncio.create_task(asyncio.to_thread(_build_text_file_response, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            response = await task
        except Exception:
            pass
        else:
            os.unlink(response.path)
        raise


class UserFS:
    """Filesystem operations scoped to an optional OS user.

    *username* is used for ownership fixups after writes (``None`` = stdlib).
    *home* is the user's home directory (default working directory).

    When *username* is set, all file operations authorize the lexical entry
    and resolved target against this home and the public read-only roots.
    """

    def __init__(
        self,
        username: str | None = None,
        home: str | None = None,
        *,
        public_read_roots: tuple[str, ...] = (),
    ):
        self.username = username
        self.home = home or os.getcwd()
        self.public_read_roots = public_read_roots
        self._policy = FileAccessPolicy(self.home, public_read_roots) if username else None

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def resolve_path(self, path: str, cwd: str | None = None) -> str:
        """Resolve *path* to an absolute path relative to the user's home.

        Relative paths are joined
        to *cwd* (if provided) or ``self.home`` so that they resolve against
        the session's working directory rather than the server process's
        ``os.getcwd()``.

        In multi-user mode, paths under ``/home/user`` (the server process's
        default home) are automatically rewritten to the provisioned user's
        home directory, since LLMs often hardcode that path.
        """
        if os.path.isabs(path):
            # Swap /home/user (and /home/usr, a common LLM hallucination)
            # → user's actual home when multi-user is active
            if self.username:
                for prefix in ("/home/user", "/home/usr"):
                    if path == prefix:
                        path = self.home
                        break
                    elif path.startswith(prefix + "/"):
                        path = self.home + path[len(prefix):]
                        break
        else:
            path = os.path.join(cwd or self.home, path)
        # Resolving '..' before following a symlink could turn an unauthorized
        # path into an apparently private path. The policy resolves in OS order.
        return path if self.username else os.path.normpath(path)

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def require_path(self, path: str, *, write: bool = False) -> str:
        """REQ-002 entry point: authorize before I/O and return a canonical path.

        Multi-user mode delegates to FileAccessPolicy for this home and the
        administrator's public read roots; rejection is FileAccessDenied with
        a fixed message. Single-user mode keeps existing unrestricted behavior.
        Callers resolve aliases/cwd first.
        See docs/design/file-access.md for enumeration and move/remove rules.
        """
        if self._policy is not None:
            return self._policy.require(path, write=write)
        return path

    def is_path_allowed(self, path: str) -> bool:
        """Return whether a path can be read without exposing denied entries."""
        return self._policy is None or self._policy.allows(path)

    def _check_path(self, path: str) -> None:
        """Compatibility read check; I/O callers should use require_path's result."""
        self.require_path(path)

    async def _chown(self, path: str) -> None:
        """Fix ownership of *path* to the provisioned user.

        Also sets group-write permission so the server process (which is
        in the provisioned user's group) can overwrite the file on
        subsequent writes.
        """
        if self.username:
            is_link = os.path.islink(path)
            await asyncio.to_thread(
                subprocess.run,
                ["sudo", "chown", "-h", f"{self.username}:{self.username}", path],
                check=True, capture_output=True,
            )
            # A renamed relative symlink can refer to a different target. Only
            # change the link owner, never permissions on its target.
            if is_link:
                return
            await asyncio.to_thread(
                subprocess.run,
                ["sudo", "chmod", "g+w", path],
                check=True, capture_output=True,
            )

    async def _ensure_parents(self, path: str) -> None:
        """Create parent directories for *path* with correct permissions.

        In multi-user mode, uses ``sudo -u`` to create directories as the
        provisioned user (so creation succeeds even inside ``755`` dirs
        made by ``run_command``), then sets ``2770`` on each directory in
        the chain so the server process has group-write access.

        In single-user mode, falls back to plain ``makedirs``.
        """
        if not self.username:
            await aiofiles.os.makedirs(path, exist_ok=True)
            return
        # Create as the provisioned user to bypass 755 restrictions.
        await asyncio.to_thread(
            subprocess.run,
            ["sudo", "-u", self.username, "mkdir", "-p", path],
            check=True, capture_output=True,
        )
        # Walk upward, setting 2770 so the server process (which is in the
        # user's group) can create files inside these directories.
        target = os.path.normpath(path)
        home = os.path.normpath(self.home)
        while target != home and target.startswith(home + "/"):
            await asyncio.to_thread(
                subprocess.run,
                ["sudo", "chmod", "2770", target],
                check=True, capture_output=True,
            )
            target = os.path.dirname(target)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def read(self, path: str) -> bytes:
        """Read raw bytes from *path*."""
        path = self.require_path(path)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read text from *path*."""
        path = self.require_path(path)
        async with aiofiles.open(path, "r", encoding=encoding, errors="strict") as f:
            return await f.read()

    async def exists(self, path: str) -> bool:
        """Check if *path* exists."""
        path = self.require_path(path)
        return await aiofiles.os.path.exists(path)

    async def isfile(self, path: str) -> bool:
        """Check if *path* is a regular file."""
        path = self.require_path(path)
        return await aiofiles.os.path.isfile(path)

    async def isdir(self, path: str) -> bool:
        """Check if *path* is a directory."""
        path = self.require_path(path)
        return await aiofiles.os.path.isdir(path)

    async def stat(self, path: str) -> dict:
        """Return size, mtime, and type for *path*."""
        target = self.require_path(path)
        s = await aiofiles.os.stat(target)
        return {
            "size": s.st_size,
            "modified": s.st_mtime,
            "type": "directory" if os.path.isdir(target) else "file",
            "writable": self._is_writable_sync(path),
        }

    def _is_writable_sync(self, path: str) -> bool:
        try:
            path = self.require_path(path, write=True)
        except PermissionError:
            return False
        try:
            if os.statvfs(path).f_flag & getattr(os, "ST_RDONLY", 0):
                return False
        except (AttributeError, OSError):
            pass
        try:
            return os.access(path, os.W_OK, effective_ids=True)
        except (TypeError, OSError, NotImplementedError, AttributeError):
            return os.access(path, os.W_OK)

    async def is_writable(self, path: str) -> bool:
        """Return whether the current process can write to *path*."""
        return await asyncio.to_thread(self._is_writable_sync, path)

    async def listdir(self, path: str) -> list[dict]:
        """List directory contents with type, size, mtime, and writability."""
        target = self.require_path(path)
        def _list_sync():
            entries = []
            for name in sorted(os.listdir(target)):
                full = os.path.join(path, name)
                try:
                    canonical = self.require_path(full)
                    s = os.stat(canonical)
                    entries.append({
                        "name": name,
                        "type": "directory" if os.path.isdir(canonical) else "file",
                        "size": s.st_size,
                        "modified": s.st_mtime,
                        "writable": self._is_writable_sync(full),
                    })
                except OSError:
                    continue
            return entries
        return await run_io(_list_sync)

    async def walk(self, path: str) -> list[tuple[str, list[str], list[str]]]:
        """Walk directory tree. Returns list of (dirpath, dirnames, filenames).

        Authorize entries before metadata and omit denied names. Directory
        symlinks can be listed but are never traversed recursively.
        """
        target = self.require_path(path)
        def _walk_filtered():
            result = []
            pending = [(path, target)]
            while pending:
                dirpath, canonical_dir = pending.pop()
                dirnames, filenames, descend = [], [], []
                try:
                    names = sorted(os.listdir(canonical_dir))
                except OSError:
                    continue
                for name in names:
                    full = os.path.join(dirpath, name)
                    try:
                        canonical = self.require_path(full)
                        if os.path.isdir(canonical):
                            dirnames.append(name)
                            if not os.path.islink(os.path.join(canonical_dir, name)):
                                descend.append((full, canonical))
                        else:
                            filenames.append(name)
                    except OSError:
                        continue
                result.append((dirpath, dirnames, filenames))
                pending.extend(reversed(descend))
            return result
        return await run_io(_walk_filtered)

    # ------------------------------------------------------------------
    # Write operations (native Python + chown for correct ownership)
    # ------------------------------------------------------------------

    async def write(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write text *content* to *path*, creating parent dirs."""
        path = self.require_path(path, write=True)
        parent = os.path.dirname(path)
        if parent:
            await self._ensure_parents(parent)
        async with aiofiles.open(path, "w", encoding=encoding) as f:
            await f.write(content)
        await self._chown(path)

    async def write_bytes(self, path: str, data: bytes) -> None:
        """Write raw *data* to *path*, creating parent dirs."""
        path = self.require_path(path, write=True)
        parent = os.path.dirname(path)
        if parent:
            await self._ensure_parents(parent)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        await self._chown(path)

    async def mkdir(self, path: str) -> None:
        """Create directory *path* and parents."""
        path = self.require_path(path, write=True)
        await self._ensure_parents(path)

    def _mutation_entry(self, path: str) -> str:
        """Authorize a mutation while preserving the final symlink entry.

        Following ancestor links is necessary to locate the entry. Following
        the last link for unlink/rename would instead mutate its target.
        """
        canonical = self.require_path(path, write=True)
        if self.username and canonical == os.path.realpath(self.home):
            raise FileAccessDenied("Access denied")
        entry = path.rstrip(os.sep)
        name = os.path.basename(entry)
        if name in ("", ".", ".."):
            return canonical
        parent = self.require_path(os.path.dirname(entry) or ".", write=True)
        return os.path.join(parent, name)

    async def remove(self, path: str) -> None:
        """Remove *path* (file or directory)."""
        path = self._mutation_entry(path)
        if os.path.isdir(path) and not os.path.islink(path):
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            await aiofiles.os.remove(path)

    async def move(self, source: str, destination: str) -> None:
        """Move *source* to *destination*."""
        source = self._mutation_entry(source)
        destination = self._mutation_entry(destination)
        if os.path.lexists(destination):
            raise FileExistsError("Destination already exists")
        if not os.path.isdir(os.path.dirname(destination)):
            raise FileNotFoundError("Destination parent directory does not exist")
        # shutil.move preserves links, including copytree(symlinks=True) in
        # its cross-filesystem directory fallback; it never copies their data.
        await asyncio.to_thread(shutil.move, source, destination)
        await self._chown(destination)

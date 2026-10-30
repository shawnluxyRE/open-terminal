"""Incremental filesystem searches with disk-backed Git and ripgrep results."""
from contextlib import contextmanager
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile

from open_terminal.utils.limits import CHUNK_SIZE


def walk_entries(fs, root, show_hidden=True):
    root = fs.require_path(root)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(fs.require_path(directory)) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            continue
        descend = []
        for entry in children:
            if not show_hidden and entry.name.startswith("."):
                continue
            path = os.path.join(directory, entry.name)
            try:
                target = fs.require_path(path)
                is_directory = os.path.isdir(target)
                yield path, "directory" if is_directory else "file"
                if is_directory and not entry.is_symlink():
                    descend.append(path)
            except OSError:
                continue
        pending.extend(reversed(descend))


def _through_directory_link(root, path):
    """Git index entries must not make recursive search follow a directory link."""
    parent = os.path.dirname(path)
    while parent != root and parent.startswith(root.rstrip(os.sep) + os.sep):
        if os.path.islink(parent):
            return True
        parent = os.path.dirname(parent)
    return False


def _nul_records(source):
    pending = b""
    while chunk := source.read(CHUNK_SIZE):
        records = (pending + chunk).split(b"\0")
        pending = records.pop()
        yield from records
    if pending:
        yield pending


@contextmanager
def disk_database():
    with tempfile.TemporaryDirectory() as directory:
        db = sqlite3.connect(os.path.join(directory, "search.sqlite"))
        try:
            db.execute("PRAGMA cache_size = -1024")
            db.execute("PRAGMA temp_store = FILE")
            yield db
        finally:
            db.close()


def _safe_git_search(fs, root):
    """Use Git only when its repository controls belong to this request.

    Git discovers metadata independently of the files it returns. A foreign
    index can therefore disclose names even if every returned worktree path
    appears to be inside home. Unusual worktree/config indirection falls back
    to the authorized filesystem walker instead of following those controls.
    """
    try:
        repository = root
        while True:
            marker = fs.require_path(os.path.join(repository, ".git"))
            if os.path.lexists(marker):
                if os.path.islink(os.path.join(repository, ".git")) or not os.path.isdir(marker):
                    return False
                break
            parent = os.path.dirname(repository)
            if parent == repository or not fs.is_path_allowed(parent):
                return False
            repository = parent

        # Worktree indirection and split-index files may refer outside the
        # visible tree. Check the files ls-files can use before invoking Git.
        for name in ("HEAD", "index", "config", "info/exclude", "commondir", "config.worktree"):
            target = fs.require_path(os.path.join(marker, name))
            if name in ("commondir", "config.worktree") and os.path.exists(target):
                return False
            if name == "config" and os.path.isfile(target):
                with open(target, encoding="utf-8-sig") as source:
                    configuration = source.read()
                # These settings read more control files, redirect the tree,
                # or invoke a helper. Normal core/remote/branch config works.
                if re.search(r"(?im)^\s*\[\s*include(?:if)?(?:\s|\])", configuration) or re.search(
                    r"(?im)(?:^|\])\s*(?:worktree|worktreeconfig|fsmonitor|excludesfile)\s*(?:=|$)", configuration
                ):
                    return False
        for name in os.listdir(marker):
            if name.startswith("sharedindex."):
                fs.require_path(os.path.join(marker, name))

        # Check ancestor ignore controls without an extra full tree scan.
        # Git itself refuses to follow nested .gitignore symlinks; returned
        # entries still pass the ordinary per-candidate authorization below.
        directory = root
        while True:
            fs.require_path(os.path.join(directory, ".gitignore"))
            if directory == repository:
                break
            directory = os.path.dirname(directory)
        return True
    except (OSError, UnicodeError):
        return False


def search_entries(fs, root, show_hidden=False, *, git_files_as_files=False):
    root = fs.require_path(root)
    if not _safe_git_search(fs, root):
        yield from walk_entries(fs, root, show_hidden)
        return
    with tempfile.TemporaryFile() as output:
        try:
            # Inherited Git variables and global configuration are outside
            # the requested workspace and must not redirect its search.
            environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS="0")
            result = subprocess.run(
                ["git", "-C", root, "ls-files", "-co", "--exclude-standard", "-z", "--", "."],
                stdout=output, stderr=subprocess.DEVNULL, env=environment,
            )
        except OSError:
            result = None
        if result is None or result.returncode:
            yield from walk_entries(fs, root, show_hidden)
            return
        output.seek(0)
        with disk_database() as db:
            db.execute("CREATE TABLE entries (path BLOB PRIMARY KEY)")
            for raw in _nul_records(output):
                rel = os.fsdecode(raw)
                if not show_hidden and any(part.startswith(".") for part in rel.replace("\\", "/").split("/")):
                    continue
                path = os.path.join(root, rel)
                if not fs.is_path_allowed(path) or _through_directory_link(root, path):
                    continue
                leaf = path
                while path != root and path.startswith(root.rstrip(os.sep) + os.sep):
                    if fs.is_path_allowed(path):
                        cursor = db.execute("INSERT OR IGNORE INTO entries VALUES (?)", (os.fsencode(path),))
                        if cursor.rowcount:
                            target = fs.require_path(path)
                            kind = "file" if git_files_as_files and path == leaf else "directory" if os.path.isdir(target) else "file"
                            yield path, kind
                    path = os.path.dirname(path)


def _safe_rg_controls(fs, root):
    """Preflight ignore inputs that rg follows independently of its results.

    Inspect directory entries and ignore controls, not ordinary file contents
    or .git object trees. Unlike Git, rg follows ignore-file symlinks.
    """
    git_safe = _safe_git_search(fs, root)
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            with os.scandir(fs.require_path(directory)) as entries:
                for entry in entries:
                    if entry.name == ".git":
                        if directory != root and not _safe_git_search(fs, directory):
                            git_safe = False
                        continue
                    path = os.path.join(directory, entry.name)
                    if entry.name in (".gitignore", ".ignore", ".rgignore"):
                        fs.require_path(path)
                    # This never stats a linked target. The directory itself
                    # is authorized before its entries are enumerated above.
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
    except OSError:
        return False, git_safe
    return True, git_safe


@contextmanager
def matching_files(query, root, show_hidden, *, fs=None):
    """Use rg's ignore rules without its potentially enormous JSON submatch arrays."""
    if fs is not None:
        root = fs.require_path(root)
    rg = shutil.which("rg")
    if not rg:
        yield None
        return
    control_flags = []
    if fs is not None:
        ignores_safe, git_safe = _safe_rg_controls(fs, root)
        if not ignores_safe:
            yield None
            return
        # Only controls inside the authorized requested tree participate.
        control_flags = ["--no-ignore-parent", "--no-ignore-global"]
        if not git_safe:
            control_flags.append("--no-ignore-vcs")
    with tempfile.TemporaryFile() as listing, tempfile.TemporaryFile() as output:
        # Enumerating with rg preserves its ignore rules. Its default traversal
        # does not follow symlinks; authorize each candidate before content rg.
        list_args = [rg, "--no-config", "--files", "--null", "--no-messages", *control_flags]
        if show_hidden:
            list_args.append("--hidden")
        try:
            result = subprocess.run([*list_args, "--", root], stdout=listing, stderr=subprocess.DEVNULL)
        except OSError:
            result = None
        if result is None or result.returncode not in (0, 1):
            yield None
            return
        listing.seek(0)
        args = [rg, "--no-config", "--no-ignore", "--files-with-matches", "--null", "--no-messages", "--fixed-strings", "--ignore-case"]

        def batches():
            batch, size = [], 0
            for raw in _nul_records(listing):
                path = os.fsdecode(raw)
                try:
                    target = fs.require_path(path) if fs is not None else path
                    if os.path.islink(path) or not os.path.isfile(target):
                        continue
                except OSError:
                    continue
                length = len(os.fsencode(target)) + 1
                if batch and (len(batch) >= 128 or size + length > 65536):
                    yield batch
                    batch, size = [], 0
                batch.append(target)
                size += length
            if batch:
                yield batch

        for batch in batches():
            try:
                result = subprocess.run([*args, "--", query, *batch], stdout=output, stderr=subprocess.DEVNULL)
            except OSError:
                result = None
            if result is None or result.returncode not in (0, 1):
                yield None
                return
        output.seek(0)
        with disk_database() as db:
            db.execute("CREATE TABLE matches (path BLOB PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO matches VALUES (?)",
                           ((os.fsencode(os.path.normpath(os.fsdecode(raw))),) for raw in _nul_records(output)))
            yield lambda path: db.execute("SELECT 1 FROM matches WHERE path = ?", (os.fsencode(path),)).fetchone() is not None


def ranked_page(candidates, offset, limit):
    # Deep pagination must not keep offset + limit full-content matches in RAM.
    with disk_database() as db:
        db.execute("CREATE TABLE results (score INTEGER, length INTEGER, name TEXT, payload TEXT)")
        total = 0
        for score, length, name, result in candidates:
            db.execute("INSERT INTO results VALUES (?, ?, ?, ?)", (score, length, name, json.dumps(result)))
            total += 1
        rows = db.execute("SELECT payload FROM results ORDER BY score, length, name, rowid LIMIT ? OFFSET ?", (limit, offset))
        return {"results": [json.loads(row[0]) for row in rows],
                "next_offset": offset + limit if total > offset + limit else None}

"""Product file authorization for private homes and declared public material.

This checks each path and symlink hop before I/O. It is not an OS sandbox and
does not protect against concurrent malicious replacement of path components.
"""

from dataclasses import dataclass
import json
import os
import stat


class FileAccessDenied(PermissionError):
    """Denied product access; expose only ``Access denied`` to the client."""


class PublicRootConfigurationError(ValueError):
    """Invalid read roots; expose only ``Invalid public read roots`` at startup."""


def _within(path: str, root: str) -> bool:
    """Compare complete path components, never an account-name prefix."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def parse_public_read_roots(
    value: str,
    *,
    private_roots: tuple[str, ...] = ("/home",),
    protected_roots: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Validate an administrator JSON array of public read-only directories.

    Return canonical absolute directories, deduplicated in input order. Empty
    JSON array means no public data. Reject malformed/non-string entries,
    relative paths, filesystem root, missing paths, files, root symlinks, and
    ancestor/descendant overlap with private or protected roots. Existing
    ancestors of each root must not be symlinks either. Raise
    PublicRootConfigurationError('Invalid public read roots'), without
    including supplied paths. The caller supplies service home/log/state roots
    in addition to /home and system pseudo-filesystems; users cannot set these.
    """
    try:
        if not isinstance(value, str):
            raise ValueError
        entries = json.loads(value)
        if not isinstance(entries, list):
            raise ValueError
        protected = tuple(
            os.path.realpath(os.path.abspath(root))
            for root in (*private_roots, *protected_roots)
        )
        roots = []
        for entry in entries:
            if not isinstance(entry, str) or not os.path.isabs(entry) or "\0" in entry:
                raise ValueError
            # Inspect before normalizing '..': a symlink ancestor is invalid
            # even when a later '..' would erase it from the declaration.
            current = os.sep
            for component in entry.split(os.sep):
                if not component:
                    continue
                current = os.path.join(current, component)
                mode = os.lstat(current).st_mode
                if not stat.S_ISDIR(mode):
                    raise ValueError
                current = os.path.normpath(current)
            canonical = os.path.normpath(current)
            if canonical == os.sep or any(
                _within(canonical, root) or _within(root, canonical)
                for root in protected
            ):
                raise ValueError
            if canonical not in roots:
                roots.append(canonical)
        return tuple(roots)
    except (OSError, TypeError, ValueError):
        raise PublicRootConfigurationError("Invalid public read roots") from None


@dataclass(frozen=True)
class FileAccessPolicy:
    """Authorization for a provisioned home and validated public read roots.

    Both fields must be canonical absolute directories. This policy authorizes
    product file access, not arbitrary executed programs or OS permissions.
    No account lookup, directory creation or ownership changes occur here.
    """

    home: str
    public_read_roots: tuple[str, ...] = ()

    def require(self, path: str, *, write: bool = False) -> str:
        """Authorize an absolute path and return its canonical I/O target.

        Read requires both the lexical entry and resolved target to belong to
        this home or an explicit public root. Write requires both in home;
        public data stays read-only even when filesystem mode allows writing.
        Resolve symlinks before '..' so normalization cannot erase a link hop.
        Each link hop must stay in the authorized roots for that operation;
        re-entering home through an unauthorized intermediate is not allowed.
        Missing trailing components are allowed for subsequent creation; deny
        dangling links, cycles, non-directory intermediates, NUL and relative
        input. Component boundaries matter, not string prefixes. Denial raises
        FileAccessDenied('Access denied') before existence/content is exposed.
        No protection against concurrent malicious link replacement is claimed.
        """
        roots = (self.home,) if write else (self.home, *self.public_read_roots)
        link_count = 0

        def allowed(candidate):
            return any(_within(candidate, root) for root in roots)

        def resolve(requested, *, must_exist=False):
            nonlocal link_count
            if not isinstance(requested, str) or not os.path.isabs(requested) or "\0" in requested:
                raise FileAccessDenied("Access denied")
            components = [part for part in requested.split(os.sep) if part]
            lexical = os.sep + os.sep.join(part for part in components if part != ".")
            if not allowed(lexical):
                raise FileAccessDenied("Access denied")
            if requested.endswith(os.sep):
                components.append(".")
            current = os.sep
            entered = False
            missing = False
            for index, component in enumerate(components):
                if component == "..":
                    # Missing intermediates cannot be walked through, even
                    # when a later parent component could erase their name.
                    if missing:
                        raise FileAccessDenied("Access denied")
                    current = os.path.dirname(current)
                    if entered and not allowed(current):
                        raise FileAccessDenied("Access denied")
                    continue
                candidate = current if component == "." else os.path.join(current, component)
                if allowed(candidate):
                    entered = True
                elif entered or not any(_within(root, candidate) for root in roots):
                    raise FileAccessDenied("Access denied")
                if missing:
                    current = candidate
                    continue
                try:
                    mode = os.lstat(candidate).st_mode
                except FileNotFoundError:
                    if must_exist:
                        raise FileAccessDenied("Access denied") from None
                    missing = True
                    current = candidate
                    continue
                if stat.S_ISLNK(mode):
                    link_count += 1
                    if link_count > 40:
                        raise FileAccessDenied("Access denied")
                    target = os.readlink(candidate)
                    target = target if os.path.isabs(target) else os.path.join(current, target)
                    # Resolve the complete link target strictly. An ordinary
                    # missing suffix is creatable; a dangling link is not.
                    current = resolve(target, must_exist=True)
                    if index < len(components) - 1 and not os.path.isdir(current):
                        raise FileAccessDenied("Access denied")
                else:
                    if index < len(components) - 1 and not stat.S_ISDIR(mode):
                        raise FileAccessDenied("Access denied")
                    current = candidate
            if not allowed(current):
                raise FileAccessDenied("Access denied")
            return current

        try:
            return resolve(path)
        except (OSError, TypeError, ValueError):
            raise FileAccessDenied("Access denied") from None

    def allows(self, path: str, *, write: bool = False) -> bool:
        """Return the same authorization decision as require, without I/O effects.

        Invalid/denied paths return False. Enumeration uses this predicate
        before stat/open and omits denied children, including their names.
        """
        try:
            self.require(path, write=write)
        except FileAccessDenied:
            return False
        return True

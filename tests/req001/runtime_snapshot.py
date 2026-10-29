"""Read-only live Terminal evidence; stream into the lab container with python -."""

import grp
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import pwd
import stat
import sys

import open_terminal


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main():
    root = Path(open_terminal.__file__).parent
    server = pwd.getpwuid(os.getuid())
    state_dir = Path("/home/.open-terminal")
    metadata = {}
    for name in (".", "registry.json", "provision.lock"):
        info = (state_dir / name).lstat()
        expected_mode = 0o700 if name == "." else 0o600
        assert info.st_uid == server.pw_uid and stat.S_IMODE(info.st_mode) == expected_mode
        assert stat.S_ISDIR(info.st_mode) if name == "." else stat.S_ISREG(info.st_mode)
        metadata[name] = {"uid": info.st_uid, "gid": info.st_gid, "mode": oct(stat.S_IMODE(info.st_mode)), "inode": info.st_ino}
    registry = json.loads((state_dir / "registry.json").read_text())
    assert registry["version"] == 1
    accounts = []
    for identity, record in registry["users"].items():
        assert record["state"] == "ready"
        account = pwd.getpwnam(record["username"])
        group = grp.getgrnam(record["username"])
        home = Path(record["home"])
        info = home.lstat()
        assert (account.pw_uid, account.pw_gid, account.pw_dir) == (record["uid"], record["gid"], record["home"])
        assert group.gr_gid == record["gid"] and server.pw_name in group.gr_mem
        assert account.pw_gecos == "ot-" + hashlib.sha256(identity.encode()).hexdigest()
        assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o2770
        assert (info.st_uid, info.st_gid) == (account.pw_uid, account.pw_gid)
        accounts.append({
            "id": identity, **record, "mode": oct(stat.S_IMODE(info.st_mode)),
            "marker_valid": True, "server_member": True,
            "marker_files_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(home.glob("req001-*-marker.txt"))},
        })
    for field in ("username", "home", "uid", "gid"):
        assert len({account[field] for account in accounts}) == len(accounts)
    print(json.dumps({
        "result": "passed", "service_uid": os.getuid(), "python": sys.version,
        "versions": {name: importlib.metadata.version(name) for name in ("open-terminal", "fastapi", "starlette", "httpx")},
        "features": {name: os.getenv(name) for name in ("OPEN_TERMINAL_MULTI_USER", "OPEN_TERMINAL_ENABLE_NOTEBOOKS", "OPEN_TERMINAL_USER_PREFIX")},
        "source_sha256_lf": {str(path): digest(root / path) for path in ("dependencies.py", "main.py", "utils/identity.py", "utils/user_isolation.py")},
        "metadata": metadata, "accounts": accounts,
        "system_uids": {account.pw_name: account.pw_uid for account in pwd.getpwall()},
        "system_gids": {group.gr_name: group.gr_gid for group in grp.getgrall()},
    }, indent=2))


if __name__ == "__main__":
    main()

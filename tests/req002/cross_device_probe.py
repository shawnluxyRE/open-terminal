"""Real EXDEV verification in a disposable container with an inner tmpfs.

Run with --tmpfs /home/req002-cross-device/mounted and no deployment volumes.
The test-image ENTRYPOINT must be overridden to python for this probe.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

from open_terminal.utils.file_access import FileAccessDenied
from open_terminal.utils.fs import UserFS


async def main():
    assert os.environ.get("REQ002_DISPOSABLE_CONTAINER") == "1"
    assert os.geteuid() == 0
    home = Path("/home/req002-cross-device")
    mounted = home / "mounted"
    assert mounted.is_dir(), "The probe requires its dedicated tmpfs mount"
    source_device = home.stat().st_dev
    destination_device = mounted.stat().st_dev
    assert source_device != destination_device, "The probe requires two actual filesystems"
    fs = UserFS("root", str(home))
    other = Path(tempfile.mkdtemp(prefix="req002-cross-device-other-", dir="/home"))
    source = home / "source"
    destination = mounted / "destination"
    target = home / "link-target"
    moved_link = mounted / "standalone-link"
    assert not any(path.exists() for path in (source, destination, target, moved_link))
    try:
        secret = other / "secret.txt"
        secret.write_bytes(b"private-other-home")
        secret.chmod(0o640)
        before = secret.stat()
        source.mkdir()
        (source / "own.txt").write_bytes(b"owned-content")
        (source / "own-link").symlink_to("own.txt")
        (source / "private-file").symlink_to(secret)
        (source / "private-dir").symlink_to(other, target_is_directory=True)

        await fs.move(str(source), str(destination))
        assert not source.exists()
        assert destination.stat().st_dev == destination_device
        assert (destination / "own.txt").read_bytes() == b"owned-content"
        assert (destination / "own-link").is_symlink()
        assert os.readlink(destination / "own-link") == "own.txt"
        assert (destination / "private-file").is_symlink()
        assert os.readlink(destination / "private-file") == str(secret)
        assert (destination / "private-dir").is_symlink()
        assert os.readlink(destination / "private-dir") == str(other)
        try:
            await fs.read(str(destination / "private-file"))
        except FileAccessDenied as exc:
            assert str(exc) == "Access denied"
        else:
            raise AssertionError("The preserved external symlink became readable")
        after = secret.stat()
        assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)
        assert secret.read_bytes() == b"private-other-home"

        await fs.remove(str(destination))
        assert not destination.exists()
        assert secret.read_bytes() == b"private-other-home"

        # Moving a final directory symlink across devices preserves the entry.
        target.mkdir()
        (target / "keep.txt").write_bytes(b"kept-target")
        standalone = home / "standalone-link"
        standalone.symlink_to(target, target_is_directory=True)
        await fs.move(str(standalone), str(moved_link))
        assert not standalone.is_symlink()
        assert moved_link.is_symlink()
        assert os.readlink(moved_link) == str(target)
        await fs.remove(str(moved_link))
        assert not moved_link.is_symlink()
        assert (target / "keep.txt").read_bytes() == b"kept-target"
        print(json.dumps({
            "result": "passed",
            "source_device": source_device,
            "destination_device": destination_device,
            "checks": ["actual_cross_device_directory_move", "private_links_preserved",
                       "private_target_unchanged", "private_link_read_denied",
                       "recursive_remove_does_not_follow_links",
                       "actual_cross_device_directory_link_move", "unlink_preserves_target"],
        }))
    finally:
        # Only exact dedicated test directories are removed. The mount itself
        # remains for Docker to discard with this disposable container.
        for path in (source, destination, target, other):
            if path.is_dir():
                shutil.rmtree(path)
        for path in (home / "standalone-link", moved_link):
            if path.is_symlink():
                path.unlink()


if __name__ == "__main__":
    asyncio.run(main())

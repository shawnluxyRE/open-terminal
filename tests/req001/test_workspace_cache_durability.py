"""A warmed workspace must not trust another user's unconfirmed registry commit.

Only metadata-directory fsync is faulted. Account preparation, persistent records
and filesystem observations use the real public resolver in disposable Docker.
"""

import errno
import grp
import json
import os
from pathlib import Path
import pwd
import stat

import pytest

from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils.user_isolation import WorkspaceProvisioner
from test_workspace_provisioning import assert_ready_account, workspace


def system_state():
    return list(pwd.getpwall()), list(grp.getgrall())


def file_state(path):
    info = path.stat()
    return info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_mtime_ns


@pytest.mark.parametrize("boundary", ["planned", "group_ready", "ready"])
def test_warmed_owner_reconfirms_another_users_visible_but_uncommitted_version(
    workspace, monkeypatch, boundary
):
    """Failed publication cannot refresh a cache; recovery first confirms durability."""
    provisioner, options, identity = workspace
    owner_id, other_id = identity + "-owner", identity + "-other"
    owner = provisioner.resolve(owner_id)
    assert_ready_account(owner, owner_id, options["home_root"])

    home = Path(owner.home)
    marker = home / "preserve-warmed-owner.txt"
    marker.write_text("the warmed owner's workspace remains unchanged", encoding="utf-8")
    os.chown(marker, owner.uid, owner.gid)
    marker.chmod(0o640)
    assert provisioner.resolve(owner_id) == owner
    assert WorkspaceProvisioner(**options).resolve(owner_id) == owner
    owner_files = file_state(home), file_state(marker), marker.read_bytes()
    state_dir = Path(options["state_dir"])
    registry = state_dir / "registry.json"
    owner_binding = json.loads(registry.read_text(encoding="utf-8"))["users"][owner_id]
    metadata = state_dir.stat()
    real_fsync = os.fsync
    fault_active = True
    failed_syncs = []
    confirmed_versions = []

    def sync_metadata_directory(fd):
        info = os.fstat(fd)
        if not (
            stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino) == (metadata.st_dev, metadata.st_ino)
        ):
            return real_fsync(fd)
        saved = json.loads(registry.read_text(encoding="utf-8"))["users"]
        other = saved.get(other_id)
        if other is None or other["state"] != boundary:
            return real_fsync(fd)
        if fault_active:
            failed_syncs.append(other["state"])
            raise OSError(errno.ENOSPC, "injected registry directory fsync failure")
        observed = system_state(), file_state(home), file_state(marker)
        result = real_fsync(fd)
        confirmed_versions.append(observed)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", sync_metadata_directory)
        with pytest.raises(WorkspaceProvisionError) as publication_failure:
            WorkspaceProvisioner(**options).resolve(other_id)
        assert publication_failure.value.code == "workspace_unavailable"
        assert failed_syncs, "The injected fault must reach the selected published state"
        visible = json.loads(registry.read_text(encoding="utf-8"))["users"]
        assert visible[other_id]["state"] == boundary
        assert visible[owner_id] == owner_binding
        other_binding = visible[other_id]
        if boundary == "planned":
            assert other_binding["username"] not in {item.pw_name for item in pwd.getpwall()}
            assert other_binding["username"] not in {item.gr_name for item in grp.getgrall()}
            assert not Path(other_binding["home"]).exists()
        resources_after_failure = system_state()

        # Both an already-used instance and a fresh instance may share a process
        # cache. Neither may publish the visible version as durable after failure.
        for resolver in (provisioner, WorkspaceProvisioner(**options)):
            attempts_before = len(failed_syncs)
            with pytest.raises(WorkspaceProvisionError) as cached_owner_failure:
                resolver.resolve(owner_id)
            assert cached_owner_failure.value.code == "workspace_unavailable"
            assert len(failed_syncs) > attempts_before
            assert confirmed_versions == []
            assert system_state() == resources_after_failure
            assert (file_state(home), file_state(marker), marker.read_bytes()) == owner_files

        fault_active = False
        recovered = WorkspaceProvisioner(**options).resolve(owner_id)
        assert confirmed_versions, "Success requires confirming the newly visible version"
        assert confirmed_versions[0] == (
            resources_after_failure, owner_files[0], owner_files[1]
        ), "Durability must be confirmed before any account or home mutation"
        assert_ready_account(recovered, owner_id, options["home_root"])
        assert recovered == owner
        assert system_state() == resources_after_failure
        assert (file_state(home), file_state(marker), marker.read_bytes()) == owner_files
        persisted = json.loads(registry.read_text(encoding="utf-8"))["users"]
        assert persisted[owner_id] == owner_binding
        assert persisted[other_id] == other_binding

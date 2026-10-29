"""Run full-app runtime checks in separate interpreters in disposable Docker."""

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from test_workspace_provisioning import workspace


def test_two_nonroot_servers_refresh_groups_for_existing_io_threads(workspace):
    probe = Path(__file__).with_name("runtime_probe.py")
    result = subprocess.run([sys.executable, str(probe), "nonroot"], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["result"] == "passed" and evidence["warmed_io"]


def test_single_user_full_app_files_command_and_pty_without_identity(workspace):
    probe = Path(__file__).with_name("runtime_probe.py")
    environment = {
        **os.environ,
        "OPEN_TERMINAL_MULTI_USER": "false",
        "OPEN_TERMINAL_LOG_DIR": "/tmp/req001-single-log-" + uuid.uuid4().hex,
    }
    result = subprocess.run([sys.executable, str(probe), "single"], env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["result"] == "passed" and evidence["lifespan"]

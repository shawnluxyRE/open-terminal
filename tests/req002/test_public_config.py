"""REQ-002 AC-009: real process-startup wiring of public read-root settings.

Each child imports production configuration and env modules afresh. Tests do
not replace the parser or settings loader; their filesystem is disposable.
"""

import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest


_STARTUP_PROBE = """
import json
import sys

try:
    if len(sys.argv) > 1:
        from open_terminal import config
        config.init(sys.argv[1])
    from open_terminal import env
    result = {
        "roots": env.PUBLIC_READ_ROOTS,
        "is_tuple": isinstance(env.PUBLIC_READ_ROOTS, tuple),
        "multi_user": env.MULTI_USER,
    }
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
    sys.exit(3)
print(json.dumps(result))
"""


@pytest.fixture
def settings():
    if os.environ.get("REQ002_DISPOSABLE_CONTAINER") != "1" or os.geteuid() != 0:
        pytest.fail("REQ-002 startup tests require the disposable root Docker test image")
    root = Path(tempfile.mkdtemp(prefix="req002-public-config-", dir="/opt"))
    first = root / "reference"
    second = root / "reference-alternative"
    logs = root / "service-logs"
    for path in (first, second, logs):
        path.mkdir()
    try:
        yield SimpleNamespace(root=root, first=first, second=second, logs=logs)
    finally:
        shutil.rmtree(root)


def start(settings, *, value=None, multi_user=True, config_value=None):
    environment = os.environ.copy()
    environment.pop("OPEN_TERMINAL_PUBLIC_READ_ROOTS", None)
    environment.pop("OPEN_TERMINAL_PUBLIC_READ_ROOTS_FILE", None)
    environment["OPEN_TERMINAL_MULTI_USER"] = "true" if multi_user else "false"
    environment["OPEN_TERMINAL_LOG_DIR"] = str(settings.logs)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if value is not None:
        environment["OPEN_TERMINAL_PUBLIC_READ_ROOTS"] = value
    command = [sys.executable, "-c", _STARTUP_PROBE]
    if config_value is not None:
        configuration = settings.root / "config.toml"
        # TOML literal strings preserve the JSON setting exactly, including
        # its double quotes; test inputs never contain a single quote.
        configuration.write_text(
            "public_read_roots = '" + config_value + "'\n", encoding="utf-8"
        )
        command.append(str(configuration))
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    assert result.stdout.strip(), result.stderr
    return result, json.loads(result.stdout.strip().splitlines()[-1])


def assert_started(result, data, roots, *, multi_user=True):
    assert result.returncode == 0, result.stdout + result.stderr
    assert data == {"roots": roots, "is_tuple": True, "multi_user": multi_user}


def assert_configuration_rejected(result, data):
    assert result.returncode == 3, result.stdout + result.stderr
    assert data == {
        "error": "PublicRootConfigurationError",
        "message": "Invalid public read roots",
    }


@pytest.mark.parametrize("value", [None, "[]"], ids=["absent", "empty-array"])
def test_startup_defaults_to_no_public_roots(settings, value):
    result, data = start(settings, value=value)
    assert_started(result, data, [])


def test_environment_setting_is_validated_and_canonicalized_at_startup(settings):
    first = str(settings.first)
    result, data = start(settings, value=json.dumps([first + "/.", first]))
    assert_started(result, data, [first])


@pytest.mark.parametrize("value", ["not-json", "{}", '["relative"]'])
def test_invalid_multi_user_setting_prevents_startup_without_disclosing_input(settings, value):
    result, data = start(settings, value=value)
    assert_configuration_rejected(result, data)


@pytest.mark.parametrize(
    "kind", ["home", "service-home", "logs", "proc", "sys", "dev", "temporary"]
)
def test_startup_protects_private_service_and_system_roots(settings, kind):
    forbidden = {
        "home": "/home",
        "service-home": pwd.getpwuid(os.geteuid()).pw_dir,
        "logs": str(settings.logs),
        "proc": "/proc",
        "sys": "/sys",
        "dev": "/dev",
        "temporary": tempfile.gettempdir(),
    }[kind]
    result, data = start(settings, value=json.dumps([str(settings.first), forbidden]))
    assert_configuration_rejected(result, data)
    assert forbidden not in result.stdout


def test_config_file_supplies_public_read_roots_when_environment_is_absent(settings):
    result, data = start(settings, config_value=json.dumps([str(settings.first)]))
    assert_started(result, data, [str(settings.first)])


def test_environment_public_roots_override_the_config_file(settings):
    result, data = start(
        settings,
        value=json.dumps([str(settings.second)]),
        config_value=json.dumps([str(settings.first)]),
    )
    assert_started(result, data, [str(settings.second)])


def test_invalid_environment_override_does_not_fall_back_to_valid_config(settings):
    result, data = start(
        settings, value="not-json", config_value=json.dumps([str(settings.first)])
    )
    assert_configuration_rejected(result, data)


def test_single_user_mode_ignores_unused_invalid_public_roots(settings):
    result, data = start(settings, value="not-json", multi_user=False)
    assert_started(result, data, [], multi_user=False)

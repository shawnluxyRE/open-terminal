"""Deterministic app configuration for the disposable REQ-001 test process.

These are synthetic credentials. Never load the deployment .env or mount its
data volumes for these tests. Disable bytecode/cache writes in the run command.
"""

import os


os.environ.update({
    "OPEN_TERMINAL_API_KEY": "req001-test-key-not-a-secret",
    "OPEN_TERMINAL_MULTI_USER": "true",
    "OPEN_TERMINAL_USER_PREFIX": "",
    "OPEN_TERMINAL_ENABLE_NOTEBOOKS": "false",
    "OPEN_TERMINAL_ENABLE_TERMINAL": "true",
    "OPEN_TERMINAL_ENABLE_SYSTEM_PROMPT": "true",
    "OPEN_TERMINAL_INFO": "req001-public-info",
    "OPEN_TERMINAL_LOG_DIR": "/tmp/req001-logs",
})
os.environ.pop("OPEN_TERMINAL_API_KEY_FILE", None)

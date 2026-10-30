"""REQ-002 integration using real non-root service, users and Office helpers."""

import json
from pathlib import Path
import subprocess
import sys


def test_real_service_files_office_and_request_temporary_cleanup():
    probe = Path(__file__).with_name("runtime_probe.py")
    result = subprocess.run([sys.executable, str(probe), "launch"],
                            capture_output=True, text=True, timeout=270)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["result"] == "passed"
    assert evidence["service_uid"] == 1000
    assert evidence["actual_libreoffice"] and evidence["actual_extractor"]
    assert evidence["public_os_writable_product_readonly"]
    print(json.dumps(evidence, sort_keys=True))

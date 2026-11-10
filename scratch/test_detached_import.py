import sys
import tempfile
import shutil
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Create a temporary directory outside the repo
with tempfile.TemporaryDirectory() as tmpdir:
    sol_dir = Path(tmpdir) / "solution"
    shutil.copytree(Path("solution"), sol_dir)
    
    # Run python in tmpdir importing solution.models without any harness on path
    import subprocess
    cmd = [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, r'" + str(sol_dir) + "'); import models; print('Detached import OK')"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Detached import failed:\n{res.stderr}"
    print(res.stdout.strip())
    print("[PASS] Gate 1 detached import test passed!")

import os
import subprocess
import sys
from pathlib import Path

from whisp.instance_lock import acquire_instance_lock

_SRC = Path(__file__).resolve().parents[1] / "src"


def test_second_process_cannot_acquire(tmp_path):
    lock = tmp_path / "whisp.lock"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC)
    helper = """
import os
import sys
from pathlib import Path
from whisp.instance_lock import acquire_instance_lock, InstanceAlreadyRunning

lock = Path(sys.argv[1])
mode = sys.argv[2]
if mode == "hold":
    fd = acquire_instance_lock(lock)
    sys.stdout.write("ready\\n")
    sys.stdout.flush()
    try:
        sys.stdin.read()
    finally:
        os.close(fd)
elif mode == "try":
    try:
        acquire_instance_lock(lock)
    except InstanceAlreadyRunning:
        sys.exit(42)
    sys.exit(0)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", helper, str(lock), "hold"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        result = subprocess.run(
            [sys.executable, "-c", helper, str(lock), "try"],
            env=env,
            check=False,
        )
        assert result.returncode == 42
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=5)


def test_acquire_after_release(tmp_path):
    lock = tmp_path / "whisp.lock"
    fd1 = acquire_instance_lock(lock)
    os.close(fd1)
    fd2 = acquire_instance_lock(lock)
    os.close(fd2)

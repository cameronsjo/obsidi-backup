"""Exercise orphan reaping in the built image; requires a local Docker daemon.

Run: python tests/container_reaping.py <image>
Negative control: python tests/container_reaping.py <image> --without-init
"""

import argparse
import subprocess

PROBE = r"""
import os
from pathlib import Path
import subprocess
import time

for _ in range(12):
    pid = os.fork()
    if pid == 0:
        # Exit without waiting, leaving a real git child for PID 1 to adopt.
        subprocess.Popen(["git", "--version"], stdout=subprocess.DEVNULL)
        os._exit(0)
    os.waitpid(pid, 0)

deadline = time.monotonic() + 5
while True:
    git_processes = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().split()
        except FileNotFoundError:
            continue
        if fields[1] == "(git)":
            git_processes[entry.name] = fields[2]
    # Wait for running children as well as zombies, avoiding an early false pass.
    if not git_processes or time.monotonic() >= deadline:
        break
    time.sleep(0.05)
zombies = [pid for pid, state in git_processes.items() if state == "Z"]
print(f"PID 1: {Path('/proc/1/comm').read_text().strip()}; git zombies: {zombies}", flush=True)
assert not git_processes, f"Git processes remain after bounded reaping poll: {git_processes}"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--without-init", action="store_true")
    args = parser.parse_args()
    command = ["docker", "run", "--rm"]
    if args.without_init:
        command.extend(["--entrypoint", "python", args.image, "-c", PROBE])
    else:
        # Preserve the image's shipped init entrypoint, overriding the app command.
        command.extend([args.image, "python", "-c", PROBE])
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()

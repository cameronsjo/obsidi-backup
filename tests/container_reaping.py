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

time.sleep(1)
zombies = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        fields = (entry / "stat").read_text().split()
    except FileNotFoundError:
        continue
    if fields[1] == "(git)" and fields[2] == "Z":
        zombies.append(entry.name)
print(f"PID 1: {Path('/proc/1/comm').read_text().strip()}; git zombies: {zombies}", flush=True)
assert not zombies, f"Orphaned git processes were not reaped: {zombies}"
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

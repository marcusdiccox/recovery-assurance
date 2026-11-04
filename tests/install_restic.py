"""Install the pinned, checksum-verified restic release on a GitHub runner."""

import bz2
import hashlib
import io
import os
import platform
import urllib.request
import zipfile
from pathlib import Path


def main():
    version = "0.19.1"
    system = "windows" if platform.system() == "Windows" else "linux"
    suffix = "zip" if system == "windows" else "bz2"
    filename = f"restic_{version}_{system}_amd64.{suffix}"
    base = f"https://github.com/restic/restic/releases/download/v{version}/"

    def download(name):
        with urllib.request.urlopen(base + name, timeout=120) as response:
            return response.read()

    checksums = dict(
        line.split(maxsplit=1)[::-1]
        for line in download("SHA256SUMS").decode().splitlines()
        if line.strip()
    )
    expected = checksums.get(filename)
    if expected is None:
        raise RuntimeError("Pinned asset missing from release checksums")
    archive = download(filename)
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError("Restic release checksum mismatch")
    directory = Path(os.environ["RUNNER_TEMP"]) / "restic-tools"
    directory.mkdir(parents=True, exist_ok=True)
    if system == "windows":
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            binary = zipped.read(f"restic_{version}_windows_amd64.exe")
        executable = directory / "restic.exe"
    else:
        binary = bz2.decompress(archive)
        executable = directory / "restic"
    executable.write_bytes(binary)
    executable.chmod(0o755)
    with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as stream:
        stream.write(str(directory) + "\n")
    print(f"Installed checksum-verified restic {version} ({system})")


if __name__ == "__main__":
    main()

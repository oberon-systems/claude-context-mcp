"""Fetch a llama.cpp release and unpack it beside this package.

The release binaries carry one ggml-cpu library per instruction set and pick
at load time, which is what makes them run where a wheel compiled for one CPU
does not. Which build to take depends on the driver, so this asks nvidia-smi
rather than making the reader match version numbers by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
# "CUDA Version" on older nvidia-smi headers, "CUDA UMD Version" on newer.
DRIVER_CUDA = re.compile(r"CUDA(?: UMD)? Version:\s*(\d+)\.(\d+)")
ASSET = re.compile(r"^llama-.+-bin-win-(?P<variant>.+)-x64\.zip$")
SERVER_EXE = "llama-server.exe"
# How far back to look for a release whose binaries are actually attached.
RECENT_RELEASES = 10


def default_dest() -> Path:
    """Where the server lands: llama-server/ beside the ctxworker package."""
    return Path(__file__).resolve().parent.parent / "llama-server"


def fetch(source: str) -> dict | list:
    """Read one URL from the GitHub API."""
    request = urllib.request.Request(
        source, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def pick_release(tag: str, variant: str) -> dict:
    """Take the newest release that has the build, not merely the newest.

    llama.cpp tags a release before its binaries finish uploading, so the
    latest one is regularly a tag with no Windows zip attached yet.
    """
    if tag:
        found = fetch(f"{RELEASES}/tags/{tag}")
        return found if isinstance(found, dict) else {}
    recent = fetch(f"{RELEASES}?per_page={RECENT_RELEASES}")
    for data in recent if isinstance(recent, list) else []:
        offered = variants([asset["name"] for asset in data.get("assets", [])])
        if offered and (not variant or variant in offered):
            return data
    wanted = variant or "Windows x64"
    raise SystemExit(
        f"none of the last {RECENT_RELEASES} llama.cpp releases has a "
        f"{wanted} build uploaded yet. Try again shortly, or name an older "
        "release with --tag."
    )


def driver_cuda() -> tuple[int, int] | None:
    """Ask nvidia-smi which CUDA version the driver serves."""
    try:
        done = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = DRIVER_CUDA.search(done.stdout or "")
    if not found:
        return None
    return int(found.group(1)), int(found.group(2))


def variants(names: list[str]) -> list[str]:
    """Every Windows x64 build this release publishes."""
    return sorted(
        {found.group("variant") for name in names if (found := ASSET.match(name))}
    )


def choose_variant(names: list[str], cuda: tuple[int, int] | None) -> str:
    """Take the newest CUDA build the driver can run, or the CPU one."""
    available = variants(names)
    if cuda is not None:
        offered = []
        for name in available:
            if not name.startswith("cuda-"):
                continue
            parts = name[len("cuda-") :].split(".")
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                offered.append(((int(parts[0]), int(parts[1])), name))
        runnable = [pair for pair in offered if pair[0] <= cuda]
        if runnable:
            return max(runnable)[1]
    return "cpu" if "cpu" in available else (available[0] if available else "cpu")


def pick_assets(assets: list[dict], variant: str) -> list[dict]:
    """Take the build itself, plus the CUDA runtime it links against."""
    wanted = [f"-bin-win-{variant}-x64.zip", f"cudart-llama-bin-win-{variant}-x64.zip"]
    chosen = [
        asset
        for asset in assets
        if asset["name"].endswith(wanted[0]) or asset["name"] == wanted[1]
    ]
    if not chosen:
        offered = ", ".join(variants([asset["name"] for asset in assets])) or "none"
        raise SystemExit(
            f"no Windows x64 build named {variant} in this release.\n"
            f"It offers: {offered}. Pass one of those as --variant."
        )
    return chosen


def unpack(asset: dict, dest: Path) -> None:
    """Download one archive and extract it over the destination."""
    partial = dest / (asset["name"] + ".part")
    print(f"llama-server: downloading {asset['name']}", file=sys.stderr)
    with urllib.request.urlopen(asset["browser_download_url"]) as response:
        with partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    try:
        with zipfile.ZipFile(partial) as archive:
            archive.extractall(dest)
    except zipfile.BadZipFile as error:
        raise SystemExit(
            f"{asset['name']} is not a zip file, kept as {partial}"
        ) from error
    partial.unlink()


def install(dest: Path, tag: str = "", variant: str = "") -> Path:
    """Put a llama.cpp build in dest, and return the server in it."""
    data = pick_release(tag, variant)
    assets = data.get("assets", [])
    names = [asset["name"] for asset in assets]
    cuda = driver_cuda()
    chosen_variant = variant or choose_variant(names, cuda)
    print(
        f"release {data.get('tag_name', '?')}, driver CUDA "
        f"{'.'.join(map(str, cuda)) if cuda else 'not reported'}, "
        f"taking {chosen_variant}"
    )
    dest.mkdir(parents=True, exist_ok=True)
    for asset in pick_assets(assets, chosen_variant):
        unpack(asset, dest)
    server = dest / SERVER_EXE
    if not server.is_file():
        raise SystemExit(f"unpacked, but there is no {SERVER_EXE} in {dest}")
    return server


def main() -> None:
    """Fetch the release, unpack it, and say how to start it."""
    parser = argparse.ArgumentParser(
        prog="ctxworker.getserver",
        description=(
            "Download a llama.cpp Windows build into worker/llama-server. "
            "On Linux use the ghcr.io/ggml-org/llama.cpp docker image instead - "
            "the releases carry no Linux CUDA archive."
        ),
    )
    parser.add_argument("--tag", default="", help="a release, default the latest")
    parser.add_argument(
        "--variant", default="", help="cuda-13.3, cuda-12.4, cpu, ... default: detected"
    )
    parser.add_argument("--dest", default="", help="where to unpack it")
    parser.add_argument(
        "--dry-run", action="store_true", help="only say what would be taken"
    )
    args = parser.parse_args()

    dest = Path(args.dest) if args.dest else default_dest()
    if args.dry_run:
        data = pick_release(args.tag, args.variant)
        assets = data.get("assets", [])
        names = [asset["name"] for asset in assets]
        variant = args.variant or choose_variant(names, driver_cuda())
        for asset in pick_assets(assets, variant):
            print(f"would take {asset['name']} -> {dest}")
        return

    server = install(dest, args.tag, args.variant)
    print(f"llama-server: ready at {server}")
    print(
        "Start it with:\n"
        f"    {server} -m <model.gguf> -c 8192 -ngl 99 "
        "--host 127.0.0.1 --port 8080 --parallel 1"
    )


if __name__ == "__main__":
    main()

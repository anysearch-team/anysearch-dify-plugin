"""Build and verify the reproducible release package from a clean commit."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_package import check  # noqa: E402
from scripts.check_release_metadata import check as check_release_metadata  # noqa: E402
from scripts.check_sdk import check as check_sdk  # noqa: E402


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", type=Path, required=True, help="official Dify plugin CLI executable")
    parser.add_argument("--output", type=Path, default=Path(".codex/release/anysearch-0.1.0.difypkg"))
    args = parser.parse_args()

    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True,
    )
    if status:
        raise SystemExit("release_check requires a clean git worktree")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    cli = args.cli.resolve()
    if not cli.is_file():
        raise SystemExit(f"Dify CLI not found: {cli}")
    output = (ROOT / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    unpacked = output.parent / "unpacked"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    run([str(cli), "plugin", "package", ".", "-o", str(output)])
    report = check(output, unpacked)
    if not report["source_commit_reproduces_package"] or report["worktree_dirty"]:
        raise SystemExit("package is not reproducible from a clean source commit")
    check_release_metadata(output)
    sdk = check_sdk(unpacked)
    result = {"commit": commit, "package": report, "sdk": sdk, "cli": str(cli)}
    report_path = output.parent / "release-check.json"
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

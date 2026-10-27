"""Check the release metadata embedded in a Dify plugin package."""

import argparse
from pathlib import Path
import re
import zipfile


def check(package: Path) -> None:
    errors = []
    with zipfile.ZipFile(package) as archive:
        manifest = archive.read("manifest.yaml").decode("utf-8")
        if not re.search(r"^\s+minimum_dify_version:\s*1\.17\.1\s*$", manifest, re.MULTILINE):
            errors.append("minimum_dify_version must record the verified CE version")
        source_prefix = "https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/"
        for name in ("README.md", "readme/README_zh_Hans.md"):
            content = archive.read(name).decode("utf-8")
            links = re.findall(r"\]\((https://github\.com/anysearch-team/anysearch-dify-plugin/blob/[^)]+)\)", content)
            if not links:
                errors.append(f"No source links in {name}")
            elif not all(link.startswith(source_prefix) for link in links):
                errors.append(f"Source link does not point to the public branch in {name}")
    if errors:
        raise AssertionError("; ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    check(args.package)
    print("Release metadata checks passed")

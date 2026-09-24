#!/usr/bin/env python3
"""Stage explicitly selected DEV UI files in a production checkout.

The DEV checkout is treated as untrusted input. Data, workflow and collector files
are deliberately outside the promotion surface.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path, PurePosixPath


DEV_BADGE = re.compile(
    r'\s*<script\b[^>]*\bsrc\s*=\s*["\']assets/dev-environment\.js(?:\?[^"\']*)?["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)
SCRIPTS = re.compile(r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def parse_paths(raw: str) -> list[PurePosixPath]:
    paths: list[PurePosixPath] = []
    for line in re.split(r"[\r\n,]+", raw):
        value = line.strip()
        if not value:
            continue
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "public"
            or path.suffix.lower() not in {".html", ".css", ".js"}
            or path.name == "dev-environment.js"
            or any(part.startswith(".") for part in path.parts)
            or "data" in path.parts
        ):
            raise ValueError(f"Promotion path is not an allowed UI file: {value}")
        if path not in paths:
            paths.append(path)
    if not paths:
        raise ValueError("Select at least one explicit public/*.html, .css or .js path")
    return paths


def local_scripts(html: str) -> set[str]:
    return {
        src.split("?", 1)[0]
        for src in SCRIPTS.findall(html)
        if not src.startswith(("http://", "https://", "//"))
    }


def stage(dev_root: Path, prod_root: Path, raw_paths: str) -> list[str]:
    paths = parse_paths(raw_paths)
    messages: list[str] = []
    for path in paths:
        source = dev_root.joinpath(*path.parts)
        target = prod_root.joinpath(*path.parts)
        if (
            not source.is_file()
            or not source.resolve().is_relative_to(dev_root.resolve())
            or not target.resolve().is_relative_to(prod_root.resolve())
        ):
            raise ValueError(f"Missing or symlinked file: {path}")
        content = source.read_bytes()
        if path.suffix.lower() == ".html":
            html = content.decode("utf-8")
            html = DEV_BADGE.sub("", html)
            if "dev-environment.js" in html or "[DEV]" in html:
                raise ValueError(f"DEV-only marker remains in {path}")
            if target.exists():
                current = target.read_text(encoding="utf-8")
                missing = local_scripts(current) - local_scripts(html)
                if missing:
                    raise ValueError(
                        f"{path} would drop production scripts: {', '.join(sorted(missing))}. "
                        "Bring them into DEV and retest before promoting."
                    )
            content = html.encode("utf-8")
            for script in local_scripts(html):
                local_path = (target.parent / script.split("#", 1)[0]).resolve()
                public_root = (prod_root / "public").resolve()
                if not local_path.is_relative_to(public_root):
                    raise ValueError(f"Script reference escapes public/: {script}")
                candidate = dev_root / local_path.relative_to(prod_root.resolve())
                if not local_path.is_file() and not candidate.is_file():
                    raise ValueError(f"Missing script for {path}: {script}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".html":
            target.write_bytes(content)
        else:
            shutil.copyfile(source, target)
        messages.append(str(path))
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--production", type=Path, required=True)
    parser.add_argument("--paths", required=True)
    args = parser.parse_args()
    for path in stage(args.dev, args.production, args.paths):
        print(f"Staged: {path}")


if __name__ == "__main__":
    main()

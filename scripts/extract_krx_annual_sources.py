#!/usr/bin/env python3
"""Extract the uploaded KRX annual source archive for public file-by-file access.

The original archive remains at ``source-data/krx-annual/Policy.zip``. This
script safely expands its contents into ``public/data/krx-annual/source-files``
and generates a manifest plus a browser index page.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "source-data" / "krx-annual" / "Policy.zip"
DEFAULT_OUTPUT = ROOT / "public" / "data" / "krx-annual" / "source-files"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract KRX annual source files and build a public index"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def normalized_member_parts(name: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/")).strip("/")
    if not normalized:
        return ()

    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe ZIP member path: {name!r}")
    if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
        return ()
    return parts


def common_top_directory(members: list[tuple[zipfile.ZipInfo, tuple[str, ...]]]) -> str | None:
    file_parts = [parts for info, parts in members if not info.is_dir() and parts]
    if not file_parts or any(len(parts) < 2 for parts in file_parts):
        return None
    first_parts = {parts[0] for parts in file_parts}
    return next(iter(first_parts)) if len(first_parts) == 1 else None


def classify_file(relative_path: Path) -> str:
    text = relative_path.as_posix().replace(" ", "")
    rules = (
        (("사전할당", "할당대상업체"), "사전할당"),
        (("추가할당",), "추가할당"),
        (("할당취소", "취소량"), "할당취소"),
        (("인증배출", "배출량"), "인증배출량"),
        (("이월",), "이월량"),
        (("차입",), "차입량"),
        (("상쇄", "외부사업"), "상쇄·외부사업"),
        (("총수량", "배출허용총량"), "총수량"),
    )
    for keywords, label in rules:
        if any(keyword in text for keyword in keywords):
            return label
    parent = relative_path.parent.as_posix()
    return parent if parent not in {"", "."} else "기타"


def infer_year(relative_path: Path) -> int | None:
    match = re.search(r"(?<!\d)(20(?:1[5-9]|2[0-5]))(?!\d)", relative_path.as_posix())
    return int(match.group(1)) if match else None


def file_size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def build_index(records: list[dict[str, Any]], archive_sha256: str) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["category"]].append(record)

    category_order = [
        "총수량",
        "사전할당",
        "추가할당",
        "할당취소",
        "인증배출량",
        "이월량",
        "차입량",
        "상쇄·외부사업",
        "기타",
    ]
    ordered_categories = sorted(
        grouped,
        key=lambda value: (
            category_order.index(value) if value in category_order else len(category_order),
            natural_key(value),
        ),
    )

    sections: list[str] = []
    for category in ordered_categories:
        items = sorted(
            grouped[category],
            key=lambda item: (
                item["year"] if item["year"] is not None else 9999,
                natural_key(item["path"]),
            ),
        )
        links = []
        for item in items:
            href = quote(item["path"], safe="/")
            year = f'<span class="year">{item["year"]}년</span>' if item["year"] else ""
            links.append(
                "<li>"
                f'<a href="{href}">{html.escape(item["name"])}</a>'
                f'<div>{year}<span>{html.escape(item["extension"].upper() or "FILE")}</span>'
                f'<span>{html.escape(file_size_label(item["size"]))}</span></div>'
                "</li>"
            )
        sections.append(
            f'<section><h2>{html.escape(category)} <small>{len(items)}개</small></h2>'
            f'<ul>{"".join(links)}</ul></section>'
        )

    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="KRX 업체현황 연간 원자료 개별파일 목록">
  <title>KRX 업체현황 연간 원자료</title>
  <style>
    :root{{--ink:#16231d;--muted:#68756f;--line:#dfe8e3;--green:#0c7c59;--bg:#f4f7f5}}
    *{{box-sizing:border-box}}
    body{{margin:0;color:var(--ink);background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}}
    main{{width:min(1080px,calc(100% - 32px));margin:34px auto 60px}}
    header{{padding:26px 28px;border:1px solid var(--line);border-radius:16px;background:#fff;box-shadow:0 10px 30px rgba(22,35,29,.05)}}
    h1{{margin:0 0 8px;font-size:28px}} header p{{margin:5px 0;color:var(--muted);font-size:14px;line-height:1.6}}
    .links{{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}} .links a{{padding:9px 12px;border:1px solid #bcd1c6;border-radius:8px;color:var(--green);background:#fff;text-decoration:none;font-size:13px;font-weight:700}}
    section{{margin-top:18px;padding:20px 22px;border:1px solid var(--line);border-radius:14px;background:#fff}}
    h2{{margin:0 0 13px;font-size:18px}} h2 small{{color:var(--muted);font-size:12px}}
    ul{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:0;padding:0;list-style:none}}
    li{{min-width:0;padding:12px 13px;border:1px solid #e4ebe7;border-radius:10px;background:#fbfcfb}}
    li a{{display:block;overflow:hidden;color:#155b43;font-size:13px;font-weight:750;text-decoration:none;text-overflow:ellipsis;white-space:nowrap}}
    li a:hover{{text-decoration:underline}} li div{{display:flex;flex-wrap:wrap;gap:7px;margin-top:7px;color:var(--muted);font-size:11px}}
    li div span{{padding:2px 6px;border-radius:999px;background:#eef4f1}} li div .year{{color:#725b18;background:#fff5cf}}
    footer{{margin-top:18px;color:var(--muted);font-size:11px;line-height:1.6;word-break:break-all}}
    @media(max-width:700px){{main{{width:min(100% - 20px,1080px);margin-top:18px}}header{{padding:20px}}h1{{font-size:23px}}ul{{grid-template-columns:1fr}}section{{padding:17px}}}}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>KRX 업체현황(연간) 원자료</h1>
      <p>업로드된 Policy.zip을 개별 파일 단위로 정리한 공개 폴더입니다. 파일명과 폴더 구조는 원본을 유지했습니다.</p>
      <p>총 {len(records)}개 파일 · 생성 {html.escape(generated)}</p>
      <div class="links">
        <a href="../../../../krx-annual.html">업체현황 대시보드</a>
        <a href="https://raw.githubusercontent.com/ebrain725/ebrain725.github.io/main/source-data/krx-annual/Policy.zip">원본 ZIP 다운로드</a>
        <a href="https://github.com/ebrain725/ebrain725.github.io/tree/main/public/data/krx-annual/source-files">GitHub 폴더</a>
        <a href="manifest.json">파일 목록 JSON</a>
      </div>
    </header>
    {''.join(sections)}
    <footer>원본 ZIP SHA-256: {archive_sha256}</footer>
  </main>
</body>
</html>
"""


def extract_archive(input_path: Path, output_path: Path) -> list[dict[str, Any]]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Source archive not found: {input_path}")

    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(input_path) as archive:
        members = [
            (info, normalized_member_parts(info.filename))
            for info in archive.infolist()
        ]
        top_directory = common_top_directory(members)

        for info, parts in members:
            if info.is_dir() or not parts:
                continue
            relative_parts = parts[1:] if top_directory and parts[0] == top_directory else parts
            if not relative_parts:
                continue

            relative_path = Path(*relative_parts)
            target = output_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            target.write_bytes(data)

            extension = relative_path.suffix.removeprefix(".").lower()
            records.append(
                {
                    "name": relative_path.name,
                    "path": relative_path.as_posix(),
                    "originalArchivePath": unicodedata.normalize("NFC", info.filename),
                    "category": classify_file(relative_path),
                    "year": infer_year(relative_path),
                    "extension": extension,
                    "size": len(data),
                    "sha256": sha256_bytes(data),
                }
            )

    records.sort(key=lambda item: natural_key(item["path"]))
    return records


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    archive_bytes = input_path.read_bytes()
    archive_sha256 = sha256_bytes(archive_bytes)
    records = extract_archive(input_path, output_path)

    manifest = {
        "schemaVersion": "1.0.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(input_path.relative_to(ROOT)),
            "filename": input_path.name,
            "size": len(archive_bytes),
            "sha256": archive_sha256,
        },
        "summary": {
            "fileCount": len(records),
            "categoryCounts": dict(sorted(Counter(item["category"] for item in records).items())),
            "extensionCounts": dict(sorted(Counter(item["extension"] or "none" for item in records).items())),
        },
        "files": records,
    }
    (output_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_path / "index.html").write_text(
        build_index(records, archive_sha256),
        encoding="utf-8",
    )

    print(f"Extracted {len(records)} files to {output_path}")
    print(f"Archive SHA-256: {archive_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

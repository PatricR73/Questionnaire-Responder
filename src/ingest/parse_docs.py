"""Parse .md/.txt/.docx/.pdf evidence documents into heading-anchored text blocks.

Each block carries the heading path it falls under (e.g. "Access Control > Password
Policy") and a location reference (line number or page number) so every chunk built
from these blocks can be traced back to an exact spot in the source file.
"""

import re
from dataclasses import dataclass
from pathlib import Path

MAX_HEADING_LEVEL = 6


@dataclass
class ParsedBlock:
    heading_path: str
    loc_ref: str
    text: str


def parse_document(path: Path) -> list[ParsedBlock]:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return _parse_markdown_or_text(path)
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix == ".pdf":
        return _parse_pdf(path)
    raise ValueError(f"Unsupported evidence document type: {suffix}")


_MD_HEADING_RE = re.compile(rf"^(#{{1,{MAX_HEADING_LEVEL}}})\s+(.*)$")


def _parse_markdown_or_text(path: Path) -> list[ParsedBlock]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)

    for line_num, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        match = _MD_HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, title))
            continue
        if not line.strip():
            continue
        heading_path = " > ".join(title for _, title in heading_stack)
        blocks.append(ParsedBlock(heading_path=heading_path, loc_ref=f"line {line_num}", text=line.strip()))

    return blocks


def _parse_docx(path: Path) -> list[ParsedBlock]:
    import docx

    document = docx.Document(str(path))
    blocks: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []

    for para_num, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if not text:
            continue

        style_name = (paragraph.style.name if paragraph.style else "") or ""
        heading_match = re.match(r"^Heading (\d)$", style_name)
        if heading_match:
            level = int(heading_match.group(1))
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, text))
            continue

        heading_path = " > ".join(title for _, title in heading_stack)
        blocks.append(ParsedBlock(heading_path=heading_path, loc_ref=f"paragraph {para_num}", text=text))

    return blocks


_HEADING_LINE_RE = re.compile(r"^[A-Z0-9][A-Za-z0-9 ,'&/-]{2,79}$")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.endswith((".", ",", ";", ":")):
        return False
    if len(stripped) > 80:
        return False
    return stripped.isupper() or stripped.istitle()


def _parse_pdf(path: Path) -> list[ParsedBlock]:
    import pdfplumber

    blocks: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []  # single-level heading stack (PDF layout heuristic only)

    with pdfplumber.open(str(path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if _looks_like_heading(stripped):
                    heading_stack = [(1, stripped)]
                    continue
                heading_path = " > ".join(title for _, title in heading_stack)
                blocks.append(ParsedBlock(heading_path=heading_path, loc_ref=f"page {page_num}", text=stripped))

    return blocks

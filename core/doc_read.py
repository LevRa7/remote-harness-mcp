"""core/doc_read.py - stdlib text extraction for Office documents (.docx/.xlsx/.pptx/.odt).

Zero third-party dependencies: Office Open XML and OpenDocument files are ZIP
containers holding UTF-8 XML. This module extracts readable text with zip-bomb
protection so ``read_media`` can inspect documents directly without LibreOffice,
python-docx, or openpyxl.
"""

from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile

OFFICE_EXTENSIONS = frozenset((".docx", ".xlsx", ".pptx", ".odt"))
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
_SLIDE_NUM_RE = re.compile(r"slide(\d+)\.xml$", re.IGNORECASE)
_SHEET_NUM_RE = re.compile(r"sheet(\d+)\.xml$", re.IGNORECASE)


def is_office_doc(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in OFFICE_EXTENSIONS


def _safe_read_xml(zf: zipfile.ZipFile, member: str, budget: List[int]) -> Optional[ET.Element]:
    try:
        info = zf.getinfo(member)
    except KeyError:
        return None
    if info.file_size > budget[0]:
        return None
    raw = zf.read(member)
    budget[0] -= len(raw)
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _extract_docx(zf: zipfile.ZipFile, budget: List[int]) -> Tuple[str, Dict[str, Any]]:
    root = _safe_read_xml(zf, "word/document.xml", budget)
    if root is None:
        return "", {"format": "docx", "paragraphs": 0}
    paragraphs: List[str] = []
    for elem in root.iter():
        if _local(elem.tag) == "p":
            parts: List[str] = []
            for sub in elem.iter():
                lname = _local(sub.tag)
                if lname == "t" and sub.text:
                    parts.append(sub.text)
                elif lname == "tab":
                    parts.append("\t")
                elif lname in ("br", "cr"):
                    parts.append("\n")
            line = "".join(parts).strip()
            if line:
                paragraphs.append(line)
    return "\n".join(paragraphs), {"format": "docx", "paragraphs": len(paragraphs)}


def _extract_xlsx(zf: zipfile.ZipFile, budget: List[int]) -> Tuple[str, Dict[str, Any]]:
    shared_strings: List[str] = []
    ss_root = _safe_read_xml(zf, "xl/sharedStrings.xml", budget)
    if ss_root is not None:
        for si in ss_root:
            if _local(si.tag) == "si":
                chunks = [sub.text for sub in si.iter() if _local(sub.tag) == "t" and sub.text]
                shared_strings.append("".join(chunks))

    sheet_names: List[str] = []
    wb_root = _safe_read_xml(zf, "xl/workbook.xml", budget)
    if wb_root is not None:
        for elem in wb_root.iter():
            if _local(elem.tag) == "sheet":
                name = elem.attrib.get("name")
                if name:
                    sheet_names.append(name)

    sheet_members = [m for m in zf.namelist() if _SHEET_NUM_RE.search(m)]
    sheet_members.sort(key=lambda m: int(_SHEET_NUM_RE.search(m).group(1)))

    sections: List[str] = []
    total_rows = 0
    for idx, member in enumerate(sheet_members):
        s_root = _safe_read_xml(zf, member, budget)
        if s_root is None:
            continue
        title = sheet_names[idx] if idx < len(sheet_names) else f"Sheet {idx + 1}"
        rows_out: List[str] = []
        for elem in s_root.iter():
            if _local(elem.tag) != "row":
                continue
            cells: List[str] = []
            for cell in elem:
                if _local(cell.tag) != "c":
                    continue
                ctype = cell.attrib.get("t", "")
                val = ""
                if ctype == "inlineStr":
                    val = "".join(t.text for t in cell.iter() if _local(t.tag) == "t" and t.text)
                else:
                    for sub in cell:
                        if _local(sub.tag) == "v" and sub.text is not None:
                            if ctype == "s":
                                try:
                                    val = shared_strings[int(sub.text)]
                                except (ValueError, IndexError):
                                    val = sub.text
                            else:
                                val = sub.text
                            break
                cells.append(val)
            if any(c.strip() for c in cells):
                rows_out.append("\t".join(cells))
        total_rows += len(rows_out)
        sections.append(f"=== {title} ===\n" + "\n".join(rows_out))

    return "\n\n".join(sections), {"format": "xlsx", "sheets": len(sheet_members), "rows": total_rows}


def _extract_pptx(zf: zipfile.ZipFile, budget: List[int]) -> Tuple[str, Dict[str, Any]]:
    slide_members = [m for m in zf.namelist() if _SLIDE_NUM_RE.search(m)]
    slide_members.sort(key=lambda m: int(_SLIDE_NUM_RE.search(m).group(1)))

    slides_out: List[str] = []
    for idx, member in enumerate(slide_members, 1):
        s_root = _safe_read_xml(zf, member, budget)
        if s_root is None:
            continue
        lines: List[str] = []
        for elem in s_root.iter():
            if _local(elem.tag) == "p":
                parts = [sub.text for sub in elem.iter() if _local(sub.tag) == "t" and sub.text]
                line = "".join(parts).strip()
                if line:
                    lines.append(line)
        slides_out.append(f"=== Slide {idx} ===\n" + "\n".join(lines))

    return "\n\n".join(slides_out), {"format": "pptx", "slides": len(slide_members)}


def _extract_odt(zf: zipfile.ZipFile, budget: List[int]) -> Tuple[str, Dict[str, Any]]:
    root = _safe_read_xml(zf, "content.xml", budget)
    if root is None:
        return "", {"format": "odt", "paragraphs": 0}
    lines: List[str] = []
    for elem in root.iter():
        if _local(elem.tag) in ("p", "h"):
            text = "".join(elem.itertext()).strip()
            if text:
                lines.append(text)
    return "\n".join(lines), {"format": "odt", "paragraphs": len(lines)}


def extract_office_text(
    data: bytes,
    filename: str,
) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """Extract plain text from raw bytes of ``.docx``/``.xlsx``/``.pptx``/``.odt``.

    Returns ``(text, meta, error)``. Never raises.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in OFFICE_EXTENSIONS:
        return None, {}, "unsupported office extension: %s" % ext
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                return None, {}, "office document exceeds %d MB uncompressed limit" % (MAX_UNCOMPRESSED_BYTES // (1024 * 1024))
            budget = [MAX_UNCOMPRESSED_BYTES]
            if ext == ".docx":
                text, meta = _extract_docx(zf, budget)
            elif ext == ".xlsx":
                text, meta = _extract_xlsx(zf, budget)
            elif ext == ".pptx":
                text, meta = _extract_pptx(zf, budget)
            else:
                text, meta = _extract_odt(zf, budget)
            return text, meta, None
    except zipfile.BadZipFile:
        return None, {}, "invalid or corrupted office archive"
    except Exception as exc:
        return None, {}, "office extraction failed: %s" % exc

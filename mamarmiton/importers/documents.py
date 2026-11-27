"""Plain text out of office documents (Word .docx, LibreOffice .odt), standard library only.

Shared by the desktop app and the server, so it must not import anything heavy.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

MAX_XML_BYTES = 20 * 1024 * 1024  # a recipe document is far smaller; refuse zip bombs

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


class DocumentError(ValueError):
    """The file is not a readable document."""


def _read_member(data: bytes, name: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo(name)
            if info.file_size > MAX_XML_BYTES:
                raise DocumentError("Document trop volumineux.")
            return zf.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocumentError("Ce document est illisible.") from exc


def _parse(xml: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml:
        raise DocumentError("Ce document est illisible.")  # no entity expansion tricks
    try:
        return ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentError("Ce document est illisible.") from exc


def _tidy(lines: list[str]) -> str:
    text = "\n".join(line.rstrip() for line in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def docx_text(data: bytes) -> str:
    """The paragraphs of a .docx file, one per line (tables included, cell by cell)."""
    root = _parse(_read_member(data, "word/document.xml"))
    lines = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return _tidy(lines)


def odt_text(data: bytes) -> str:
    """The paragraphs and headings of an .odt file, one per line."""
    root = _parse(_read_member(data, "content.xml"))
    lines = []
    for node in root.iter():
        if node.tag in (f"{_TEXT}p", f"{_TEXT}h"):
            lines.append("".join(_odt_inline(node)))
    return _tidy(lines)


def _odt_inline(node: ElementTree.Element) -> list[str]:
    parts = [node.text or ""]
    for child in node:
        if child.tag == f"{_TEXT}s":
            parts.append(" " * int(child.get(f"{_TEXT}c", "1") or 1))
        elif child.tag == f"{_TEXT}tab":
            parts.append("\t")
        elif child.tag == f"{_TEXT}line-break":
            parts.append("\n")
        elif child.tag not in (f"{_TEXT}p", f"{_TEXT}h"):  # nested paragraphs are visited on their own
            parts.extend(_odt_inline(child))
        parts.append(child.tail or "")
    return parts


def plain_text(data: bytes) -> str:
    """Decode a text file: UTF-8 (with or without BOM), else Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding).replace("\r\n", "\n").strip()
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1").strip()

"""Unit tests for core/doc_read.py and Office integration in core/media.py."""

from __future__ import annotations

import io
import zipfile

from core import doc_read, media


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_docx_xlsx_pptx_odt():
    docx_raw = _zip_bytes({
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Hello Resume</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Python</w:t><w:tab/><w:t>Linux</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
    })
    text, meta, err = doc_read.extract_office_text(docx_raw, "cv.docx")
    assert err is None
    assert meta["format"] == "docx"
    assert "Hello Resume" in text
    assert "Python\tLinux" in text

    xlsx_raw = _zip_bytes({
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="Budget" sheetId="1"/></sheets></workbook>'
        ),
        "xl/sharedStrings.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Item</t></si><si><t>Cost</t></si><si><t>VPS</t></si></sst>'
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>500</v></c></row>'
            '</sheetData></worksheet>'
        ),
    })
    text, meta, err = doc_read.extract_office_text(xlsx_raw, "plan.xlsx")
    assert err is None
    assert meta["format"] == "xlsx" and meta["sheets"] == 1
    assert "=== Budget ===" in text
    assert "VPS\t500" in text

    pptx_raw = _zip_bytes({
        "ppt/slides/slide1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody>'
            '<a:p><a:r><a:t>Architecture Deck</a:t></a:r></a:p>'
            '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
        )
    })
    text, meta, err = doc_read.extract_office_text(pptx_raw, "deck.pptx")
    assert err is None
    assert meta["format"] == "pptx" and meta["slides"] == 1
    assert "Architecture Deck" in text

    odt_raw = _zip_bytes({
        "content.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            '<office:body><office:text>'
            '<text:h>Title ODT</text:h><text:p>Body paragraph</text:p>'
            '</office:text></office:body></office:document-content>'
        )
    })
    text, meta, err = doc_read.extract_office_text(odt_raw, "doc.odt")
    assert err is None
    assert meta["format"] == "odt"
    assert "Title ODT" in text and "Body paragraph" in text


def test_read_media_reads_docx_end_to_end(tmp_path):
    docx_file = tmp_path / "sample.docx"
    docx_file.write_bytes(_zip_bytes({
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Verified via read_media</w:t></w:r></w:p></w:body></w:document>'
        )
    }))
    res = media.read_media(str(docx_file))
    assert res["ok"] is True
    assert res["meta"]["office"]["format"] == "docx"
    assert "Verified via read_media" in res["content"][0]["text"]

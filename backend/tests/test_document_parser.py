import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from backend.services.document_parser import DocumentParser


@pytest.fixture
def parser():
    return DocumentParser(chunk_size=100, overlap=20)


class TestChunkText:
    def test_short_text_no_split(self, parser):
        text = "Hello world"
        result = parser.chunk_text(text)
        assert result == ["Hello world"]

    def test_exact_chunk_size(self, parser):
        text = "a" * 100
        result = parser.chunk_text(text)
        assert len(result) == 1
        assert result[0] == text

    def test_long_text_splits(self, parser):
        text = " ".join([f"word{i}" for i in range(200)])
        result = parser.chunk_text(text)
        assert len(result) > 1

    def test_overlap_contains_shared_content(self, parser):
        words = [f"w{i}" for i in range(50)]
        text = " ".join(words)
        chunks = parser.chunk_text(text)

        if len(chunks) >= 2:
            shared = set(chunks[0].split()) & set(chunks[1].split())
            assert len(shared) > 0, "Chunks should share overlapping words"

    def test_no_empty_chunks(self, parser):
        text = "a" * 300
        result = parser.chunk_text(text)
        for chunk in result:
            assert chunk.strip(), "Chunk should not be empty or whitespace-only"

    def test_breaks_at_word_boundary(self, parser):
        words = [f"word{i}" for i in range(100)]
        text = " ".join(words)
        chunks = parser.chunk_text(text)

        for chunk in chunks:
            assert not chunk.startswith(" "), "Chunk should not start with space"
            assert not chunk.endswith(" "), "Chunk should not end with space"


class TestTxtParsing:
    def test_parse_txt_single_paragraph(self, parser):
        content = "Single paragraph of text."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = parser._parse_txt(tmp_path)
            assert len(result) == 1
            assert "Single paragraph" in result[0]
        finally:
            os.unlink(tmp_path)

    def test_parse_txt_multiple_paragraphs(self, parser):
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = parser._parse_txt(tmp_path)
            assert len(result) == 3
        finally:
            os.unlink(tmp_path)

    def test_parse_txt_empty_lines_filtered(self, parser):
        content = "Para one.\n\n   \n\nPara two."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = parser._parse_txt(tmp_path)
            assert len(result) == 2
        finally:
            os.unlink(tmp_path)


class TestMdParsing:
    def test_parse_md_sections(self, parser):
        content = "# Title\n\nIntro text.\n\n## Section A\nContent A.\n\n## Section B\nContent B."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = parser._parse_md(tmp_path)
            assert any("Section A" in r for r in result)
            assert any("Content B" in r for r in result)
        finally:
            os.unlink(tmp_path)

    def test_parse_md_no_sections(self, parser):
        content = "Just some plain text with no headings."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = parser._parse_md(tmp_path)
            assert len(result) == 1
        finally:
            os.unlink(tmp_path)


class TestParseDispatch:
    @pytest.mark.asyncio
    async def test_unsupported_format(self, parser):
        with pytest.raises(ValueError, match="Unsupported file type"):
            await parser.parse("/tmp/file.rtf", "rtf")

    @pytest.mark.asyncio
    async def test_parse_txt_end_to_end(self, parser):
        content = "Line one.\n\nLine two."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            result = await parser.parse(tmp_path, "txt")
            assert len(result) >= 1
            assert any("Line one" in r for r in result)
        finally:
            os.unlink(tmp_path)


class TestPdfImportError:
    def test_pdf_raises_without_library(self):
        with patch("backend.services.document_parser.PdfReader", None):
            p = DocumentParser()
            with pytest.raises(ImportError, match="pypdf not installed"):
                p._parse_pdf("/tmp/fake.pdf")


class TestDocxImportError:
    def test_docx_raises_without_library(self):
        with patch("backend.services.document_parser.DocxDocument", None):
            p = DocumentParser()
            with pytest.raises(ImportError, match="python-docx not installed"):
                p._parse_docx("/tmp/fake.docx")


class TestXlsxImportError:
    def test_xlsx_raises_without_library(self):
        with patch("backend.services.document_parser.openpyxl", None):
            p = DocumentParser()
            with pytest.raises(ImportError, match="openpyxl not installed"):
                p._parse_xlsx("/tmp/fake.xlsx")


class TestOdtImportError:
    def test_odt_raises_without_library(self):
        with patch("backend.services.document_parser.load", None):
            p = DocumentParser()
            with pytest.raises(ImportError, match="odfpy not installed"):
                p._parse_odt("/tmp/fake.odt")

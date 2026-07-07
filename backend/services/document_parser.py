import os
import re
from typing import List, Union

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    from odf.opendocument import load
    from odf.text import P as TextP
except ImportError:
    load = None


class DocumentParser:
    """Multi-format document parser with text chunking."""

    def __init__(self, chunk_size: int | None = None, overlap: int | None = None):
        self.chunk_size = chunk_size or 1500
        self.overlap = overlap or 150

    async def parse(self, file_path: str, file_type: str) -> List[str]:
        """Parse a document and return list of text chunks.

        Args:
            file_path: Path to the document file
            file_type: File extension (pdf/docx/xlsx/txt/md/odt)

        Returns:
            List of text chunks suitable for embedding
        """
        parser_map = {
            "pdf": self._parse_pdf,
            "docx": self._parse_docx,
            "xlsx": self._parse_xlsx,
            "txt": self._parse_txt,
            "md": self._parse_md,
            "odt": self._parse_odt,
        }

        parser = parser_map.get(file_type.lower())
        if not parser:
            raise ValueError(f"Unsupported file type: {file_type}")

        raw_chunks = parser(file_path)
        all_chunks: List[str] = []
        for text in raw_chunks:
            all_chunks.extend(self.chunk_text(text))
        return all_chunks

    def _parse_pdf(self, path: str) -> List[str]:
        """Extract pages from PDF, split by page."""
        if PdfReader is None:
            raise ImportError("pypdf not installed")

        reader = PdfReader(path)
        chunks: List[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                chunks.append(text)
        return chunks

    def _parse_docx(self, path: str) -> List[str]:
        """Extract paragraphs from DOCX with heading context."""
        if DocxDocument is None:
            raise ImportError("python-docx not installed")

        doc = DocxDocument(path)
        chunks: List[str] = []
        current_heading = ""
        current_text = ""

        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                if current_text.strip():
                    chunks.append(f"{current_heading}\n{current_text.strip()}")
                current_heading = para.text.strip()
                current_text = ""
            else:
                current_text += para.text.strip() + "\n"

        if current_text.strip():
            chunks.append(f"{current_heading}\n{current_text.strip()}")
        return chunks

    def _parse_xlsx(self, path: str) -> List[str]:
        """Parse XLSX — each row as 'col_header: value' string."""
        if openpyxl is None:
            raise ImportError("openpyxl not installed")

        wb = openpyxl.load_workbook(path, read_only=True)
        sheet = wb.active
        chunks: List[str] = []

        headers: List[str] = []
        for row in sheet.iter_rows(values_only=True):
            row_strs = [str(v) if v is not None else "" for v in row]
            if not headers:
                headers = row_strs
            else:
                parts: List[str] = []
                for i, val in enumerate(row_strs):
                    col_name = headers[i] if i < len(headers) else f"Column_{i}"
                    parts.append(f"{col_name}: {val}")
                chunk = " | ".join(parts)
                if chunk.strip():
                    chunks.append(chunk)

        wb.close()
        return chunks

    def _parse_txt(self, path: str) -> List[str]:
        """Read TXT file and split by double newline."""
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = [c.strip() for c in re.split(r"\n\s*\n", text)]
        return [c for c in chunks if c]

    def _parse_md(self, path: str) -> List[str]:
        """Read MD file and split by heading (## sections)."""
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        sections = re.split(r"\n## ", text)
        chunks: List[str] = []
        for i, section in enumerate(sections):
            if i == 0 and not section.startswith("#"):
                chunks.append(section.strip())
            else:
                prefix = "## " if i > 0 else ""
                chunks.append((prefix + section).strip())
        return [c for c in chunks if c]

    def _parse_odt(self, path: str) -> List[str]:
        """Parse ODT file text paragraphs."""
        if load is None:
            raise ImportError("odfpy not installed")

        doc = load(path)
        current_text = ""

        for elem in doc.body.childNodes:
            if hasattr(elem, "QName") and elem.QName == "p":
                text = elem.getStringValue() or ""
                if text.strip():
                    current_text += text.strip() + "\n"

        chunks: List[str] = []
        if current_text.strip():
            chunks.append(current_text.strip())
        return chunks

    def chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks of ~chunk_size characters."""
        if len(text) <= self.chunk_size:
            return [text]

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]

            # Try to break at word boundary instead of mid-word
            if end < len(text) and text[end] != " ":
                break_point = chunk.rfind(" ", self.overlap, self.chunk_size)
                if break_point > self.chunk_size * 0.5:
                    chunk = chunk[:break_point]

            chunks.append(chunk.strip())
            start = end - self.overlap

        return [c for c in chunks if c]

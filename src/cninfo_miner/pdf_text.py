"""PDF download and text extraction for text-based CNINFO attachments."""

import hashlib
import io

import fitz
import httpx


async def download_pdf_text(url: str, client: httpx.AsyncClient) -> tuple[str, str]:
    response = await client.get(url)
    response.raise_for_status()
    content = response.content
    document = fitz.open(stream=content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    if not text.strip():
        raise ValueError("PDF 没有可提取文本；首版不启用 OCR")
    return hashlib.sha256(content).hexdigest(), text

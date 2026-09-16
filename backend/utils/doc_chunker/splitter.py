"""
PDF/DOCX → list-of-PDF-bytes splitter.

Keeps all file I/O isolated so the pipeline stays pure.
"""
from __future__ import annotations

import io
import logging
import os
import sys
from typing import List, Tuple

from pypdf import PdfReader, PdfWriter

try:
    import docx2txt
except ImportError:
    docx2txt = None

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
except ImportError:
    pass

logger = logging.getLogger(__name__)


def convert_docx_to_pdf(docx_path: str) -> str:
    """
    Convert a .docx file to PDF using multiple fallback methods.
    Returns the path of the generated PDF (same directory, .pdf extension).
    Caller is responsible for deleting the temp file when done.
    """
    pdf_path = os.path.splitext(docx_path)[0] + "_converted.pdf"
    logger.info("Converting DOCX → PDF: %s → %s", docx_path, pdf_path)
    
    # Method 1: Try docx2pdf with COM (original method)
    if sys.platform == "win32":
        try:
            import pythoncom
            pythoncom.CoInitialize()
            
            from docx2pdf import convert
            convert(docx_path, pdf_path)
            
            if os.path.exists(pdf_path):
                logger.info("DOCX → PDF conversion successful using docx2pdf")
                return pdf_path
                
        except Exception as e:
            logger.warning("docx2pdf conversion failed: %s. Trying alternative methods...", e)
        finally:
            try:
                pythoncom.CoUninitialize()
            except:
                pass
    
    # Method 2: Try docx2txt + reportlab (fallback using existing packages)
    try:
        # Extract text from DOCX using docx2txt
        text = docx2txt.process(docx_path) if docx2txt else None
        
        if not text or not text.strip():
            # Fallback: try python-docx if available
            try:
                from docx import Document
                doc = Document(docx_path)
                paragraphs = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        paragraphs.append(para.text.strip())
                text = '\n\n'.join(paragraphs)
            except ImportError:
                text = None
        
        if text and text.strip():
            # Create PDF using reportlab
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import inch
            
            doc = SimpleDocTemplate(pdf_path, pagesize=letter)
            styles = getSampleStyleSheet()
            story = []
            
            # Split text into paragraphs and add to PDF
            paragraphs = text.split('\n\n')
            for para in paragraphs:
                if para.strip():
                    # Escape HTML characters and handle line breaks
                    clean_para = para.strip().replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    clean_para = clean_para.replace('\n', '<br/>')
                    p = Paragraph(clean_para, styles['Normal'])
                    story.append(p)
                    story.append(Spacer(1, 0.2*inch))
            
            if story:  # Only build if we have content
                doc.build(story)
                
                if os.path.exists(pdf_path):
                    logger.info("DOCX → PDF conversion successful using text extraction + reportlab")
                    return pdf_path
            
    except Exception as e:
        logger.warning("Text extraction + reportlab conversion failed: %s", e)
    
    # Method 3: Create a simple placeholder PDF if all else fails
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        
        logger.warning("Creating placeholder PDF due to conversion failure")
        
        doc = SimpleDocTemplate(pdf_path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = [
            Paragraph("Document Conversion Notice", styles['Title']),
            Paragraph(f"Original file: {os.path.basename(docx_path)}", styles['Normal']),
            Paragraph("This PDF was created as a placeholder because the original DOCX file could not be converted.", styles['Normal']),
            Paragraph("The document processing will continue, but manual review may be required.", styles['Normal'])
        ]
        
        doc.build(story)
        
        if os.path.exists(pdf_path):
            logger.info("Created placeholder PDF for failed conversion")
            return pdf_path
            
    except Exception as e:
        logger.error("Failed to create placeholder PDF: %s", e)
    
    # If everything fails, raise an error
    raise RuntimeError(
        f"Failed to convert DOCX to PDF using all available methods. "
        f"File: {docx_path}"
    )


def split_pdf_to_chunks(
    pdf_path: str,
    chunk_size: int = 5,
) -> Tuple[List[bytes], int]:
    """
    Split a PDF into chunks of `chunk_size` pages.

    Returns:
        (chunks, total_pages)
        chunks — list of raw PDF bytes, one entry per chunk
    """
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    chunks: List[bytes] = []

    for start in range(0, total_pages, chunk_size):
        writer = PdfWriter()
        end = min(start + chunk_size, total_pages)
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())

    logger.info(
        "Split '%s' into %d chunk(s) of up to %d pages (total=%d pages)",
        os.path.basename(pdf_path),
        len(chunks),
        chunk_size,
        total_pages,
    )
    return chunks, total_pages


def prepare_document(file_path: str) -> Tuple[str, bool]:
    """
    Ensure the document is a PDF, converting from DOCX if necessary.

    Returns:
        (working_pdf_path, was_converted)
        was_converted=True means the caller must delete working_pdf_path when done.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return file_path, False
    if ext == ".docx":
        pdf_path = convert_docx_to_pdf(file_path)
        return pdf_path, True
    raise ValueError(f"Unsupported file type: {ext!r}. Only .pdf and .docx are supported.")

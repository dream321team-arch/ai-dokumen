from src.document_extractor import extract_document_pages, SUPPORTED_EXTENSIONS

# Alias for backward compatibility
def extract_pages(pdf_path: str):
    return extract_document_pages(pdf_path)

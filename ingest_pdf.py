import PyPDF2
import json
import requests

# 1. Extraer texto del PDF
pdf_path = "ai_act.pdf"
document_id = "pdf_doc_1"
chunks = []

with open(pdf_path, "rb") as f:
    reader = PyPDF2.PdfReader(f)
    for page_num, page in enumerate(reader.pages):
        text = page.extract_text()
        # Dividir por párrafos o por longitud
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if para.strip():  # Ignorar vacíos
                chunks.append({
                    "text": para.strip(),
                    "metadata": {"page": page_num + 1}
                })

# 2. Ingestar en el servidor
payload = {
    "document_id": document_id,
    "chunks": chunks,
}

response = requests.post(
    "http://localhost:8001/ingest",
    json=payload,
)
print(response.json())
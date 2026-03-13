
import time
from google.genai import errors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether, Table
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import A4

# ... (rest of your imports stay the same)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from google import genai
import fitz
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

# Gemini client
client = genai.Client(api_key="AQ.Ab8RN6IaBUahT0Hg3nF5eT7TUVfsS3-4S1BbufFlsnMAIk8_RA")

# Tesseract path (Windows)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

app = FastAPI()

progress_status = "Waiting..."

templates = Jinja2Templates(directory="templates")


# ----------- PDF GENERATION FUNCTION -----------

def add_page_number(canvas, doc):
    page_num = canvas.getPageNumber()
    text = f"Page {page_num}"
    canvas.drawRightString(550, 20, text)

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_RIGHT

def create_translated_pdf(text):
    file_path = "translated_output.pdf"
    styles = getSampleStyleSheet()

    # --- STYLES ---
    legal_body = ParagraphStyle('LegalBody', parent=styles['Normal'], fontName='Times-Roman', fontSize=12, leading=18, alignment=TA_JUSTIFY, spaceAfter=12, firstLineIndent=0)
    legal_heading_center = ParagraphStyle('LegalHeadingCenter', parent=styles['Normal'], fontName='Times-Bold', fontSize=13, leading=18, alignment=TA_CENTER, spaceAfter=14)
    legal_right = ParagraphStyle('LegalRight', parent=styles['Normal'], fontName='Times-Roman', fontSize=12, leading=18, alignment=TA_RIGHT, spaceAfter=6)
    legal_left = ParagraphStyle('LegalLeft', parent=styles['Normal'], fontName='Times-Roman', fontSize=12, leading=18, alignment=TA_LEFT, spaceAfter=6)

    story = []
    table_data = [] 
    
    # Define our special keywords right here so Python sees them immediately
    court_keywords = ["before the", "in the court", "in the hon'ble", "office of the"]
    
    lines = text.split("\n")
    
    for line in lines:
        clean_line = line.strip().replace("**", "").replace("##", "")
        if clean_line == "":
            continue
            
        # --- RULE 1: The Two-Column Signature Block ---
        if "|" in clean_line:
            parts = clean_line.split("|", 1)
            left_text = parts[0].strip()
            right_text = parts[1].strip()
            
            left_p = Paragraph(left_text, legal_left) if left_text else Paragraph("", legal_left)
            right_p = Paragraph(right_text, legal_right) if right_text else Paragraph("", legal_right)
            
            table_data.append([left_p, right_p])
            continue
            
        if table_data:
            sig_table = Table(table_data, colWidths=[215, 215]) 
            story.append(Spacer(1, 15))
            story.append(KeepTogether(sig_table))
            table_data = []

        lower_line = clean_line.lower()

        # --- RULE 2: "Versus" ---
        if lower_line in ["versus", "v/s", "vs", "vs."]:
            story.append(Paragraph(f"<b>{clean_line.upper()}</b>", legal_heading_center))
            
        # --- RULE 2.5: Court Names / Authority Headings ---
        elif any(lower_line.startswith(k) for k in court_keywords):
            story.append(Paragraph(f"<b>{clean_line.upper()}</b>", legal_heading_center))
            
        # --- RULE 3: Fix Short Headings ---
        elif clean_line.isupper() or (len(clean_line) < 40 and not clean_line[0].isdigit() and not clean_line.endswith(".")):
            formatted_heading = clean_line.replace(":", "").upper()
            story.append(Paragraph(f"<b>{formatted_heading}</b>", legal_heading_center))
            
        # --- RULE 4: Normal Body Paragraphs ---
        else:
            story.append(Paragraph(clean_line, legal_body))

    if table_data:
        sig_table = Table(table_data, colWidths=[215, 215])
        story.append(Spacer(1, 15))
        story.append(KeepTogether(sig_table))

    # --- PAGE SETUP ---
    doc = SimpleDocTemplate(
        file_path, pagesize=A4, leftMargin=108, rightMargin=54, topMargin=72, bottomMargin=72  
    )

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return file_path

# ----------- OCR FUNCTION -----------

def ocr_pdf(file_path):

    images = convert_from_path(file_path)

    text = ""

    for img in images:
        page_text = pytesseract.image_to_string(img, lang="hin+eng")
        text += page_text + "\n"

    return text


# ----------- ROOT ENDPOINT -----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ----------- MAIN PDF TRANSLATION ENDPOINT -----------

@app.post("/upload-pdf/")
async def upload_pdf(file: UploadFile = File(...)):
    global progress_status

    contents = await file.read()
    with open("uploaded.pdf", "wb") as f:
        f.write(contents)

    doc = fitz.open("uploaded.pdf")

    # 1. EXTRACT ALL TEXT FIRST (Fixes the missing information issue)
    full_document_text = ""
    for page in doc:
        page_text = page.get_text()
        if page_text.strip() == "":
            page_text = ocr_pdf("uploaded.pdf")
        full_document_text += page_text + "\n"

    # 2. UPDATED PROMPT (Fixes junk characters)
    prompt = """
You are an expert legal translator practicing in Indian Courts.
Translate the following Hindi legal document into formal legal English.

CRITICAL RULES:
1. Translate paragraph by paragraph.
2. DO NOT use any Markdown formatting (no asterisks **, no hashes ##).
3. Headings (like 'Special Request') must be on their own line.
4. For the signature block at the bottom, separate the left side (Place/Date) and right side (Names/Signatures) using a single pipe character '|'. 
   Example format:
   Jaipur | Respondent
   Date: | Address: Temple Shri Govind Dev Ji
   Jaipur | Through Manager
5. Translate the ENTIRE document. Do not summarize.

Document:
"""
    progress_status = "Translating entire document..."
    print(progress_status)

    # 3. SEND TO GEMINI IN ONE GO
    progress_status = "Sending to Gemini..."
    print(progress_status)

    # --- NEW RETRY LOGIC ---
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt + full_document_text
        )
    except errors.ClientError as e:
        if "429" in str(e):
            progress_status = "Speed limit reached. Pausing for 45 seconds before continuing..."
            print(progress_status)
            time.sleep(45) # Pauses the server safely for 45 seconds
            
            # Tries one more time after waiting
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt + full_document_text
            )
        else:
            # If it's a different error, raise it normally
            raise e
    # -----------------------

    translation = response.text if response.text else ""

    progress_status = "Generating PDF..."
    pdf_file = create_translated_pdf(translation)
    progress_status = "Completed"

    return FileResponse(
        path=pdf_file,
        media_type="application/pdf",
        filename="translated_document.pdf"
    )
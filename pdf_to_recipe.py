import re
from pathlib import Path
import pdfplumber
import io 
import os
from PIL import Image
if False:
    import llama_cpp
    import pdf2image
    import pytesseract

# Optional OCR
try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
USE_LLM = True

# --------------------------------------------------
# PDF TEXT EXTRACTION
# --------------------------------------------------

def _extract_text_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text.strip()


def _extract_text_ocr(path):
    if not OCR_AVAILABLE:
        return ""
    pages = convert_from_path(path)
    text = ""
    for page in pages:
        text += pytesseract.image_to_string(page, lang="fra+eng") + "\n"
    return text.strip()


def extract_text(path):
    text = _extract_text_pdf(path)
    if len(text) < 200 and OCR_AVAILABLE:
        text = _extract_text_ocr(path)
    return text


# --------------------------------------------------
# CLEANING
# --------------------------------------------------

def clean_text(text):
    text = text.lower()
    text = text.replace("\r", "\n")
    text = re.sub(r"[•●▪–—]", "-", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# --------------------------------------------------
# TITLE
# --------------------------------------------------

def extract_title(raw_text):
    for line in raw_text.splitlines():
        if len(line.strip()) > 3:
            return line.strip().title()
    return "Recette importée"


# --------------------------------------------------
# TIMES (minutes)
# --------------------------------------------------

def extract_times(text):
    temps_prep = 0
    temps_cuisson = 0

    patterns = [
        (r"(préparation|prep).*?(\d+)\s*(min|h)", "prep"),
        (r"(cuisson|cook).*?(\d+)\s*(min|h)", "cuisson"),
    ]

    for pattern, ttype in patterns:
        match = re.search(pattern, text)
        if match:
            value = int(match.group(2))
            if match.group(3).startswith("h"):
                value *= 60
            if ttype == "prep":
                temps_prep = value
            else:
                temps_cuisson = value

    return temps_prep, temps_cuisson


# --------------------------------------------------
# INGREDIENTS
# --------------------------------------------------

ING_HEADERS = ["ingrédients", "ingredients"]

def extract_ingredients(text):
    lines = text.splitlines()
    ingredients = []
    capture = False

    for line in lines:
        line = line.strip()

        if any(h in line for h in ING_HEADERS):
            capture = True
            continue

        if capture:
            if line == "" or len(line) < 2:
                break

            # split "500g farine" or "farine : 500g"
            if ":" in line:
                nom, qty = line.split(":", 1)
            else:
                qty = "".join(c for c in line if c.isdigit() or c.isalpha())
                nom = line.replace(qty, "").strip("- ")

            ingredients.append({
                "nom": nom.strip().capitalize(),
                "quantité": qty.strip()
            })

    return ingredients


# --------------------------------------------------
# STEPS
# --------------------------------------------------

STEP_HEADERS = ["préparation", "preparation", "instructions", "méthode"]

def extract_steps(text):
    lines = text.splitlines()
    steps = []
    capture = False

    for line in lines:
        line = line.strip()

        if any(h in line for h in STEP_HEADERS):
            capture = True
            continue

        if capture:
            if not line:
                continue
            line = re.sub(r"^\d+[\).\s-]+", "", line)
            steps.append(line.capitalize())

    return steps


# --------------------------------------------------
# TYPE DE PLAT / CUISSON
# --------------------------------------------------

def detect_type_plat(text):
    if any(k in text for k in ["dessert", "gâteau", "sucré"]):
        return "Dessert"
    if "pain" in text:
        return "Pain"
    if "entrée" in text:
        return "Entrée"
    return "Plat"


def detect_type_cuisson(text):
    if "four" in text:
        return "Four"
    if "poêle" in text or "poele" in text:
        return "Poêle"
    return "Sans cuisson"


# --------------------------------------------------
# PERSONS
# --------------------------------------------------

def extract_persons(text):
    match = re.search(r"(\d+)\s*(personnes|pers|parts)", text)
    if match:
        return int(match.group(1))
    return 2


# --------------------------------------------------
# MAIN FUNCTION
# --------------------------------------------------

def pdf_to_recipe(pdf_path):
    if not Path(pdf_path).exists():
        raise FileNotFoundError(pdf_path)
    raw = extract_text(pdf_path)
    print (raw)
    clean = clean_text(raw)

    temps_prep, temps_cuisson = extract_times(clean)

    recipe = {
        "nom": extract_title(raw),
        "temps_cuisson": temps_cuisson,
        "temps": temps_prep,
        "pour": extract_persons(clean),
        "type_plat": detect_type_plat(clean),
        "type_cuisson": detect_type_cuisson(clean),
        "ingredients": extract_ingredients(clean),
        "etapes": extract_steps(clean)
        }
    if USE_LLM:
        try:

            print("Extracting text from PDF... with llm")
            from llm_parser import parse_recipe_with_llm
            llm_recipe = parse_recipe_with_llm(raw)
            try:
                print("LLM extraction successful.")
                print(llm_recipe)
                recipe = llm_recipe
                print("new recipe:", recipe)
            except Exception as e:
                print("LLM extraction error:", e)
        except Exception as e:
            print("LLM fallback failed:", e)


    return recipe
def extract_images_from_pdf(pdf_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    saved = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            for img_index, img in enumerate(page.images):
                try:
                    image = None

                    # CASE 1: image has direct stream
                    if "stream" in img and img["stream"]:
                        img_bytes = img["stream"].get_data()
                        image = Image.open(io.BytesIO(img_bytes))

                    # CASE 2: image has object_id
                    elif "object_id" in img:
                        obj = pdf.streams.get(img["object_id"])
                        if obj:
                            img_bytes = obj.get_data()
                            image = Image.open(io.BytesIO(img_bytes))

                    if image is None:
                        continue

                    # Skip icons / logos
                    if image.width < 200 or image.height < 200:
                        continue

                    filename = f"image_{page_index+1}_{img_index+1}.png"
                    image.save(os.path.join(output_dir, filename))
                    saved += 1

                except Exception as e:
                    print("Image extraction error:", e)

    return saved



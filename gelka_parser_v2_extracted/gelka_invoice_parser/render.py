from __future__ import annotations
from PIL import Image
import pdfplumber

def render_pdf_pages(pdf_path: str, dpi: int = 200) -> list[Image.Image]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            im = p.to_image(resolution=dpi).original
            pages.append(im)
    return pages

def crop_region(img: Image.Image, region: tuple[float,float,float,float]) -> Image.Image:
    w, h = img.size
    x0,y0,x1,y1 = region
    box = (int(x0*w), int(y0*h), int(x1*w), int(y1*h))
    return img.crop(box)

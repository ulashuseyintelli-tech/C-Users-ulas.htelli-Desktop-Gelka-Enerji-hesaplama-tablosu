import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import pypdfium2 as pdfium
from PIL import Image
import io
import base64
import json

# PDF'i bul
pdf_path = None
for f in os.listdir('Fatura örnekler'):
    if 'BBE' in f and 'CK' in f:
        pdf_path = os.path.join('Fatura örnekler', f)
        break

print(f"PDF: {pdf_path}")

pdf = pdfium.PdfDocument(pdf_path)

# İlk 2 sayfayı birleştir
images = []
for i in range(min(len(pdf), 2)):
    page = pdf[i]
    bitmap = page.render(scale=2.0)
    pil_image = bitmap.to_pil()
    images.append(pil_image)
    page.close()

total_height = sum(img.height for img in images)
max_width = max(img.width for img in images)
combined = Image.new('RGB', (max_width, total_height), (255, 255, 255))
y = 0
for img in images:
    combined.paste(img, (0, y))
    y += img.height

buffer = io.BytesIO()
combined.save(buffer, format='PNG')
image_bytes = buffer.getvalue()
base64_image = base64.b64encode(image_bytes).decode('utf-8')

print(f"Image: {combined.size}, {len(image_bytes)} bytes")

from app.extraction_prompt import EXTRACTION_PROMPT
from app.extractor import EXTRACTION_SCHEMA, clear_extraction_cache

# Cache temizle
clear_extraction_cache()

# GPT-5.2 ile test
print("\n=== GPT-5.2 + Structured Outputs ===")
from openai import OpenAI
client = OpenAI()

response = client.chat.completions.create(
    model='gpt-5.2',
    messages=[
        {'role': 'system', 'content': EXTRACTION_PROMPT},
        {
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}', 'detail': 'high'}}
            ]
        }
    ],
    response_format=EXTRACTION_SCHEMA,
    max_completion_tokens=3000
)

print(f"Response object: {response}")
print(f"Finish reason: {response.choices[0].finish_reason}")
content = response.choices[0].message.content
print(f"Raw response length: {len(content) if content else 0} chars")
print(f"Content: {content}")

result = json.loads(content)
print(f"Consumption: {result.get('consumption_kwh', {}).get('value')} kWh")
print(f"Unit Price: {result.get('current_active_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
print(f"Total: {result.get('invoice_total_with_vat_tl', {}).get('value')} TL")
print(f"Line items: {len(result.get('line_items', []))}")
for item in result.get('line_items', [])[:5]:
    print(f"  - {item.get('label')}: {item.get('qty')} kWh x {item.get('unit_price')} = {item.get('amount_tl')} TL")

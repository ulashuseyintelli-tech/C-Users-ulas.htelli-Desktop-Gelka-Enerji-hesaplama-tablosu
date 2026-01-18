import os
import time
from dotenv import load_dotenv
load_dotenv()

from app.extractor import extract_invoice_data, clear_extraction_cache, _optimize_image_size, encode_image
from app.extraction_prompt import EXTRACTION_PROMPT

# Clear cache
clear_extraction_cache()

# Read test image
with open('test_page1.png', 'rb') as f:
    img_bytes = f.read()

print(f'Original image: {len(img_bytes)/1024:.1f} KB')

# Time optimization
t0 = time.time()
optimized = _optimize_image_size(img_bytes, max_size=1200)
t1 = time.time()
print(f'Optimization: {t1-t0:.2f}s, result: {len(optimized)/1024:.1f} KB')

# Time encoding
t2 = time.time()
b64 = encode_image(optimized)
t3 = time.time()
print(f'Base64 encoding: {t3-t2:.2f}s')

# Time API call directly
from openai import OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

messages = [
    {"role": "system", "content": EXTRACTION_PROMPT},
    {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "auto"}}
    ]}
]

t4 = time.time()
response = client.chat.completions.create(
    model='gpt-4o',
    messages=messages,
    response_format={"type": "json_object"},
    max_tokens=800
)
t5 = time.time()
print(f'API call: {t5-t4:.2f}s')

print(f'Total: {t5-t0:.2f}s')

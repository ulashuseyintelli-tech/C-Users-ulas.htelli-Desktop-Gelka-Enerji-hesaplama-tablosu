import os
import time
import base64
import json
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

with open('test_page1.png', 'rb') as f:
    img_bytes = f.read()

img = Image.open(BytesIO(img_bytes))
if img.width > 1200:
    ratio = 1200 / img.width
    new_size = (1200, int(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)

buf = BytesIO()
if img.mode == 'RGBA':
    img = img.convert('RGB')
img.save(buf, format='JPEG', quality=85)
img_bytes = buf.getvalue()
print(f'Image: {len(img_bytes)/1024:.1f} KB')

b64 = base64.b64encode(img_bytes).decode()

prompt = '''Turkish electricity invoice extraction.

CRITICAL: Get consumption_kwh from the BILLING TABLE where there is kWh × unit_price = amount.
NOT from yearly/monthly comparison graphs or averages.

Return JSON:
{
  "vendor": "enerjisa|ck_bogazici|ekvator|yelden|unknown",
  "consumption_kwh": number,
  "current_active_unit_price_tl_per_kwh": number,
  "invoice_total_with_vat_tl": number
}

Number format: 1.234,56 = 1234.56'''

print('Calling gpt-4o...')
start = time.time()
response = client.chat.completions.create(
    model='gpt-4o',
    messages=[
        {'role': 'user', 'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'auto'}}
        ]}
    ],
    response_format={'type': 'json_object'},
    max_tokens=300
)
elapsed = time.time()-start
print(f'Response in {elapsed:.2f}s')
result = json.loads(response.choices[0].message.content)
print(json.dumps(result, indent=2))

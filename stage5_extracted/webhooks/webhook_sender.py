import httpx

def send_webhook(url, payload): httpx.post(url, json=payload)

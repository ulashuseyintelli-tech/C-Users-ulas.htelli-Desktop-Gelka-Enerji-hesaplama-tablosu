"""Cache temizle ve yeniden test et"""
import requests

API_URL = "http://localhost:8000"

# Cache'i temizlemek için extraction_cache'i sıfırla
# Bu endpoint yoksa, backend'i yeniden başlatmak gerekir

print("Cache temizlemek için backend'i yeniden başlatın.")
print("Veya EXTRACTION_CACHE_ENABLED=false yapın.")

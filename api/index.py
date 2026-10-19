import sys
import os

# Asosiy papkani tizim yo'liga qo'shamiz
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

# Vercel uchun ASGI handler
handler = app
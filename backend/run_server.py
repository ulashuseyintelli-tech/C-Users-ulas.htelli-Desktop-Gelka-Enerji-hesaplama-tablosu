"""
PyInstaller entry point for Gelka Enerji backend.
Standalone .exe olarak paketlendiğinde bu dosya çalışır.
"""
import sys
import os
import uvicorn

# PyInstaller bundled modda çalışma dizinini ayarla
if getattr(sys, 'frozen', False):
    # .exe olarak çalışıyorsa
    base_dir = os.path.dirname(sys.executable)
    os.chdir(base_dir)
    # .env dosyasını yükle
    os.environ.setdefault('DATABASE_URL', f'sqlite:///{os.path.join(base_dir, "gelka_enerji.db")}')

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run(
        'app.main:app',
        host=args.host,
        port=args.port,
        log_level='info',
    )

if __name__ == '__main__':
    main()

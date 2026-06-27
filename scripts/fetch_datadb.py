"""
Download the Delver Lens APK and extract data.db from it.
Writes progress lines to stdout so the caller can tail a log file.
"""
import sys
import zipfile
import io
from pathlib import Path

APK_URL = 'https://delver-public.s3.us-west-1.amazonaws.com/app-release.apk'
DB_ENTRY = 'res/Cc.db'


def main(dest_path: str = None, log_path: str = None):
    import requests
    import config

    dest = Path(dest_path) if dest_path else config.DATA_DIR / 'data.db'
    dest.parent.mkdir(parents=True, exist_ok=True)

    log = open(log_path, 'w', buffering=1) if log_path else None

    def emit(msg):
        print(msg, flush=True)
        if log:
            log.write(msg + '\n')
            log.flush()

    try:
        emit(f'Downloading Delver Lens APK from {APK_URL} …')

        resp = requests.get(APK_URL, stream=True, timeout=300)
        resp.raise_for_status()

        total = int(resp.headers.get('content-length', 0))
        downloaded = 0
        chunks = []
        last_pct = -1

        for chunk in resp.iter_content(chunk_size=1024 * 256):  # 256 KB chunks
            chunks.append(chunk)
            downloaded += len(chunk)
            if total:
                pct = int(downloaded / total * 100)
                if pct != last_pct and pct % 5 == 0:
                    emit(f'  {pct}% ({downloaded // 1024 // 1024} MB / {total // 1024 // 1024} MB)')
                    last_pct = pct

        emit('Download complete. Extracting data.db …')

        apk_bytes = b''.join(chunks)
        with zipfile.ZipFile(io.BytesIO(apk_bytes)) as zf:
            if DB_ENTRY not in zf.namelist():
                raise RuntimeError(f'{DB_ENTRY} not found in APK. The APK structure may have changed.')
            db_bytes = zf.read(DB_ENTRY)

        dest.write_bytes(db_bytes)
        emit(f'Saved {len(db_bytes) // 1024} KB to {dest}')
        emit('Done.')
    finally:
        if log:
            log.close()


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)

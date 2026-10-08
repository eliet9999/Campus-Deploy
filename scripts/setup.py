"""One-time local setup; preserves existing secrets and configuration."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from campus.config import Settings
from campus.auth import initialize_secrets
from campus.db import DB

if __name__ == '__main__':
    cfg = Settings(data=ROOT / '.data', secrets=ROOT / '.secrets')
    created = initialize_secrets(cfg)
    DB(cfg).initialize()
    if not (ROOT / '.env').exists():
        (ROOT / '.env').write_text((ROOT / '.env.example').read_text('utf-8'), encoding='utf-8')
    print('Secrets created.' if created else 'Existing secrets preserved.')
    print('Administrator password is in .secrets/admin-password.txt (not printed).')

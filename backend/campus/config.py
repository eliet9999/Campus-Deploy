import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', '.data')).resolve())
    secrets: Path = field(default_factory=lambda: Path(os.getenv('SECRETS_DIR', '.secrets')).resolve())
    ui: Path = field(default_factory=lambda: Path(os.getenv('UI_DIR', 'frontend/dist')).resolve())
    origin: str = field(default_factory=lambda: os.getenv('ADMIN_ORIGIN', 'http://localhost:3000'))
    domain: str = field(default_factory=lambda: os.getenv('BASE_DOMAIN', 'localhost'))
    scheme: str = field(default_factory=lambda: os.getenv('SITE_SCHEME', 'http'))
    port: str = field(default_factory=lambda: os.getenv('SITE_PORT', '8080'))
    gateway: str = field(default_factory=lambda: os.getenv('GATEWAY_INTERNAL_URL', 'http://127.0.0.1:8080'))
    image: str = field(default_factory=lambda: os.getenv('BUILD_IMAGE', 'node:24.11.1-bookworm-slim'))
    instance: str = field(default_factory=lambda: os.getenv('INSTANCE_ID', 'campus-deploy-local'))
    upload_limit: int = field(default_factory=lambda: int(os.getenv('UPLOAD_LIMIT_MIB', '50')) * 1024**2)
    extract_limit: int = field(default_factory=lambda: int(os.getenv('EXTRACT_LIMIT_MIB', '200')) * 1024**2)
    artifact_limit: int = field(default_factory=lambda: int(os.getenv('ARTIFACT_LIMIT_MIB', '100')) * 1024**2)
    file_limit: int = field(default_factory=lambda: int(os.getenv('FILE_LIMIT', '10000')))
    timeout: int = field(default_factory=lambda: int(os.getenv('BUILD_TIMEOUT_SECONDS', '600')))
    min_free: int = field(default_factory=lambda: int(os.getenv('MIN_FREE_MIB', '1024')) * 1024**2)
    log_limit: int = 2 * 1024**2
    lease_seconds: int = 30
    runtime_network: str = field(default_factory=lambda: os.getenv('RUNTIME_NETWORK', os.getenv('INSTANCE_ID', 'campus-deploy-local') + '-runtime'))
    health_timeout: int = field(default_factory=lambda: int(os.getenv('HEALTH_TIMEOUT_SECONDS', '30')))
    runtime_archive_limit: int = 500 * 1024**2
    runtime_file_limit: int = 100000

    @property
    def db(self):
        return self.data / 'metadata' / 'campus.sqlite3'

    def initialize(self):
        for name in ('metadata', 'uploads', 'sources', 'work', 'artifacts', 'artifacts/.staging'):
            (self.data / name).mkdir(parents=True, exist_ok=True)

    def site_url(self, host):
        return f'{self.scheme}://{host}.{self.domain}' + (f':{self.port}' if self.port else '')

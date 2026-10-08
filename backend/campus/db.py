import contextlib
import json
import sqlite3
import time
import uuid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, locator TEXT NOT NULL,
 status TEXT NOT NULL, preset TEXT, sha TEXT, branch TEXT, error TEXT,
 created REAL NOT NULL, request_key TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id), preset TEXT NOT NULL,
 production_id TEXT, created REAL NOT NULL, deleting INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, cleanup TEXT
);
CREATE TABLE IF NOT EXISTS deployments (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 source_id TEXT NOT NULL REFERENCES sources(id), sha TEXT NOT NULL, preset TEXT NOT NULL,
 settings TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
 stages TEXT NOT NULL, finished REAL, exit_code INTEGER, error TEXT,
 artifact TEXT, validation TEXT, cancel INTEGER NOT NULL DEFAULT 0,
 log_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transitions (
 id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, previous_id TEXT,
 deployment_id TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'QUEUED', owner TEXT, created REAL NOT NULL, finished REAL
);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, deployment_id TEXT NOT NULL, created REAL NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_deploy ON logs(deployment_id, id);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, id);
CREATE TABLE IF NOT EXISTS worker_lease (slot INTEGER PRIMARY KEY CHECK(slot=1), owner TEXT, expires REAL NOT NULL);
INSERT OR IGNORE INTO worker_lease VALUES (1,NULL,0);
CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, operation TEXT NOT NULL, response TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (client TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, deployment_id TEXT NOT NULL,
 previous_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
 finished REAL, error TEXT
);
'''


def uid():
    return uuid.uuid4().hex


class DB:
    def __init__(self, settings):
        self.settings = settings

    def connect(self, readonly=False):
        con = sqlite3.connect(f'file:{self.settings.db.as_posix()}?mode=ro' if readonly else str(self.settings.db), uri=readonly, timeout=10)
        con.row_factory = sqlite3.Row
        if not readonly:
            # Keep WAL/SHM available for the read-only gateway, including between requests.
            con.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        con.execute('PRAGMA busy_timeout=10000')
        con.execute('PRAGMA foreign_keys=ON')
        if readonly:
            con.execute('PRAGMA query_only=ON')
        return con

    def initialize(self):
        self.settings.initialize()
        with self.connect() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript(SCHEMA)
            # Additive migrations preserve existing P0 deployments and logs.
            migrations = {
                'deployments': {'runtime_image': 'TEXT', 'runtime_state': 'TEXT', 'runtime_error': 'TEXT',
                    'runtime_log_bytes': 'INTEGER NOT NULL DEFAULT 0', 'runtime_log_container': 'TEXT', 'runtime_log_cursor': 'TEXT'},
                'logs': {'stream': "TEXT NOT NULL DEFAULT 'build'"},
            }
            for table, fields in migrations.items():
                existing = {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}
                for name, definition in fields.items():
                    if name not in existing:
                        con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    @contextlib.contextmanager
    def tx(self):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def one(self, sql, args=(), readonly=False):
        with contextlib.closing(self.connect(readonly)) as con:
            row = con.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=(), readonly=False):
        with contextlib.closing(self.connect(readonly)) as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def log(self, dep, message, stream='build'):
        if stream not in ('build', 'runtime'):
            raise ValueError('unknown log stream')
        column = 'runtime_log_bytes' if stream == 'runtime' else 'log_bytes'
        message = str(message).replace('\x00', '')
        with self.tx() as con:
            row = con.execute(f'SELECT {column} AS used FROM deployments WHERE id=?', (dep,)).fetchone()
            if not row:
                return
            remaining = self.settings.log_limit - row['used']
            if remaining <= 0:
                return
            text = message.encode('utf-8')[:remaining].decode('utf-8', errors='replace')
            if len(message.encode('utf-8')) > remaining:
                text += '\n[로그 한도 도달: 이후 로그 생략]\n'
            con.execute('INSERT INTO logs(deployment_id,created,text,stream) VALUES (?,?,?,?)', (dep, time.time(), text, stream))
            con.execute(f'UPDATE deployments SET {column}={column}+? WHERE id=?', (len(text.encode()), dep))


def enqueue(con, kind, subject):
    con.execute('INSERT INTO jobs(kind,subject,created) VALUES (?,?,?)', (kind, subject, time.time()))


def new_deployment(con, project, source, settings):
    dep = uid()
    now = time.time()
    snapshot = {'build': 'npm ci && npm run build' if source['preset'] == 'VITE_STATIC' else 'STATIC: build skipped',
                'output': 'dist' if source['preset'] == 'VITE_STATIC' else '.', 'image': settings.image,
                'timeout_seconds': settings.timeout, 'memory_bytes': 2 * 1024**3, 'cpu': 1}
    if source['preset'] == 'NODE_SERVER':
        snapshot.update(build='npm ci && npm run build --if-present', output='immutable Docker image',
                        start='npm start', port=8080, runtime_memory_bytes=512 * 1024**2,
                        runtime_cpu=0.5, runtime_pids=128, health_path='/', health_timeout=settings.health_timeout)
    con.execute('INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages) VALUES(?,?,?,?,?,?,?,?,?)',
                (dep, project, source['id'], source['sha'], source['preset'], json.dumps(snapshot), 'QUEUED', now, json.dumps({'QUEUED': now})))
    enqueue(con, 'DEPLOY', dep)
    return dep

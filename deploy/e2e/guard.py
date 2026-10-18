"""Private real-generation test boundaries; production imports none of this."""
from pathlib import Path
import os

HOSTS = frozenset({'asurascans.com', 'cdn.asurascans.com',
                   'generativelanguage.googleapis.com', 'texttospeech.googleapis.com'})
PROXY_PORT = 8149


def allowed_tunnel(authority):
    host, sep, port = authority.rpartition(':')
    return bool(sep and port == '443' and host.lower() in HOSTS)


def blocked(method, path):
    # Publication/auth/upload routes are forbidden even for GET polling.
    if path.startswith(('/api/publishing', '/api/youtube', '/api/drive', '/api/social',
                        '/api/autopilot', '/api/studio/queue', '/api/backup')):
        return True
    if method not in ('GET', 'HEAD', 'OPTIONS'):
        return path in {'/api/settings', '/api/schedule', '/api/spend/cap',
                        '/api/studio/bulk', '/api/studio/stats', '/api/jobs/resume',
                        '/api/review'}  # review approval could schedule or revoice
    return False


def validate(stage, environ):
    stage = Path(stage).resolve()
    app = stage / 'app'
    for rel in ('manhwa-recap-v1/review_ui/projects',
                'manhwa-recap-v1/hyperframes/segments-workspace',
                'manhwa-recap-v1/review_ui/data'):
        p = app / rel
        if not p.resolve().is_relative_to(stage):
            raise RuntimeError('Test state escapes isolated directory')
    if any(app.rglob('.env')) or any(app.rglob('.env.local')):
        raise RuntimeError('Test snapshot must not contain environment files')
    allowed = {'GEMINI_API_KEY', 'TTS_API_KEY', 'SHARED_SECRET'}
    for key in environ:
        upper = key.upper()
        if upper in allowed:
            continue
        if any(s in upper for s in ('UPLOAD', 'DRIVE', 'YOUTUBE', 'GOOGLE_APPLICATION_CREDENTIALS',
                                    'GOOGLE_SERVICE_ACCOUNT', 'ANTHROPIC', 'CLAUDE_API', 'TOKEN')):
            raise RuntimeError('Forbidden external credential/configuration in test process')
    if environ.get('AUTOPILOT_SCHEDULER') != '0' or environ.get('BACKUP_ENABLED') != '0':
        raise RuntimeError('Automatic work must be disabled')
    if environ.get('HTTPS_PROXY') != 'http://127.0.0.1:8149':
        raise RuntimeError('Provider-only proxy is required')
    if environ.get('MANHWA_DATA_DIR') != str(stage / 'data'):
        raise RuntimeError('Isolated database directory is required')
    return stage


def sandbox_profile(stage):
    """macOS OS boundary applies to all descendants, including ffmpeg/Node."""
    stage = str(Path(stage).resolve())
    if '"' in stage or '\\' in stage:
        raise ValueError('Unsafe sandbox path')
    return f'''(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "{stage}") (literal "/dev/null") (literal "/dev/tty"))
(deny network*)
(allow network-outbound (remote ip "localhost:8149") (remote ip "localhost:8138") (remote ip "localhost:3138"))
(allow network-inbound (local ip "localhost:8138") (local ip "localhost:3138"))
(allow network-bind (local ip "localhost:8138") (local ip "localhost:3138"))
'''

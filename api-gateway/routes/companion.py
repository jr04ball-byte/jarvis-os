"""Local iPhone backend. A configured bearer token is mandatory even in open mode."""
import hashlib
import os
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from deps import AI_API_TOKEN, APP_VERSION
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from services import _activity_core, _maintenance_worker

PHONE_UPLOAD_DIR = Path(os.getenv(
    'JARVIS_PHONE_UPLOAD_DIR',
    str(Path(__file__).resolve().parents[2] / 'phone-uploads'),
)).expanduser().resolve()
PHONE_UPLOAD_MAX_BYTES = max(1, int(os.getenv('JARVIS_PHONE_UPLOAD_MAX_MB', '250'))) * 1024 * 1024
_SAFE_PART = re.compile(r'[^A-Za-z0-9._ -]+')


def _safe_name(value: str, fallback: str = 'upload') -> str:
    name = Path(unicodedata.normalize('NFKC', value or '')).name
    name = _SAFE_PART.sub('_', name).strip(' .')
    if not name or name in {'.', '..'}:
        name = fallback
    return name[:180]


def _upload_folder(value: str) -> Path:
    folder = _safe_name(value, 'Inbox') if value.strip() else 'Inbox'
    destination = (PHONE_UPLOAD_DIR / folder).resolve()
    if not destination.is_relative_to(PHONE_UPLOAD_DIR):
        raise HTTPException(400, 'Invalid upload folder')
    return destination


def _unique_destination(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for number in range(2, 10_000):
        alternate = folder / f'{stem} ({number}){suffix}'
        if not alternate.exists():
            return alternate
    raise HTTPException(409, 'Too many files with this name')


async def companion_auth(request: Request):
    if not AI_API_TOKEN:
        raise HTTPException(503, 'Configure AI_API_TOKEN before connecting a companion')
    if not secrets.compare_digest(request.headers.get('authorization', ''), f'Bearer {AI_API_TOKEN}'):
        raise HTTPException(401, 'Companion authentication required')


router = APIRouter(prefix='/v1/companion', dependencies=[Depends(companion_auth)])


@router.post('/confirm')
async def confirm(request: Request):
    from schemas import ConfirmationRequest

    from routes.workers import agent_confirm
    try:
        body = ConfirmationRequest.model_validate(await request.json())
    except ValueError:
        raise HTTPException(422, 'Invalid confirmation') from None
    return await agent_confirm(request, body)


@router.get('/status')
async def status(request: Request, after: int = 0):
    return {'version': APP_VERSION, 'voice_mode': 'push_to_talk', 'audio_output': False,
            'activity': _activity_core(request).snapshot(after),
            'maintenance': dict(_maintenance_worker(request).status)}


@router.post('/chat')
async def chat(request: Request):
    from schemas import AgentChatRequest

    from routes.workers import agent_chat
    try:
        body = AgentChatRequest.model_validate(await request.json())
    except ValueError:
        raise HTTPException(422, 'Invalid chat request') from None
    return await agent_chat(request, body)


@router.post('/upload')
async def upload(file: UploadFile = File(...), folder: str = Form('Inbox')):  # noqa: B008
    """Store one iPhone-selected file on the Jarvis PC without loading it into RAM."""
    destination_folder = _upload_folder(folder)
    destination_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_folder, _safe_name(file.filename or 'upload'))
    temporary = destination_folder / f'.{uuid.uuid4().hex}.uploading'
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open('xb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > PHONE_UPLOAD_MAX_BYTES:
                    raise HTTPException(413, f'File exceeds the {PHONE_UPLOAD_MAX_BYTES // 1048576} MB upload limit')
                digest.update(chunk)
                stream.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    relative = destination.relative_to(PHONE_UPLOAD_DIR).as_posix()
    return {
        'ok': True,
        'name': destination.name,
        'relative_path': relative,
        'size': size,
        'sha256': digest.hexdigest(),
        'stored_at': datetime.now(timezone.utc).isoformat(),
    }


@router.get('/files')
async def files(limit: int = 50):
    PHONE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in PHONE_UPLOAD_DIR.glob('*/*'):
        if not path.is_file() or path.name.startswith('.'):
            continue
        stat = path.stat()
        rows.append({
            'name': path.name,
            'relative_path': path.relative_to(PHONE_UPLOAD_DIR).as_posix(),
            'size': stat.st_size,
            'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    rows.sort(key=lambda row: row['modified_at'], reverse=True)
    return {'root': str(PHONE_UPLOAD_DIR), 'files': rows[:max(1, min(limit, 200))]}


@router.get('/files/{folder}/{filename}')
async def download(folder: str, filename: str):
    path = (_upload_folder(folder) / _safe_name(filename)).resolve()
    if not path.is_relative_to(PHONE_UPLOAD_DIR) or not path.is_file():
        raise HTTPException(404, 'Uploaded file not found')
    return FileResponse(path, filename=path.name)

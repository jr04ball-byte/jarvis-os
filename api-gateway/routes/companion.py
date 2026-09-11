"""Local iPhone backend. A configured bearer token is mandatory even in open mode."""
import secrets

from deps import AI_API_TOKEN, APP_VERSION
from fastapi import APIRouter, Depends, HTTPException, Request
from services import _activity_core, _maintenance_worker


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

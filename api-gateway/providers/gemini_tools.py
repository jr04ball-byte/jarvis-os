"""Native Gemini function turns; raw model parts retain thought signatures."""
import asyncio
from fastapi import HTTPException
import os
import json
import httpx


async def turn(client, model, messages, tools):
    contents, system = [], []
    pending_names = []
    for message in messages:
        role = message.get('role')
        if role == 'system':
            system.append(message.get('content', ''))
            continue
        if role == 'assistant':
            calls = message.get('tool_calls') or []
            pending_names = [c['function']['name'] for c in calls]
            parts = message.get('_gemini_parts') or [{'text': message.get('content') or ' '}]
            contents.append({'role': 'model', 'parts': parts})
        elif role == 'tool':
            name = message.get('name') or (pending_names.pop(0) if pending_names else None)
            if not name:
                raise ValueError('Tool result is missing its function name')
            try:
                result = json.loads(message.get('content', '{}'))
            except ValueError:
                result = {'result': message.get('content')}
            response = {'name': name, 'response': result if isinstance(result, dict) else {'result': result}}
            if message.get('tool_call_id'):
                response['id'] = message['tool_call_id']
            part = {'functionResponse': response}
            if contents and contents[-1]['role'] == 'user' and 'functionResponse' in contents[-1]['parts'][0]:
                contents[-1]['parts'].append(part)
            else:
                contents.append({'role': 'user', 'parts': [part]})
        else:
            contents.append({'role': 'user', 'parts': [{'text': message.get('content') or ' '} ]})
    declarations = []
    for tool in tools:
        fn = tool['function']
        declarations.append({'name': fn['name'], 'description': fn['description'],
                             'parametersJsonSchema': fn.get('parameters', {'type': 'object'})})
    payload = {'contents': contents, 'tools': [{'functionDeclarations': declarations}],
               'generationConfig': {'temperature': 0.2}}
    if system:
        payload['systemInstruction'] = {'parts': [{'text': '\n\n'.join(system)}]}
    base = os.getenv('GEMINI_BASE', 'https://generativelanguage.googleapis.com/v1beta').rstrip('/')
    for attempt in range(3):
        try:
            response = await client.post(f'{base}/models/{model}:generateContent',
                                         headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']}, json=payload)
        except httpx.RequestError:
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            raise HTTPException(503, "Cannot reach Gemini right now. Please try again shortly.") from None
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            await asyncio.sleep(0.5 * (2 ** attempt))
            continue
        if response.status_code >= 400:
            status = 503 if response.status_code >= 500 else response.status_code
            raise HTTPException(status, "Gemini is temporarily unavailable or its request limit was reached. Please try again shortly." if status in (429, 503) else "Gemini rejected the request. Check the provider configuration.")
        break
    candidates = response.json().get('candidates') or []
    if not candidates:
        raise RuntimeError('Gemini returned no candidate')
    parts = candidates[0].get('content', {}).get('parts', [])
    calls = []
    for part in parts:
        if 'functionCall' in part:
            fn = part['functionCall']
            calls.append({'id': fn.get('id'), 'type': 'function',
                          'function': {'name': fn['name'], 'arguments': fn.get('args', {})}})
    return {'role': 'assistant', 'content': ''.join(p.get('text','') for p in parts if not p.get('thought')),
            'tool_calls': calls, '_gemini_parts': parts}

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))
from schema_validation import validate


@pytest.mark.parametrize('value', [True, -1, 2, float('nan')])
def test_nested_numeric_validation(value):
    with pytest.raises(ValueError):
        validate({'rows': [{'weight': value}]}, {'type':'object', 'properties':{
            'rows':{'type':'array','items':{'type':'object','properties':{
                'weight':{'type':'number','minimum':0,'maximum':1}}}}}})


def test_fact_memory_survives_restart_and_forgets(tmp_path):
    from store import ConversationDB
    from fact_memory import facts
    db = ConversationDB(str(tmp_path/'conversations.db'))
    result = facts(db, 'remember_fact', 'I prefer blue')
    facts(db, 'remember_fact', 'I prefer blue')
    other = ConversationDB(str(tmp_path/'conversations.db'))
    assert len(facts(other, 'recall_facts')['facts']) == 1
    facts(other, 'forget_fact', fact_id=result['fact_id'])
    assert facts(db, 'recall_facts')['facts'] == []
    assert 'I prefer blue' not in (tmp_path/'memory-mirror/explicit-facts.md').read_text()


def test_validation_precedes_approval(monkeypatch):
    import services
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid request reached approval creation')
    monkeypatch.setattr(services, '_create_confirmation', forbidden)
    with pytest.raises(Exception) as error:
        asyncio.run(services.execute_tool_core('write_file', {'path': 23}))
    assert error.value.status_code == 400


def test_side_effects_not_retryable():
    from services import _is_idempotent
    for tool in ['computer_shell', 'computer_click', 'computer_key', 'home_device','gmail_send']:
        assert not _is_idempotent(tool)


def test_gemini_raw_parts_and_result_roundtrip(monkeypatch):
    from providers.gemini_tools import turn
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    part = {'functionCall': {'name':'recall_facts','args':{},'id':'call1'}, 'thoughtSignature':'preserve-me'}
    count = 0
    def handler(request):
        nonlocal count
        body=json.loads(request.content)
        count += 1
        if count == 1:
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[part]}}]})
        assert body['contents'][1]['parts'] == [part]
        assert body['contents'][2]['parts'][0]['functionResponse']['id'] == 'call1'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'Your preference is blue.'}]}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            history=[{'role':'user','content':'What do you remember?'}]
            history.append(await turn(client,'test',history,[]))
            history.append({'role':'tool','name':'recall_facts','tool_call_id':'call1','content':'{"facts":["blue"]}'})
            result=await turn(client,'test',history,[])
            assert result['content']=='Your preference is blue.'
    asyncio.run(run())

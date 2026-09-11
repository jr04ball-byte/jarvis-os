"""V18.1 smart-router regression tests: distributed-systems gap + fast-path guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))
from schemas import DEEP_MODEL as DEEP
from schemas import FAST_MODEL as FAST
from schemas import TOOL_MODEL, ChatMessage
from services import apply_system_prompt, select_agent_model, select_model


def route(text, profile='general'):
    """Route exactly like the chat-completions call site: system prompt
    injected, then non-system messages passed to select_model."""
    msgs = apply_system_prompt([ChatMessage(role='user', content=text)], profile)
    return select_model('auto', [m.model_dump() for m in msgs if m.role != 'system'], profile)


def test_a_joke_goes_fast():
    assert route('Tell me a joke') == FAST


def test_b_arithmetic_goes_fast():
    assert route("What's 25 times 4?") == FAST


def test_c_raft_paxos_goes_deep():
    assert route('Explain how a distributed database handles consensus and compare Raft with Paxos.') == DEEP


def test_d_quorum_leader_election_goes_deep():
    assert route('Explain quorum-based replication and leader election.') == DEEP


def test_e_generic_words_stay_fast():
    for text in (
        'Is my database backed up?',
        'The system is slow today, any tips?',
        'Compare these two phones for me.',
        'Explain your refund policy.',
        'How does encryption work?',
    ):
        assert route(text) == FAST, text


def test_f_system_prompt_cannot_force_deep():
    # select_model itself must ignore system-role content even if a caller
    # ever passes unfiltered messages (regression guard for V17.1).
    msgs = [m.model_dump() for m in apply_system_prompt([ChatMessage(role='user', content='Tell me a joke')], 'general')]
    assert any(m['role'] == 'system' for m in msgs)  # sanity: system present
    assert select_model('auto', msgs, 'general') == FAST


def test_explicit_and_sales_rules_preserved():
    assert select_model('llama3.1:8b', [{'role': 'user', 'content': 'hi'}], 'general') == 'llama3.1:8b'
    assert select_model('auto', [{'role': 'user', 'content': 'hi'}], 'sales') == FAST
    assert select_agent_model('auto', [{'role': 'user', 'content': 'hi'}], 'general') == TOOL_MODEL


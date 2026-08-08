"""No test file existed for the TUI before this PR. Rather than trying to
drive the full REPL loop (run() builds its own httpx.Client internally
and reads from console.input() - refactoring that for testability is a
larger change than this PR's scope), these tests cover the two units
that actually carry the new :temp behavior: temperature parsing/
validation, and _ask()'s request/response handling, via httpx.MockTransport
(a real httpx feature, not a hand-rolled fake) instead of hitting a real
API Gateway.
"""

import json

import httpx
import pytest

from ekassistant.tui.app import InvalidTemperature, _ask, _parse_temperature


def test_parse_temperature_accepts_a_value_in_range():
    assert _parse_temperature("0.7") == 0.7


def test_parse_temperature_accepts_the_boundary_values():
    assert _parse_temperature("0.0") == 0.0
    assert _parse_temperature("2.0") == 2.0


def test_parse_temperature_rejects_non_numeric_input():
    with pytest.raises(InvalidTemperature):
        _parse_temperature("hot")


def test_parse_temperature_rejects_a_value_above_the_max():
    with pytest.raises(InvalidTemperature):
        _parse_temperature("2.1")


def test_parse_temperature_rejects_a_negative_value():
    with pytest.raises(InvalidTemperature):
        _parse_temperature("-0.1")


def _client_with_response(json_body: dict, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        handler.last_request = request  # type: ignore[attr-defined]
        return httpx.Response(status_code, json=json_body)

    return httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(handler)
    ), handler


def test_ask_sends_the_question_and_temperature_and_user_header(capsys):
    client, handler = _client_with_response(
        {"answer": "Yes.", "citations": [], "abstained": False, "confidence": 0.8}
    )
    with client:
        _ask(client, "alice", 0.7, "does the vpn need mfa?")

    request = handler.last_request
    assert request.headers["X-User-Id"] == "alice"
    body = json.loads(request.content)
    assert body == {"question": "does the vpn need mfa?", "temperature": 0.7}


def test_ask_renders_the_answer_and_citations(capsys):
    client, _ = _client_with_response(
        {
            "answer": "Yes, MFA is required.",
            "citations": [{"chunk_id": "c1", "source": "policy.md"}],
            "abstained": False,
            "confidence": 0.8,
        }
    )
    with client:
        _ask(client, "alice", 0.2, "does the vpn need mfa?")

    out = capsys.readouterr().out
    assert "Yes, MFA is required." in out
    assert "policy.md" in out
    assert "c1" in out
    assert "confidence: 0.80" in out


def test_ask_renders_abstain_distinctly_and_without_a_confidence_line(capsys):
    client, _ = _client_with_response(
        {"answer": "", "citations": [], "abstained": True, "confidence": None}
    )
    with client:
        _ask(client, "guest", 0.2, "does the vpn need mfa?")

    out = capsys.readouterr().out
    assert "No grounded answer found" in out
    assert "confidence" not in out


def test_ask_reports_a_transport_error_instead_of_raising(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(handler))
    with client:
        _ask(client, "alice", 0.2, "does the vpn need mfa?")

    out = capsys.readouterr().out
    assert "Request to the API Gateway failed" in out

import json

from app.services.audit_service import _sanitize


def test_sanitize_redacts_top_level_sensitive_keys():
    assert json.loads(_sanitize({'key': 'secret'}))['key'] == '***'
    assert json.loads(_sanitize({'password': 'secret'}))['password'] == '***'


def test_sanitize_redacts_nested_sensitive_keys():
    raw = {'config': {'key': 'nested-secret', 'webhook': 'http://x'}, 'name': 'ok'}
    sanitized = json.loads(_sanitize(raw))
    assert sanitized['config']['key'] == '***'
    assert sanitized['config']['webhook'] == '***'
    assert sanitized['name'] == 'ok'


def test_sanitize_redacts_sensitive_keys_in_lists():
    raw = [{'key': 'a'}, {'token': 'b'}, {'plain': 'c'}]
    sanitized = json.loads(_sanitize(raw))
    assert sanitized[0]['key'] == '***'
    assert sanitized[1]['token'] == '***'
    assert sanitized[2]['plain'] == 'c'


def test_sanitize_none_returns_none():
    assert _sanitize(None) is None

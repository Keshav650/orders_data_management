"""Shared credential validation; errors never include credential values."""
import json


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Team access file must contain a JSON object with username and password.')
    username, password = value.get('username'), value.get('password')
    if not isinstance(username, str) or not username.strip() or ':' in username or any(ord(c)<32 for c in username):
        raise ValueError('Username must be non-empty text without a colon or control characters.')
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError('Password must be text with at least 8 characters.')
    return dict(username=username, password=password)


def read_credentials(path):
    try:
        return validate(json.loads(path.read_text(encoding='utf-8-sig')))
    except json.JSONDecodeError as exc:
        raise ValueError('Team access file is not valid JSON. Use double quotes around username and password.') from exc

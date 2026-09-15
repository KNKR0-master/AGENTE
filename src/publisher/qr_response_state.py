"""Fail-closed projection. No response bodies are fetched by this module."""
from urllib.parse import urlsplit

QR_ENDPOINTS = {
    ('www.tiktok.com', '/passport/web/get_qrcode'),
    ('login-us.www.tiktok.com', '/passport/web/check_qrconnect'),
    ('login-eu.www.tiktok.com', '/passport/web/check_qrconnect'),
    ('web-sg.tiktok.com', '/passport/web/check_qrconnect'),
}
# No raw response schema was captured in the historical run. Do not guess fields.
VERIFIED_FIELDS = {}


def project_state(url, payload, *, verified_fields=None):
    endpoint = urlsplit(url)
    key = (endpoint.hostname, endpoint.path.rstrip('/'))
    schema = (VERIFIED_FIELDS if verified_fields is None else verified_fields).get(key)
    if key not in QR_ENDPOINTS or not schema:
        return {'state': 'UNKNOWN', 'reason': 'NO_VERIFIED_SCHEMA', 'fields': {}}
    fields = {}
    # Future reviewed schema maps exact paths to explicitly reviewed scalar enums.
    # Never emit arbitrary strings from payloads, including message/status fields.
    for path, allowed in schema.items():
        value = payload
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if type(value) in (str, int) and value in allowed:
            fields['/'.join(path)] = allowed[value]
    return {'state': 'UNKNOWN', 'reason': 'PROJECTED_FIELDS_ONLY', 'fields': fields}

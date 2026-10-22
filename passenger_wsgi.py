"""Passenger entry point for WHC cPanel Python App.

Configure EVENTOS_* settings through cPanel's environment variables.
No credentials, usernames, database paths or private tokens in this file.
"""
import os
from a2wsgi import ASGIMiddleware

_adapter=None
_adapter_pid=None

def application(environ,start_response):
    global _adapter,_adapter_pid
    pid=os.getpid()
    if _adapter is None or _adapter_pid!=pid:
        from app import app
        _adapter=ASGIMiddleware(app)
        _adapter_pid=pid
    return _adapter(environ,start_response)

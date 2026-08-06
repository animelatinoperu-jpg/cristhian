from contextlib import contextmanager
from contextvars import ContextVar


current_request = ContextVar("current_request", default=None)
automatic_audit_suppressed = ContextVar("automatic_audit_suppressed", default=False)


@contextmanager
def suppress_automatic_audit():
    token = automatic_audit_suppressed.set(True)
    try:
        yield
    finally:
        automatic_audit_suppressed.reset(token)

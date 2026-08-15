from __future__ import annotations

import functools
import inspect
import logging


def log_call(func):
    """Wraps a single function/method: logs a debug line before it runs and
    an error line (with traceback) if it raises, then re-raises unchanged.
    """
    log = logging.getLogger(func.__module__)
    qualname = func.__qualname__

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            log.debug("%s called", qualname, extra={"event": "method_call", "target": qualname})
            try:
                return await func(*args, **kwargs)
            except Exception:
                log.exception(
                    "%s failed", qualname, extra={"event": "method_error", "target": qualname}
                )
                raise
        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        log.debug("%s called", qualname, extra={"event": "method_call", "target": qualname})
        try:
            return func(*args, **kwargs)
        except Exception:
            log.exception(
                "%s failed", qualname, extra={"event": "method_error", "target": qualname}
            )
            raise
    return sync_wrapper


def log_errors(cls):
    """Class decorator: applies log_call to every public method of cls."""
    for name, attr in list(vars(cls).items()):
        if name.startswith("_") or not callable(attr):
            continue
        setattr(cls, name, log_call(attr))
    return cls

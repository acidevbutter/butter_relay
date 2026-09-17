from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


def log_call(func: Callable[P, R]) -> Callable[P, R]:
    """Wraps a single function/method: logs a debug line before it runs and
    an error line (with traceback) if it raises, then re-raises unchanged.
    Works on both sync and async functions.
    """
    log = logging.getLogger(func.__module__)
    qualname = func.__qualname__

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            log.debug("%s called", qualname, extra={"event": "method_call", "target": qualname})
            try:
                return await cast(Callable[P, Awaitable[R]], func)(*args, **kwargs)
            except Exception:
                log.exception(
                    "%s failed", qualname, extra={"event": "method_error", "target": qualname}
                )
                raise

        return cast(Callable[P, R], async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        log.debug("%s called", qualname, extra={"event": "method_call", "target": qualname})
        try:
            return func(*args, **kwargs)
        except Exception:
            log.exception(
                "%s failed", qualname, extra={"event": "method_error", "target": qualname}
            )
            raise

    return sync_wrapper


def log_errors(cls: type[Any]) -> type[Any]:
    """Class decorator: applies log_call to every public method of cls."""
    for name, attr in list(vars(cls).items()):
        if name.startswith("_") or not callable(attr):
            continue
        setattr(cls, name, log_call(attr))
    return cls

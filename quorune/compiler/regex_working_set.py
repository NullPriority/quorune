"""Compiler-local immutable patterns; ordinary regex APIs keep stdlib ownership."""
from functools import lru_cache
import re as _stdlib_re
from re import (
    I, IGNORECASE, M, Match, Pattern, escape, findall, finditer, match, search,
    split, sub, subn,
)

_FACTORY = _stdlib_re.compile
_FACTORY_CODE = getattr(_FACTORY, '__code__', None)
_ENTRYPOINT = _stdlib_re._compile
_ENTRYPOINT_CODE = getattr(_ENTRYPOINT, '__code__', None)


@lru_cache(maxsize=4096)
def _compiled(pattern_type, pattern, flags):
    return _FACTORY(pattern, flags)


def compile(pattern, flags=0):
    """Retain only stock immutable patterns; changed compile policies stay live."""
    factory = _stdlib_re.compile
    if (factory is not _FACTORY or getattr(factory, '__code__', None) is not _FACTORY_CODE
            or _stdlib_re._compile is not _ENTRYPOINT
            or getattr(_ENTRYPOINT, '__code__', None) is not _ENTRYPOINT_CODE):
        return factory(pattern, flags)
    if isinstance(flags, _stdlib_re.RegexFlag):
        flags = flags.value
    if (not isinstance(pattern, (str, bytes, Pattern)) or not isinstance(flags, int)
            or flags & (_stdlib_re.DEBUG | _stdlib_re.TEMPLATE)):
        return factory(pattern, flags)
    return _compiled(type(pattern), pattern, flags)


def fullmatch(pattern, string, flags=0):
    return compile(pattern, flags).fullmatch(string)


def purge():
    _compiled.cache_clear()
    _stdlib_re.purge()


def __getattr__(name):
    # A module facade must not acquire the standard package's search path.
    if name == '__path__':
        raise AttributeError(name)
    return getattr(_stdlib_re, name)

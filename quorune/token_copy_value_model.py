"""Additive copy-value semantics for new games and historical absence."""

TOKEN_COPY_VALUE_VERSION_FIELD = 'token_copy_value_version'


def token_copy_value_version(value):
    if value is None:return None
    if type(value) is not int or value != 1:
        raise ValueError('Unsupported token copy value version')
    return value


def token_copy_value_mode(host):
    return token_copy_value_version(getattr(getattr(host,'state',None),TOKEN_COPY_VALUE_VERSION_FIELD,None))


def token_copy_value_annotations(host):
    return {TOKEN_COPY_VALUE_VERSION_FIELD:1} if token_copy_value_mode(host)==1 else {}

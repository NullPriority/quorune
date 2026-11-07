"""Additive copy-value semantics for new games and historical absence."""

TOKEN_COPY_VALUE_VERSION_FIELD = 'token_copy_value_version'
PREDEFINED_TOKEN_NAME_VERSION_FIELD = "predefined_token_name_version"


def token_copy_value_version(value):
    if value is None:return None
    if type(value) is not int or value != 1:
        raise ValueError('Unsupported token copy value version')
    return value


def token_copy_value_mode(host):
    return token_copy_value_version(getattr(getattr(host,'state',None),TOKEN_COPY_VALUE_VERSION_FIELD,None))


def token_copy_value_annotations(host):
    return {TOKEN_COPY_VALUE_VERSION_FIELD:1} if token_copy_value_mode(host)==1 else {}


def predefined_token_name_version(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported predefined token name version")
    return value


def predefined_token_name_mode(host) -> int | None:
    return predefined_token_name_version(
        getattr(getattr(host, "state", None), PREDEFINED_TOKEN_NAME_VERSION_FIELD, None)
    )

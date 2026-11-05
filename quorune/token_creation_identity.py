from __future__ import annotations

"""Read-only token identity and copiable-value planning.

No refs are allocated, live objects mutated or entry events dispatched here.
The creation transaction owns commitment of the returned values.
"""

import copy
from typing import Any, Mapping, Protocol

from .fixed_token_production import (
    TOKEN_LITERAL_NAME_VERSION_FIELD,
    FixedTokenProductionError,
    literal_token_name_version,
)
from .model import CardInstance
from .token_copy_value_model import token_copy_value_mode,token_copy_value_annotations
from .face_down_characteristics_model import face_down_characteristics_mode
from .face_down_copy import anonymous_face_down_values,token_snapshot_identity
from .defined_token_faces import (
    DEFINED_TOKEN_FACES_FIELD, DEFINED_TOKEN_ACTIVE_FACE_FIELD,
    DEFINED_TOKEN_IDENTITY_FIELD, defined_token_creation_identity,
)


class TokenIdentityHost(Protocol):
    card_db: Any
    state: Any

    @property
    def seats(self) -> list[str]: ...

    def _resolve_object(self, seat: str, ref: str, *, zones: set[str]) -> CardInstance: ...
    def _copyable_characteristics(self, card: CardInstance) -> Mapping[str, Any]: ...
    def _stable_runtime_id(self, namespace: str, value: str) -> str: ...
    def display_name(self, object_id: str) -> str: ...


class TokenCreationError(ValueError):
    pass


def token_copy_snapshot(
    host: TokenIdentityHost,
    source: CardInstance,
) -> dict[str, Any]:
    """Capture one public copiable-value snapshot for later token creation."""

    copyable = copy.deepcopy(dict(host._copyable_characteristics(source)))
    from .token_copy_faces import token_creation_copy_faces
    copyable=token_creation_copy_faces(host,source,copyable)
    from .entry_copy import COPIABLE_ORACLE_FIELD
    from .token_copy_faces import copied_object_name
    if anonymous_face_down_values(host,copyable):
        oracle_id,printed_name=token_snapshot_identity(host,source,copyable)
    else:
        anonymous=token_copy_value_mode(host)==1 and str(copyable.get(COPIABLE_ORACLE_FIELD) or '').startswith('custom-copy:')
        oracle_id=copyable[COPIABLE_ORACLE_FIELD] if anonymous else source.oracle_id
        printed_name=copied_object_name(host,source,copyable) if token_copy_value_mode(host)==1 else source.printed_name
    return {
        "oracle_id": oracle_id,
        "printed_name": printed_name,
        "annotations": {
            "copied_from": source.object_id,
            "copy_overrides": copyable,
        },
        "characteristics": copyable,
    }


def _copied_token_identity(
    host: TokenIdentityHost,
    controller: str,
    *,
    copy_of: Any,
    name: str,
    characteristics: Mapping[str, Any],
    ref: str,
    copy_source_zone: str = "battlefield",
    copy_snapshot: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    if copy_snapshot is not None:
        if set(copy_snapshot) != {
            "oracle_id",
            "printed_name",
            "annotations",
            "characteristics",
        }:
            raise TokenCreationError("Token copy snapshot fields are closed")
        annotations = copy.deepcopy(dict(copy_snapshot["annotations"]))
        overrides = dict(copy_snapshot["characteristics"])
        if name:
            if DEFINED_TOKEN_FACES_FIELD in overrides:
                from .token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
                overrides = apply_copy_exception(overrides, TokenCopyExceptionSpec(name=name,schema_version=3))
            else:
                overrides["name"] = name
        overrides.update(characteristics)
        annotations["copy_overrides"] = overrides
        annotations.update(token_copy_value_annotations(host))
        annotations.pop(DEFINED_TOKEN_IDENTITY_FIELD, None)
        annotations.update(defined_token_creation_identity(overrides))
        return (
            ref,
            str(copy_snapshot["oracle_id"]),
            name or str((overrides.get('name') if token_copy_value_mode(host)==1 else None)
                        or copy_snapshot["printed_name"]),
            annotations,
        )
    original = host._resolve_object(
        controller,
        str(copy_of),
        zones={copy_source_zone},
    )
    from .defined_token_faces import defined_token_definition,defined_token_characteristic_values
    if (token_copy_value_mode(host) is None and defined_token_definition(original) is None
            and not (original.face_down and face_down_characteristics_mode(host)==1)
            and DEFINED_TOKEN_FACES_FIELD not in defined_token_characteristic_values(original)):
        # Historical direct copies retain their original annotation payload.
        annotations = copy.deepcopy(original.annotations)
        annotations['copied_from'] = original.object_id
        overrides = dict(annotations.get('copy_overrides') or {})
        if name:overrides['name'] = name
        overrides.update(characteristics);annotations['copy_overrides'] = overrides
        return ref,original.oracle_id,name or host.display_name(original.object_id),annotations
    return _copied_token_identity(host, controller, copy_of=copy_of, name=name,
        characteristics=characteristics, ref=ref, copy_source_zone=copy_source_zone,
        copy_snapshot=token_copy_snapshot(host, original))


def _new_token_definition(
    host: TokenIdentityHost,
    name: str,
    characteristics: Mapping[str, Any],
) -> tuple[str | None, str, dict[str, Any]]:
    """Share source-owned identity and historical lookup between preview and entry."""
    values = dict(characteristics)
    try:
        name_version = literal_token_name_version(values)
    except FixedTokenProductionError as exc:
        raise TokenCreationError(str(exc)) from exc
    oracle_id = None
    printed_name = name
    if name_version is not None:
        if DEFINED_TOKEN_FACES_FIELD in values:
            raise TokenCreationError("A literal token name cannot override defined faces")
        del values[TOKEN_LITERAL_NAME_VERSION_FIELD]
    elif DEFINED_TOKEN_FACES_FIELD not in values:
        try:
            record = host.card_db.lookup(name)
        except KeyError:
            pass
        else:
            oracle_id, printed_name = record.oracle_id, record.name
    annotations = {
        "token_characteristics": copy.deepcopy(values),
        **defined_token_creation_identity(values),
        **token_copy_value_annotations(host),
    }
    if values:
        annotations["copy_overrides"] = copy.deepcopy(values)
    return oracle_id, printed_name, annotations


def _new_token_identity(
    host: TokenIdentityHost,
    *,
    name: str,
    characteristics: Mapping[str, Any],
    ref: str,
) -> tuple[str, str, str, dict[str, Any]]:
    oracle_id, printed_name, annotations = _new_token_definition(host, name, characteristics)
    if oracle_id is None:
        oracle_id = "custom-token:" + host._stable_runtime_id("token-oracle", ref)
    return ref, oracle_id, printed_name, annotations


def _preview_token_object(
    host: TokenIdentityHost,
    controller: str,
    token_spec: Mapping[str, Any],
) -> CardInstance:
    """Build a nonauthoritative token object for entry preflight.

    No runtime IDs or refs are allocated here.  In particular, an Aura token
    prohibited by CR 303.4g must not consume object identity before the engine
    knows whether it can legally enter attached.
    """

    spec = dict(token_spec)
    characteristics = dict(spec.get("characteristics") or {})
    copy_of = spec.get("copy_of")
    raw_name = spec.get("name")
    name = str(raw_name) if raw_name is not None else ""
    if copy_of:
        snapshot = spec.get("copy_snapshot")
        _,oracle_id,printed_name,annotations = _copied_token_identity(
            host,controller,copy_of=copy_of,name=name,characteristics=characteristics,
            ref='aura-entry-preview',copy_source_zone=str(spec.get('copy_source_zone') or 'battlefield'),
            copy_snapshot=snapshot if isinstance(snapshot,Mapping) else None)
    else:
        if not name:
            name = "Token"
        oracle_id, printed_name, annotations = _new_token_definition(host, name, characteristics)
        if oracle_id is None:
            oracle_id = "custom-token:aura-entry-preview"
    return CardInstance(
        object_id="aura-entry-preview",
        ref="aura-entry-preview",
        oracle_id=oracle_id,
        printed_name=printed_name,
        owner=controller,
        controller=controller,
        zone="outside",
        is_token=True,
        active_face=(annotations.get('copy_overrides') or {}).get(DEFINED_TOKEN_ACTIVE_FACE_FIELD),
        annotations=annotations,
        known_to=list(host.seats),
        revealed_to=list(host.seats),
    )

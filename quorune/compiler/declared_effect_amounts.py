from __future__ import annotations

"""Closed declared-X amounts over existing fixed result and target owners."""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import hashlib
import re
from typing import Any, Callable, Mapping, Sequence

from ..query_effect_amount_model import (
    CAST_X_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_KIND,
    CastXAmountSpec, PublicQueryAmountError, PublicQueryAmountSpec,
    DECLARED_AMOUNT_CAPABILITY, scope_declared_amount_bindings,
)
from .query_characteristic_templates import query_characteristic_quantity
from ..util import mana_cost_to_vector


DECLARED_EFFECT_AMOUNT_MECHANIC = "declared-effect-amount"
CompiledEffectTemplate = tuple[str | None, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]]
EffectCompiler = Callable[[str], CompiledEffectTemplate]
_DEFINITION = re.compile(
    r"(?P<body>.+?), where X is (?:equal to )?the number of (?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_X = re.compile(r"\bX\b")
_RESULT_FIELDS = {
    "damage": ("amount",), "damage_fixed_set": ("amount",),
    "draw": ("count",), "draw_each_player": ("count",),
    "mill": ("count",), "scry": ("count",),
    "life": ("delta",), "lose_life": ("amount",),
    "lose_life_each_opponent": ("amount",), "create_token": ("quantity",),
    "modify_stats_until_end_of_turn": ("power", "toughness"),
    "modify_all_matching_permanents_until_end_of_turn": ("power", "toughness"),
    "apply_source_characteristics_until_end_of_turn": ("power", "toughness"),
}


def _result_slot(operation: str, path: tuple[str, ...]) -> bool:
    return path in tuple((field,) for field in _RESULT_FIELDS.get(operation, ())) or (
        operation == "create_token" and path in {("characteristics", "power"), ("characteristics", "toughness")}
    )


def _signed_number(value: Any) -> int | None:
    if type(value) is int:
        return value
    if type(value) is str and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def declared_effect_amount_template(
    text: str, *, source_name: str, compile_fixed: EffectCompiler,
    cast_x_available: bool = False, forbid_public_x: bool = False,
) -> CompiledEffectTemplate | None:
    """Vary only result slots; prove the entire fixed schema stays invariant."""
    if type(cast_x_available) is not bool or type(forbid_public_x) is not bool:
        return None
    normalized = text.strip()
    definition = _DEFINITION.fullmatch(normalized)
    quantity = None
    if definition is not None:
        if forbid_public_x:
            return None
        body = definition["body"]
        # This definition belongs to one instruction, not arbitrary statements.
        if "." in body or len(re.findall(r"\bwhere X\b", normalized, re.I)) != 1:
            return None
        quantity = query_characteristic_quantity(definition["quantity"], source_name=source_name, definition_extensions=True)
        if quantity is None:
            return None
        try:
            PublicQueryAmountSpec(quantity=quantity)
        except PublicQueryAmountError:
            return None
    elif cast_x_available and not re.search(r"\b(?:where|if|unless)\b", normalized, re.I):
        body = normalized
    else:
        return None
    if _X.search(body) is None:
        return None
    first = compile_fixed(_X.sub("2", body))
    second = compile_fixed(_X.sub("3", body))
    if first[0] is None or second[0] is None or not first[3] or first[3] != second[3] or first[2] != second[2] or len(first[1]) != len(second[1]):
        return None
    binding_id = "x:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    replaced = 0

    def project(a: Any, b: Any, operation: str, path: tuple[str, ...]) -> Any:
        nonlocal replaced
        if a == b and type(a) is type(b):
            return deepcopy(a)
        if isinstance(a, Mapping) and isinstance(b, Mapping) and set(a) == set(b):
            return {key:project(a[key], b[key], operation, (*path, key)) for key in a}
        if not _result_slot(operation, path):
            raise PublicQueryAmountError("X changed something other than a result slot")
        n, m = _signed_number(a), _signed_number(b)
        if n not in {-2, 2} or m != n * 3 // 2 or type(a) is not type(b):
            raise PublicQueryAmountError("X result did not preserve its signed unit coefficient")
        replaced += 1
        coefficient = 1 if n > 0 else -1
        return (
            PublicQueryAmountSpec(quantity=quantity, coefficient=coefficient, schema_version=2, binding_id=binding_id).to_dict()
            if quantity is not None else CastXAmountSpec(coefficient=coefficient).to_dict()
        )

    try:
        effects = tuple(project(a,b,str(a.get("op") or ""),()) for a,b in zip(first[1],second[1],strict=True))
    except PublicQueryAmountError:
        return None
    if not replaced:
        return None
    # A self result must already use the existing logical-object-aware owner.
    if any(effect.get("op") == "modify_stats_until_end_of_turn" and effect.get("card") == "$source" for effect in effects):
        return None
    mechanics = (DECLARED_EFFECT_AMOUNT_MECHANIC, *first[3])
    if quantity is not None:
        mechanics = (*mechanics, "public-query-effect-amount")
    return first[0], effects, deepcopy(first[2]), tuple(dict.fromkeys(mechanics))


def contains_declared_effect_amount(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("kind") == CAST_X_AMOUNT_KIND or (
            value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND and value.get("schema_version") == 2
        ) or any(contains_declared_effect_amount(child) for child in value.values())
    return isinstance(value, (list,tuple)) and any(contains_declared_effect_amount(child) for child in value)


def declared_spell_effect_compiler(record: Any, *, is_spell: bool, oracle_text: str,
                                   compile_effect: Callable[..., Any]) -> Callable[..., Any]:
    """Select announced cost-X context through the canonical mana parser."""
    _, symbols = mana_cost_to_vector(record.mana_cost)
    available = bool(is_spell and not record.faces and symbols and set(symbols) == {"X"})
    return partial(compile_effect,
        cast_x_available=available and re.search(r"\bX (?:is|can't|cannot)\b",oracle_text) is None,
        forbid_public_x=available)


def scope_declared_card_faces(faces: Sequence[Any]) -> tuple[Any, ...]:
    """Scope repeated declarations only at their canonical Oracle-node owner."""
    return tuple(replace(face,nodes=tuple(
        replace(node,effects=tuple(scope_declared_amount_bindings(
            node.effects,f"{face.face_id}:{node.node_id}")))
        if contains_declared_effect_amount(node.effects) else node
        for node in face.nodes)) for face in faces)


def declared_amount_dependencies(mechanics: set[str]) -> set[str]:
    return {DECLARED_AMOUNT_CAPABILITY} if DECLARED_EFFECT_AMOUNT_MECHANIC in mechanics else set()


def is_closed_declared_amount_program(program: Any, *, required_dependencies: Sequence[str],
                                     is_fixed_program: Callable[[Any], bool]) -> bool:
    """Project only validated scalar slots before existing result admission."""
    from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
    from ..rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities

    if DECLARED_EFFECT_AMOUNT_MECHANIC not in program.coverage:
        return False
    context = declared_amount_shape_context(program.effects,set(program.coverage))
    required = set(required_dependencies)
    if context is None or DECLARED_AMOUNT_CAPABILITY not in required or not required.issubset(program.capability_dependencies):
        return False
    effects, mechanics = context
    characteristic_required = set(fixed_resolution_characteristic_set_node_capabilities(
        effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics))
    return bool(characteristic_required and characteristic_required.issubset(program.capability_dependencies)) or is_fixed_program(
        replace(program,effects=effects,coverage=tuple(sorted(mechanics))))


def declared_amount_shape_context(
    effects: Sequence[Mapping[str, Any]], mechanics: set[str],
) -> tuple[tuple[Mapping[str, Any], ...], set[str]] | None:
    """Validate every symbolic path before asking existing fixed shape owners."""
    count = 0
    def project(value: Any, operation: str, path: tuple[str,...]) -> Any:
        nonlocal count
        if isinstance(value, Mapping) and value.get("kind") in {CAST_X_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_KIND}:
            if not _result_slot(operation,path):
                raise PublicQueryAmountError("Declared amount is outside a result slot")
            if value["kind"] == CAST_X_AMOUNT_KIND:
                spec = CastXAmountSpec.from_dict(value)
            else:
                if "public-query-effect-amount" not in mechanics:
                    raise PublicQueryAmountError("Declared public amounts require their query owner")
                spec = PublicQueryAmountSpec.from_dict(value)
                if spec.schema_version != 2:
                    raise PublicQueryAmountError("Declared instruction requires its binding identity")
            count += 1
            result = 2 * spec.coefficient
            return str(result) if path in {("characteristics","power"),("characteristics","toughness")} else result
        if isinstance(value, Mapping):
            return {key:project(child,operation,(*path,key)) for key,child in value.items()}
        if isinstance(value,(list,tuple)):
            if contains_declared_effect_amount(value):
                raise PublicQueryAmountError("Nested effect programs require their own declaration scopes")
            return deepcopy(value)
        return value
    try:
        fixed = tuple(project(effect,str(effect.get("op") or ""),()) for effect in effects)
    except PublicQueryAmountError:
        return None
    return (fixed, mechanics - {DECLARED_EFFECT_AMOUNT_MECHANIC,"public-query-effect-amount"}) if count else None

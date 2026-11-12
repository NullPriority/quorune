from __future__ import annotations

"""Strict capability closure for source attachment instructions."""

from typing import Any, Iterable, Mapping, Sequence

from .attachment_actions import (
    CREATE_AND_ATTACH,
    ENTRY_ATTACHMENT_VERSION,
    FIXED_SOURCE_ATTACHMENT_CAPABILITY,
    FOR_MIRRODIN_CAPABILITY,
    LIVING_WEAPON_CAPABILITY,
    JOB_SELECT_CAPABILITY,
    ENTRY_ATTACHMENT_EVENTS,
    TARGETED_EQUIPMENT_CAPABILITY,
    TARGETED_EQUIPMENT_RECIPIENT,
    is_targeted_equipment_attachment,
    validate_targeted_equipment_attachment,
    validate_created_token_attachment,
    validate_entry_equipment_attachment,
)
from ..creature_subtypes import canonical_creature_subtype
from ..event_counter_reference import EVENT_ENTRY_OBJECT
from .reconfigure import RECONFIGURE_CAPABILITY


def attachment_action_covered_mechanics(
    supplied: Iterable[str],
) -> tuple[str, ...]:
    """Return only mechanics backed by the supplied attachment capabilities."""

    capabilities = frozenset(supplied)
    mechanics: list[str] = []
    if capabilities.intersection(
        {"attachment.aura.simple_object", "attachment.aura.typed_restriction"}
    ):
        mechanics.append("enchant")
    if FIXED_SOURCE_ATTACHMENT_CAPABILITY in capabilities:
        mechanics.append("cr-701-3-attach")
    if LIVING_WEAPON_CAPABILITY in capabilities:
        mechanics.append("living weapon")
    if FOR_MIRRODIN_CAPABILITY in capabilities:
        mechanics.append("for mirrodin!")
    if JOB_SELECT_CAPABILITY in capabilities:
        mechanics.append("job select")
    if RECONFIGURE_CAPABILITY in capabilities:
        mechanics.append('reconfigure')
    return tuple(mechanics)


def _attachment_target_role(group, *, index):
    from ..compiler.direct_target import DirectPermanentTargetSpec

    if not isinstance(group, Mapping) or group.get("id") != f"target_{index}":
        return None
    schema = {key: value for key, value in group.items() if key != "id"}
    if "up_to" in schema:
        if "count" in schema or type(schema["up_to"]) is not int or schema.pop("up_to") != 1:
            return None
        schema["count"] = 1
    if type(schema.get("count")) is not int or schema["count"] != 1:
        return None
    try:
        spec = DirectPermanentTargetSpec.from_target_schema(schema)
    except (TypeError, ValueError):
        return None
    return spec if spec.to_target_schema() == schema else None


def targeted_equipment_attachment_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..compiler.direct_target import DirectPermanentTargetSpec
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    if (len(effects) != 1 or "cr-701-3-attach" not in mechanic_ids
        or not isinstance(target_schema, Mapping) or set(target_schema) != {"groups"}):
        return ()
    effect = effects[0]
    try:
        validate_targeted_equipment_attachment(effect)
    except (TypeError, ValueError):
        return ()
    groups = target_schema["groups"]
    selects_recipient = effect["target"] == TARGETED_EQUIPMENT_RECIPIENT
    if not isinstance(groups, (list, tuple)) or len(groups) != (2 if selects_recipient else 1):
        return ()
    attachment = _attachment_target_role(groups[0], index=0)
    if attachment is None or attachment != DirectPermanentTargetSpec(
        subtypes_any=("equipment",), controller_relation=attachment.controller_relation,
    ):
        return ()
    roles = [attachment]
    if selects_recipient:
        recipient = _attachment_target_role(groups[1], index=1)
        if recipient is None or recipient != DirectPermanentTargetSpec(
            types_any=recipient.types_any, subtypes_any=recipient.subtypes_any,
            controller_relation=recipient.controller_relation,
        ):
            return ()
        subtype = recipient.subtypes_any
        if (recipient.types_any not in {(), ("creature",)}
            or not (recipient.types_any or subtype)
            or subtype and (len(subtype) != 1 or canonical_creature_subtype(subtype[0]) is None)):
            return ()
        roles.append(recipient)
    ambient = {capability for role in roles for capability in direct_target_predicate_capabilities(role.to_target_schema())}
    return tuple(sorted({TARGETED_EQUIPMENT_CAPABILITY, FIXED_SOURCE_ATTACHMENT_CAPABILITY,
                         "target.revalidate_resolution", *ambient}))


def targeted_equipment_attachment_runtime_dependencies(program):
    def uses(value):
        if isinstance(value, Mapping):
            return is_targeted_equipment_attachment(value) or any(uses(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(uses(child) for child in value)

    if not uses(program.effects):
        return ()
    if len(program.effects) != 1:
        raise ValueError("Targeted Equipment requires its own exact attachment instruction")
    effect, = program.effects
    schema, mechanics = program.target_schema, program.coverage
    inherited = ()
    if effect.get("op") == "offer_optional_mana_payment" and effect.get("schema_version") == 4:
        from ..replacement.immutable import thaw_value
        from .reflexive_payment_shapes import reflexive_payment_parts, reflexive_payment_runtime_dependencies

        inherited = reflexive_payment_runtime_dependencies(program)
        _, child = reflexive_payment_parts(effect)
        if not inherited or len(child.effects) != 1:
            raise ValueError("Targeted Equipment requires an independently owned reflexive child")
        effect = thaw_value(child.effects[0])
        schema, mechanics = thaw_value(child.target_schema), child.mechanic_ids
    optional = effect.get("op") == "offer_optional_effect"
    if optional:
        children = effect.get("effects")
        if (set(effect) != {"op", "player", "effects"} or effect["player"] != "$controller"
            or not isinstance(children, (list, tuple)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
            raise ValueError("Optional targeted Equipment requires one closed controller instruction")
        effect, = children
    required = targeted_equipment_attachment_node_capabilities(
        effects=(effect,), target_schema=schema, mechanic_ids=mechanics,
    )
    if not required:
        raise ValueError("Targeted Equipment requires independently closed attachment and recipient roles")
    if effect["target"] == EVENT_ENTRY_OBJECT and program.event not in ENTRY_ATTACHMENT_EVENTS:
        raise ValueError("Equipment attachment to an entrant requires its normalized entry event")
    if effect["target"] != TARGETED_EQUIPMENT_RECIPIENT and program.active_zone != "battlefield":
        raise ValueError("Bound Equipment recipients require their battlefield source program")
    return tuple(sorted({*required, *inherited, *(("effect.choice.optional_fixed",) if optional else ())}))


def is_closed_targeted_equipment_attachment_program(program) -> bool:
    try:
        required = targeted_equipment_attachment_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def fixed_attachment_action_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if 'reconfigure' in mechanics:
        return reconfigure_node_capabilities(effects=effects, target_schema=target_schema)
    if "cr-701-3-attach" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if is_targeted_equipment_attachment(effect):
        return targeted_equipment_attachment_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if ENTRY_ATTACHMENT_VERSION in effect:
        if target_schema is not None:
            return ()
        try:
            return validate_entry_equipment_attachment(effect)
        except (TypeError, ValueError):
            return ()
    if effect.get("op") == CREATE_AND_ATTACH:
        if target_schema is not None:
            return ()
        try:
            return validate_created_token_attachment(effect)
        except (TypeError, ValueError):
            return ()
    if not isinstance(target_schema, Mapping):
        return ()
    if (
        set(effect) != {"op", "attachment_kind", "source", "target"}
        or effect.get("op") != "attach"
        or effect.get("attachment_kind") not in {"aura", "equipment"}
        or effect.get("source") != "$source.zone_object"
        or effect.get("target") != "$target.0"
    ):
        return ()
    if effect.get("attachment_kind") == "equipment" and "controller_relation" in target_schema:
        from ..compiler.direct_target import DirectPermanentTargetSpec
        from .permanent_predicate_capability_shapes import (
            direct_permanent_target_schema_is_closed,
            direct_target_predicate_capabilities,
        )

        if not direct_permanent_target_schema_is_closed(target_schema):
            return ()
        try:
            spec = DirectPermanentTargetSpec.from_target_schema(target_schema)
        except (TypeError, ValueError):
            return ()
        opponent = DirectPermanentTargetSpec(types_any=("creature",), controller_relation="opponent")
        restricted_controller = (
            (spec.types_any == ("creature",) or spec.types_all == ("creature",))
            and (spec.subtypes_any or spec.subtypes_none)
            and spec == DirectPermanentTargetSpec(
                types_any=spec.types_any,
                types_all=spec.types_all,
                subtypes_any=spec.subtypes_any,
                subtypes_none=spec.subtypes_none,
                controller_relation="you",
            )
        )
        if spec != opponent and not restricted_controller:
            return ()
        return (
            FIXED_SOURCE_ATTACHMENT_CAPABILITY,
            "target.revalidate_resolution",
            *direct_target_predicate_capabilities(target_schema),
        )
    allowed_schema = {
        "zones",
        "categories",
        "controller",
        "creature",
        "count",
        "predicate",
    }
    if (
        set(target_schema) - allowed_schema
        or target_schema.get("zones") != ["battlefield"]
        or target_schema.get("categories") != ["permanent"]
        or target_schema.get("creature") is not True
        or type(target_schema.get("count")) is not int
        or target_schema.get("count") != 1
        or target_schema.get("controller") not in {None, "you"}
        or target_schema.get("predicate")
        not in {None, "not_source_attachment"}
    ):
        return ()
    if effect.get("attachment_kind") == "equipment" and (
        target_schema.get("controller") != "you"
        or target_schema.get("predicate") is not None
    ):
        return ()
    return (
        FIXED_SOURCE_ATTACHMENT_CAPABILITY,
        "target.revalidate_resolution",
    )


def is_closed_fixed_source_equipment_activation_program(program) -> bool:
    if (
        program.event != "activate"
        or program.active_zone != "battlefield"
        or not isinstance(program.cost_schema, Mapping)
        or not program.cost_schema
        or program.cost_schema.get("uncompiled_costs")
        or len(program.effects) != 1
        or program.effects[0].get("attachment_kind") != "equipment"
    ):
        return False
    required = fixed_attachment_action_node_capabilities(
        effects=program.effects,
        target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    )
    return FIXED_SOURCE_ATTACHMENT_CAPABILITY in required and set(required).issubset(program.capability_dependencies)


def reconfigure_node_capabilities(*, effects, target_schema):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    attach = {'op':'attach', 'attachment_kind':'equipment',
        'source':'$source.zone_object', 'target':'$target.0'}
    schema = {'zones':['battlefield'], 'categories':['permanent'], 'controller':'you',
        'creature':True, 'count':1, 'source_exclusion':True}
    if (dict(effect) == attach and target_schema == schema and type(target_schema.get('count')) is int
            or dict(effect) == {'op':'unattach', 'source':'$source.zone_object'} and target_schema is None):
        return (RECONFIGURE_CAPABILITY,)
    return ()


__all__ = [
    "attachment_action_covered_mechanics",
    "fixed_attachment_action_node_capabilities",
    "is_closed_fixed_source_equipment_activation_program",
]

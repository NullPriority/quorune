"""Closed paid-X copies of one controlled stack ability."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key

CAPABILITY = "stack.copy.announced_controlled_abilities"
COPY_FIELD = "announced_ability_copies"
REMAINING_FIELD = "_remaining_ability_copies"
_MINIMUM_FIELD = "announced_x_minimum"
_ABILITY_KINDS = {"activated_ability", "triggered_ability"}


def announced_ability_copy_body(text):
    normalized = " ".join(text.replace("’", "'").split()).casefold()
    normalized = normalized.removesuffix(" (mana abilities can't be targeted.)")
    return normalized == (
        "copy target activated or triggered ability you control x times. "
        "you may choose new targets for the copies. this ability can't be copied and x can't be 0."
    )


def copy_effect():
    return {"op": "copy_stack_item", "stack": "$target.0", COPY_FIELD: 1}


def target_schema():
    from ..stack_spell_copy_model import FixedQualifiedStackCopySpec

    return FixedQualifiedStackCopySpec("activated_or_triggered_ability").target_schema


def node_dependencies(*, effects, schema):
    if (len(effects) == 1 and type(effects[0].get(COPY_FIELD)) is int
            and effects[0] == copy_effect() and schema == target_schema()):
        return (CAPABILITY, "stack.copy.fixed_qualified_target", "target.revalidate_resolution")
    return ()


def _positive_double_x_tap(cost):
    from ..activation_x import exact_mana_only_x_activation_cost

    if (not isinstance(cost, Mapping) or cost.get("text") != "{X}{X}, {T}"
            or cost.get("tap_source") is not True or type(cost.get("announced_x_symbols")) is not int
            or cost.get("announced_x_symbols") != 2 or type(cost.get(_MINIMUM_FIELD)) is not int
            or cost[_MINIMUM_FIELD] != 1):
        return False
    ordinary = {key: value for key, value in cost.items() if key != _MINIMUM_FIELD}
    return exact_mana_only_x_activation_cost({
        **ordinary, "text": "{X}", "tap_source": False, "announced_x_symbols": 1,
    })


def program_dependencies(program):
    effects = tuple(program.effects)
    handlers = tuple(getattr(program, "handlers", ()))
    cost = getattr(program, "cost_schema", None)
    if (not contains_nested_key((effects, handlers, cost), COPY_FIELD)
            and not (isinstance(cost, Mapping) and _MINIMUM_FIELD in cost)
            and CAPABILITY not in getattr(program, "capability_dependencies", ())):
        return ()
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or not _positive_double_x_tap(cost)):
        raise ValueError("Announced ability copies require the exact positive double-X tap activation")
    required = node_dependencies(effects=effects, schema=program.target_schema)
    if not required:
        raise ValueError("Announced ability copies require the complete controlled ability instruction")
    if handlers:
        from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

        abilities = activated_abilities_from_descriptors(handlers)
        if len(handlers) != 1 or len(abilities) != 1:
            raise ValueError("Announced ability copies require one original activation descriptor")
        ability, = abilities
        if (ability.announced_x_minimum != 1 or ability.announced_x_symbols != 2
                or ability.target_schema != FrozenMap(target_schema())
                or ability.cost_text != "{X}{X}, {T}" or not ability.tap_source):
            raise ValueError("Announced ability copy price and target descriptor changed")
    return required


def copy_prohibited(host, item):
    """The producer remains a legal target. Copy attempts do not create it."""
    registry = getattr(host, "semantics", None)
    program = registry.get(item.semantic_key) if registry is not None else None
    return (item.kind == "activated_ability" and program is not None
            and any(COPY_FIELD in effect for effect in program.effects))


def active_copy_scope(host, item, *, protected):
    if not protected or item.context.get("currently_resolving") is not True:
        return None
    program = host.semantics.get(item.semantic_key)
    x = item.x_value
    if (type(x) is not int or x < 1 or len(item.targets) != 1
            or not host.semantic_program_is_current_trusted(program)
            or CAPABILITY not in program_dependencies(program)):
        return None
    return FrozenMap({"target": item.targets[0], "total": x})


def choice_copy_count(effect, *, query, producer_ref, actor, target_ref, continuation=False):
    producer = query.stack_object(producer_ref)
    if (type(effect.get(COPY_FIELD)) is not int or effect[COPY_FIELD] != 1
            or producer is None or producer.controller != actor or producer.announced_copy_scope is None
            or producer.announced_copy_scope["target"] != target_ref):
        raise ValueError("Ability copies lost the original resolving activation and target")
    total = producer.announced_copy_scope["total"]
    remaining = effect.get(REMAINING_FIELD, total)
    if type(remaining) is not int or not 1 <= remaining <= total or continuation and REMAINING_FIELD not in effect:
        raise ValueError("Ability copy continuation changed its paid count")
    return remaining

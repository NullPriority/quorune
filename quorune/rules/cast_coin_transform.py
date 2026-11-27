"""One coin flip with the original resolving player's damage or owner return."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..linked_exile_return_model import (
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION,
    LINKED_TRANSFORMED_OWNER_RETURN_VERSION, LinkedExileReturnSpec,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .random_selection import flip_fixed_coin


CAPABILITY = "random.coin.source_damage_or_transformed_return"
MECHANIC = "fixed-cast-coin-transform"
TEMPLATE = "cast-coin-owner-transform-v1"
VERSION_FIELD = "cast_coin_transform_version"
OUTCOME_FIELD = "cast_coin_transform_outcome"
BINDING = "blink:cast-coin-owner-transform"


def coin_instruction():
    return {"op": "damage", "source": "$source", "target": "$controller", "amount": 1, VERSION_FIELD: 1}


def owner_transform_instructions():
    spec = LinkedExileReturnSpec(
        timing="immediate", controller="owner", transformed=True,
        schema_version=LINKED_TRANSFORMED_OWNER_RETURN_VERSION,
    ).to_dict()
    pair = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": BINDING, "spec": spec}
    return ({**pair, "phase": "exile", "cards": "$source.zone_object"},
            {**pair, "phase": "return"})


def original_cast_condition():
    from ..compiler.spell_cast_predicates import (
        FixedSpellCastController, FixedSpellCastQuality, FixedSpellCastSubject, FixedSpellCastTurnRelation,
    )

    return FixedSpellCastSubject(
        controller=FixedSpellCastController.SOURCE, quality=FixedSpellCastQuality.INSTANT_OR_SORCERY,
        turn_relation=FixedSpellCastTurnRelation.SOURCE,
    ).event_condition


def coin_node_capabilities(effects, target_schema, mechanics, cost_schema):
    if MECHANIC not in mechanics and not any(VERSION_FIELD in effect for effect in effects):
        return None
    if (MECHANIC not in mechanics or len(effects) != 1 or target_schema is not None or cost_schema is not None
            or FrozenMap(effects[0]) != FrozenMap(coin_instruction())
            or type(effects[0].get(VERSION_FIELD)) is not int or type(effects[0].get("amount")) is not int):
        return ()
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities

    linked = linked_exile_return_node_capabilities(
        effects=owner_transform_instructions(), target_schema=None, mechanic_ids=(LINKED_EXILE_RETURN_MECHANIC,),
    )
    if not linked:
        return ()
    return (
        CAPABILITY, "damage.amount.positive", "damage.source.stack_current_or_lki",
        "effect.choice.optional_fixed", "zone.change.destination_replacement", *linked,
    )


def random_result_node_capabilities(effects, target_schema, mechanics, cost_schema):
    from .die_roll_draw import declared_die_draw_node_capabilities

    coin = coin_node_capabilities(effects, target_schema, mechanics, cost_schema)
    return declared_die_draw_node_capabilities(effects, target_schema, mechanics, cost_schema) if coin is None else coin


def coin_runtime_dependencies(program):
    if (CAPABILITY not in program.capability_dependencies
            and not any(VERSION_FIELD in effect for effect in program.effects)):
        return ()
    required = coin_node_capabilities(program.effects, program.target_schema, program.coverage, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != FrozenMap(original_cast_condition())
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program.handlers or program.trigger_limit is not None):
        raise ValueError("The coin result requires its original own-turn instant or sorcery cast trigger")
    return required


def _require_resolving_trigger(host, item):
    program = host.semantics.get(item.semantic_key)
    if (item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "spell.cast" or not item.context.get("source_logical_object_id")
            or item.targets or not any(active is item for active in host.state.stack)
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not coin_runtime_dependencies(program)):
        raise ValueError("The coin result lost its original resolving trigger")


def _available_source(host, item):
    source = host.state.cards.get(item.source_object_id)
    return source if (
        source is not None and source.zone == "battlefield" and not source.phased_out
        and source.logical_object_id == item.context.get("source_logical_object_id")
    ) else None


def _receipt(host, item):
    # This production has exactly one original instruction. Choice children
    # use their own subsequent pointers and must refer back to that flip.
    won = flip_fixed_coin(seed=host.state.config.seed, instruction_key=f"{item.ref}|0", seat=item.controller)
    return {"schema_version": 1, "stack": item.ref, "player": item.controller, "won": won}


def _has_original_receipt(host, item, *, needs_win):
    stored = item.context.get(OUTCOME_FIELD)
    expected = _receipt(host, item)
    return (isinstance(stored, Mapping) and thaw_value(stored) == expected
            and type(stored.get("schema_version")) is int and type(stored.get("won")) is bool
            and (not needs_win or expected["won"]))


def _bind_flip(host, item, effect):
    _require_resolving_trigger(host, item)
    if (FrozenMap(effect) != FrozenMap(coin_instruction()) or type(effect.get(VERSION_FIELD)) is not int
            or type(effect.get("amount")) is not int):
        raise ValueError("The source coin instruction changed")
    if item.controller not in host.active_seats:
        return None
    receipt = _receipt(host, item)
    if OUTCOME_FIELD in item.context:
        if not _has_original_receipt(host, item, needs_win=False):
            raise ValueError("The resolving coin outcome changed")
    else:
        item.context[OUTCOME_FIELD] = receipt
        result = "won" if receipt["won"] else "lost"
        host._log(
            item.controller, "coin.flip", f"{item.controller} {result} the coin flip.",
            receipt, importance=2,
        )
    if not receipt["won"]:
        return {key: value for key, value in effect.items() if key != VERSION_FIELD}
    if _available_source(host, item) is None:
        return None
    return {"op": "offer_optional_effect", "player": "$controller", "effects": list(owner_transform_instructions())}


def _bind_return_phase(host, item, effect):
    _require_resolving_trigger(host, item)
    if not _has_original_receipt(host, item, needs_win=True):
        raise ValueError("The source exile and return require the original winning flip")
    if effect.get("phase") == "exile":
        source = _available_source(host, item)
        if source is None:
            return None
        original = dict(effect)
        if original.get("cards") == source.ref:
            original["cards"] = "$source.zone_object"
        if FrozenMap(original) != FrozenMap(owner_transform_instructions()[0]):
            raise ValueError("The winning exile lost its original source incarnation")
        return effect
    if FrozenMap(effect) != FrozenMap(owner_transform_instructions()[1]):
        raise ValueError("The winning return instruction changed")
    return effect if BINDING in item.context.get("linked_exile_returns", {}) else None


def bind_owner_transform_resolution(host, item, effect):
    from .loot_transform_graveyard import bind_loot_transform

    if VERSION_FIELD in effect:
        return _bind_flip(host, item, effect)
    if effect.get("binding_id") == BINDING:
        return _bind_return_phase(host, item, effect)
    return bind_loot_transform(host, item, effect)

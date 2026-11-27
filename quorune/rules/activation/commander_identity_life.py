"""Closed commander-identity life price for one ordinary private draw."""

from collections.abc import Mapping

from ...replacement.immutable import FrozenMap, thaw_value


ACTIVATION_COMMANDER_LIFE_CAPABILITY = "activation.cost.commander_identity_life"
ACTIVATION_COMMANDER_LIFE_FIELD = "commander_identity_life_payment"
PRICE_CLAUSE = "pay life equal to the number of colors in your commanders' color identity"
_PRINTED_COST = "{3}, {t}, " + PRICE_CLAUSE
_MANA = {"GENERIC": 3, **dict.fromkeys("WUBRGC", 0)}
_FALSE_COST_FLAGS = ("untap_source", "discard_source", "sacrifice_source", "exile_source")
_COLORS = frozenset("WUBRG")


def _normalized(text):
    return " ".join(text.replace("’", "'").casefold().split()) if isinstance(text, str) else ""


def commander_identity_life_draw_text(cost, effect):
    return _normalized(cost) == _PRINTED_COST and _normalized(effect) == "draw a card."


def commander_identity_life_draw_runtime_dependencies(program):
    from ...semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID, ActivatedAbilityCatalogHandler

    schema = program.cost_schema or {}
    catalogs = [handler for handler in program.handlers
                if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID]
    if (
        ACTIVATION_COMMANDER_LIFE_FIELD not in schema
        and ACTIVATION_COMMANDER_LIFE_CAPABILITY not in program.capability_dependencies
        and not any(isinstance(raw := row.get("ability"), Mapping)
                    and commander_identity_life_draw_text(raw.get("cost_text"), raw.get("effect_text"))
                    for row in catalogs)
    ):
        return ()
    expected = {
        "text": schema.get("text"), "mana": _MANA, "complex_symbols": [], "tap_source": True,
        **dict.fromkeys(_FALSE_COST_FLAGS, False), "life_payment": 0, "energy_payment": 0,
        "loyalty_delta": None, "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
        ACTIVATION_COMMANDER_LIFE_FIELD: 1,
    }
    if (
        (program.event, program.active_zone) != ("activate", "battlefield")
        or program.target_schema is not None or program.event_condition is not None
        or len(program.handlers) != 1 or len(catalogs) != 1
        or thaw_value(program.effects) != [{"op": "draw", "player": "$controller", "count": 1, "private": True}]
        or FrozenMap(schema) != FrozenMap(expected)
        or not commander_identity_life_draw_text(schema.get("text"), "draw a card.")
        or any(type(schema[field]) is not int for field in (ACTIVATION_COMMANDER_LIFE_FIELD, "life_payment", "energy_payment"))
        or any(type(amount) is not int for amount in schema["mana"].values())
        or schema["tap_source"] is not True or any(schema[field] is not False for field in _FALSE_COST_FLAGS)
    ):
        raise ValueError("Commander-identity life payment requires its original activation price and private draw")
    ability = ActivatedAbilityCatalogHandler().validate(catalogs[0])
    if (
        not commander_identity_life_draw_text(ability.cost_text, ability.effect_text)
        or ability.cost_text != schema["text"] or ability.mana != FrozenMap(_MANA)
        or ability.builtin_semantic_key != program.key or not ability.compiled_cost or ability.mana_ability
        or ability.zones != ("battlefield",) or not ability.tap_source or ability.target_schema is not None
        or any(getattr(ability, field) for field in (*_FALSE_COST_FLAGS, "life_payment", "energy_payment"))
    ):
        raise ValueError("Commander-identity life payment requires its matching original activation catalog")
    return (ACTIVATION_COMMANDER_LIFE_CAPABILITY, "zone.draw.library_to_hand")


def uses_commander_identity_life(ability):
    return commander_identity_life_draw_text(ability.cost_text, ability.effect_text)


def current_commander_identity_life(host, seat, ability):
    """Return the payable amount for the payer's pregame commander designation."""
    if not uses_commander_identity_life(ability):
        return ability.life_payment
    program = host.semantics.get(ability.builtin_semantic_key) if ability.builtin_semantic_key else None
    if (program is None or ACTIVATION_COMMANDER_LIFE_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)):
        return None
    try:
        if not commander_identity_life_draw_runtime_dependencies(program):
            return None
        ids = host.state.commander_oracle_ids.get(seat)
        if not isinstance(ids, (list, tuple)) or not ids or any(type(key) is not str or not key for key in ids):
            return None
        colors = host._commander_identity(seat)
    except (KeyError, TypeError, ValueError):
        return None
    return len(colors) if colors <= _COLORS else None


def commander_identity_life_details(host, seat, ability):
    if not uses_commander_identity_life(ability):
        return {}
    from .model import ActivationProposalError

    amount = current_commander_identity_life(host, seat, ability)
    if amount is None:
        raise ActivationProposalError("Commander-identity life price is unavailable", reason="commander_identity_life_unavailable")
    return {ACTIVATION_COMMANDER_LIFE_FIELD: amount}


def activation_life_from_proposal(ability, details):
    if uses_commander_identity_life(ability):
        from ..action_proposals import thaw_json

        return thaw_json(details)[ACTIVATION_COMMANDER_LIFE_FIELD]
    return ability.life_payment

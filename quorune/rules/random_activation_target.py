from __future__ import annotations

"""One server-chosen damage target at the ordinary activation boundary."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from ..util import stable_json
from .effect_shape_walkers import contains_nested_key
from .random_selection import sample_identity_rows


RANDOM_ACTIVATION_TARGET_FIELD = "random_activation_target_version"
RANDOM_ACTIVATION_TARGET_CAPABILITY = "target.announcement.random_public_single"
_TAP_ONLY_COST = {
    "text": "{T}",
    "mana": {key: 0 for key in ("B", "C", "G", "GENERIC", "R", "U", "W")},
    "complex_symbols": [],
    "tap_source": True,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "loyalty_delta": None,
    "source_counter_removal_cost": None,
    "choices": [],
    "uncompiled_costs": [],
}


def random_activation_has_tap_price(cost: Mapping | None) -> bool:
    return (
        isinstance(cost, Mapping)
        and cost.get("tap_source") is True
        and stable_json(thaw_value(cost)) == stable_json(_TAP_ONLY_COST)
    )


def has_random_activation_target(program) -> bool:
    return program is not None and contains_nested_key(program.effects, RANDOM_ACTIVATION_TARGET_FIELD)


def ordinary_random_activation_damage(effect: Mapping) -> dict:
    amount = effect.get("amount")
    if (
        set(effect) != {"op", "source", "target", "amount", RANDOM_ACTIVATION_TARGET_FIELD}
        or (effect.get("op"), effect.get("source"), effect.get("target")) != ("damage", "$source", "$target.0")
        or type(amount) is not int or amount < 1
        or type(effect[RANDOM_ACTIVATION_TARGET_FIELD]) is not int
        or effect[RANDOM_ACTIVATION_TARGET_FIELD] != 1
    ):
        raise ValueError("Random activation requires one fixed damage effect and its complete target")
    return {key: value for key, value in effect.items() if key != RANDOM_ACTIVATION_TARGET_FIELD}


def random_activation_target_runtime_dependencies(program):
    inputs = (program.effects, program.handlers, program.cost_schema, program.event_condition, program.target_schema)
    if not contains_nested_key(inputs, RANDOM_ACTIVATION_TARGET_FIELD):
        return ()
    if (
        (program.event, program.active_zone) != ("activate", "battlefield")
        or program.provenance.get("granted_only") is True
        or program.event_condition is not None or len(program.effects) != 1
        or not random_activation_has_tap_price(program.cost_schema)
    ):
        raise ValueError("Random target selection requires one complete ordinary tap activation")
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    from .damage_capability_shapes import fixed_damage_node_capabilities

    if len(program.handlers) != 1:
        raise ValueError("Random target selection requires the ordinary activation catalog")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (
        ability.mana_ability or ability.zones != ("battlefield",)
        or ability.builtin_semantic_key != program.key
        or thaw_value(ability.target_schema) != thaw_value(program.target_schema)
        or not random_activation_has_tap_price(activated_ability_cost(ability))
    ):
        raise ValueError("The random activation catalog changed its damage target or tap price")

    required = fixed_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if RANDOM_ACTIVATION_TARGET_CAPABILITY not in required:
        raise ValueError("Random target selection dropped its complete damageable target schema")
    return required


def choose_random_activated_target(host, actor, source, ability, refs: tuple[str, ...]) -> str:
    """Sample all legal targets once before the ordinary activation prices the selected one."""
    rows = []
    for ref in refs:
        snapshot = host._target_snapshot(ref)
        if snapshot.get("category") == "player":
            identity = f"player:{ref}"
        elif snapshot.get("category") == "permanent" and snapshot.get("zone") == "battlefield":
            identity = f"permanent:{snapshot['object_id']}"
        else:
            raise ValueError("A random target lost its public identity")
        rows.append({"object_id": identity, "ref": ref})
    chosen = sample_identity_rows(
        rows, 1, seed=host.state.config.seed, seat=actor, namespace="random-activation-target",
        instruction_key=f"{host.state.revision}|{source.logical_object_id}|{ability.ability_id}",
    )
    if not chosen:
        raise ValueError("No legal random target for this activation")
    return chosen[0]["ref"]

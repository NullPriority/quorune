"""Life from the original artifacts affected by a fixed destruction instruction."""

from collections import Counter
from collections.abc import Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects


ARTIFACT_DESTRUCTION_LIFE_CAPABILITY = "permanent.destroy.affected_artifacts_controller_mana_value_life"
ARTIFACT_DESTRUCTION_LIFE_VERSION = 6
ARTIFACT_LIFE_RESULT_FIELD = "affected_artifact_life_result_version"
ARTIFACT_SCOPE = AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)))
ARTIFACT_LIFE_RESULT = FrozenMap({"kind": "affected_artifact_controller_mana_value_life"})
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections"}


def _mana_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not float(value).is_integer():
        raise ValueError("Artifact life gain needs a current integral mana value")
    return int(value)


def capture_original_artifact_values(snapshot, rows):
    by_id = {row.object_id: row for row in rows}
    return tuple(
        (permanent, _mana_value(by_id[permanent.object_id].mana_value))
        for permanent in snapshot.permanents
    )


def committed_affected_artifact_life(host, facts, *, actor, source_ref):
    totals = Counter()
    for expected, mana_value in facts:
        controller = expected.controller
        current = host.state.cards.get(expected.object_id)
        if (current is not None and current.zone == "battlefield"
                and current.logical_object_id == expected.logical_object_id and current.ref == expected.ref):
            effective = host._effective_card_data(current)
            controller = effective.get("controller", current.controller)
            mana_value = _mana_value(effective.get("mana_value", 0))
        if controller in host.active_seats and mana_value:
            totals[controller] += mana_value
    shares = [{"player": seat, "amount": totals[seat]} for seat in host.apnap_order() if totals[seat]]
    if not shares:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "life", ARTIFACT_LIFE_RESULT_FIELD: 1, "controller": actor, "source": source_ref, "shares": shares,
    }),))


def artifact_destruction_life_runtime_dependencies(program):
    from .destruction_result_model import DestructionFollowup

    effects = tuple(nested_effects((program.effects, program.handlers, program.target_schema)))
    if any(ARTIFACT_LIFE_RESULT_FIELD in effect for effect in effects):
        raise ValueError("Artifact life shares must come from the committed destruction instruction")
    variants = tuple(effect for effect in effects if isinstance(effect.get("followup"), Mapping)
                     and effect["followup"].get("schema_version") == ARTIFACT_DESTRUCTION_LIFE_VERSION
                     and effect.get("op") == "destroy_all")
    if not variants:
        return ()
    if (len(variants) != 1 or variants != tuple(program.effects) or program.handlers
            or program.cost_schema is not None or program.target_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack") or not program.ability_id.startswith("spell:")):
        raise ValueError("Affected artifact life gain requires its complete ordinary destruction spell")
    followup = DestructionFollowup.from_destruction_effect(variants[0])
    return (*followup.capability_dependencies, "permanent.destroy.fixed_set", "permanent.destroy.regeneration_prohibition")


def artifact_life_changes(host, effect, *, actor):
    from ..life_state import LifeChange
    from ..semantic_runtime.context import SemanticSourceContext

    if (set(effect) - _RUNTIME_FIELDS != {"op", ARTIFACT_LIFE_RESULT_FIELD, "controller", "source", "shares"}
            or effect.get("op") != "life" or type(effect.get(ARTIFACT_LIFE_RESULT_FIELD)) is not int
            or effect[ARTIFACT_LIFE_RESULT_FIELD] != 1 or effect["controller"] != actor):
        raise ValueError("Affected artifact life gain must follow the original committed instruction")
    source = SemanticSourceContext(**effect["_runtime_source"])
    item = next((current for current in host.state.stack if current.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.controller != actor or item.context.get("currently_resolving") is not True
            or program is None or ARTIFACT_DESTRUCTION_LIFE_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)):
        raise ValueError("Affected artifact life gain lost its resolving source")
    shares = effect["shares"]
    if (not isinstance(shares, (list, tuple)) or not shares
            or any(not isinstance(row, Mapping) or set(row) != {"player", "amount"}
                   or type(row["player"]) is not str or row["player"] not in host.state.players
                   or type(row["amount"]) is not int or row["amount"] <= 0 for row in shares)):
        raise ValueError("Affected artifact life gain needs one positive share per player")
    amounts = {row["player"]: row["amount"] for row in shares}
    if len(amounts) != len(shares):
        raise ValueError("Affected artifact life gain cannot repeat a player's share")
    return tuple(LifeChange(seat, amounts[seat]) for seat in host.apnap_order() if seat in amounts)

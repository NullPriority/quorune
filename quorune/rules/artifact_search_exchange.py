"""Closed source grammar for a mandatory artifact price and private search payment."""

from collections.abc import Mapping, Sequence
from typing import Any

from .effect_shape_walkers import nested_effects

ARTIFACT_SEARCH_EXCHANGE_OPERATION = "artifact_search_exchange"
ARTIFACT_SEARCH_EXCHANGE_CAPABILITY = "library.search.resolution_artifact_exchange"
ARTIFACT_SEARCH_EXCHANGE_MECHANIC = "resolution-artifact-search-exchange"
ARTIFACT_SEARCH_EXCHANGE_VERSION = 1

_EXACT = (
    "Sacrifice an artifact. If you do, search your library for an artifact card. "
    "If that card's mana value is less than or equal to the sacrificed artifact's mana value, "
    "put it onto the battlefield. If it's greater, you may pay {X}, where X is the difference. "
    "If you do, put it onto the battlefield. If you don't, put it into its owner's graveyard. Then shuffle."
)


def artifact_search_exchange_instruction() -> dict[str, Any]:
    return {"op": ARTIFACT_SEARCH_EXCHANGE_OPERATION, "schema_version": ARTIFACT_SEARCH_EXCHANGE_VERSION}


def artifact_search_exchange_template(text: str):
    if " ".join(text.split()).casefold() != _EXACT.casefold():
        return None
    return ("resolution-artifact-search-exchange-v1", (artifact_search_exchange_instruction(),), None,
            (ARTIFACT_SEARCH_EXCHANGE_MECHANIC,))


def uses_artifact_search_exchange(effect: Mapping[str, Any]) -> bool:
    return effect.get("op") == ARTIFACT_SEARCH_EXCHANGE_OPERATION


def artifact_search_exchange_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None, mechanic_ids,
) -> tuple[str, ...]:
    if (
        target_schema is None and len(effects) == 1
        and ARTIFACT_SEARCH_EXCHANGE_MECHANIC in mechanic_ids
        and dict(effects[0]) == artifact_search_exchange_instruction()
        and type(effects[0]["schema_version"]) is int
    ):
        return (ARTIFACT_SEARCH_EXCHANGE_CAPABILITY,)
    return ()


def artifact_search_exchange_program_dependencies(program) -> tuple[str, ...]:
    if not any(uses_artifact_search_exchange(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:")
        or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("Artifact exchange requires one complete untargeted resolving spell")
    dependencies = artifact_search_exchange_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not dependencies:
        raise ValueError("Artifact exchange requires its closed resolution descriptor")
    return dependencies


def shadowed_reviewed_artifact_exchange_keys(record, generated, reviewed) -> set[str]:
    """Retire the reviewed view only when its complete current replacement is trusted."""
    previous_by_key = {program.key: program for program in reviewed}
    shadowed = set()
    for candidate in generated:
        if not any(uses_artifact_search_exchange(effect) for effect in candidate.effects):
            continue
        previous = previous_by_key.get(candidate.key)
        if previous is None or previous.oracle_id != record.oracle_id or candidate.oracle_id != record.oracle_id:
            continue
        if (
            candidate.trust_level != "trusted"
            or (candidate.capability_closure or {}).get("trusted") is not True
            or ARTIFACT_SEARCH_EXCHANGE_CAPABILITY not in candidate.capability_dependencies
            or previous.trust_level != "trusted" or previous.version != 3
            or previous.provenance.get("review_status") != "reviewed"
            or tuple(map(dict, previous.effects)) != ({"op": "transmute_artifact", "stage": "sacrifice_v3"},)
            or any(getattr(previous, name) != getattr(candidate, name) for name in (
                "active_zone", "event", "event_condition", "target_schema", "cost_schema", "destination",
            ))
            or any(not candidate.provenance.get(name)
                   or candidate.provenance[name] != previous.provenance.get(name)
                   for name in ("source_oracle_hash", "source_rulings_hash"))
        ):
            continue
        if artifact_search_exchange_program_dependencies(candidate):
            shadowed.add(previous.key)
    return shadowed

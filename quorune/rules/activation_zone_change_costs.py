from __future__ import annotations

"""Closed continuation identity for activation zone-change costs."""

from typing import Any, Mapping
from ..replacement.model import ReplacementEffectError

from ..activated_ability_descriptor import SOURCE_LIBRARY_BOTTOM_OFFER
from ..additional_cost_vocabulary import FIXED_ZONE_CHANGE_COST_CONTRACTS
from .announced_hand_discard import ANNOUNCED_HAND_DISCARD_KIND, announced_hand_discard_predicate
from .announced_subtype_sacrifice import (
    ANNOUNCED_SUBTYPE_SACRIFICE_KIND, announced_subtype_sacrifice_predicate,
)
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec


ANNOUNCED_ZONE_COST_ORIGINS = {
    ANNOUNCED_SUBTYPE_SACRIFICE_KIND: "battlefield",
    ANNOUNCED_HAND_DISCARD_KIND: "hand",
}


def announced_zone_cost_replay_refs(choice, response):
    kind = choice.get("k")
    if (kind not in ANNOUNCED_ZONE_COST_ORIGINS or choice.get("z") != ANNOUNCED_ZONE_COST_ORIGINS[kind]
            or type(choice.get("n")) is not int or choice["n"] != 0 or not isinstance(choice.get("q"), Mapping)):
        return None
    from .activation.announced_selected_zone_cost import selected_announced_cost_refs

    try:
        refs = selected_announced_cost_refs(response, response.get("x"))
        query = ObjectQuerySpec.from_dict(choice["q"])
    except (TypeError, ValueError):
        return None
    if kind == ANNOUNCED_HAND_DISCARD_KIND:
        if query != announced_hand_discard_predicate():
            return None
    elif (len(query.subtypes_all) != 1 or canonical_creature_subtype(query.subtypes_all[0]) is None
            or query != announced_subtype_sacrifice_predicate(query.subtypes_all[0])):
        return None
    legal = choice.get("legal_refs")
    return refs if isinstance(legal, (list, tuple)) and all(ref in legal for ref in refs) else None
from .announced_energy_reanimation import FIELD as ENERGY_REANIMATION_FIELD, energy_reanimation_sacrifice_choice
from ..replacement.immutable import thaw_value
from ..util import stable_json


def _energy_reanimation_cost_refs(response: Mapping[str, Any]) -> tuple[str, str] | None:
    summary = response.get("cost_summary")
    if (not isinstance(summary, Mapping) or summary.get(ENERGY_REANIMATION_FIELD) is not True
            or set(summary) - {ENERGY_REANIMATION_FIELD, "choose_cost", "m"}
            or response.get("from") != "battlefield"
            or type(response.get("x")) is not int or response["x"] < 0
            or response.get("_counted_cost_replacement_sequence")):
        return None
    source = response.get("source")
    choices = summary.get("choose_cost")
    cards = response.get("cost_cards")
    objects = response.get("cost_objects")
    refs = objects or cards
    if (type(source) is not str or not source or not isinstance(refs, (list, tuple))
            or len(refs) != 1 or type(refs[0]) is not str or not refs[0] or refs[0] == source
            or (objects and cards and objects != cards)
            or not isinstance(choices, (list, tuple)) or len(choices) != 1 or not isinstance(choices[0], Mapping)):
        return None
    choice = dict(thaw_value(choices[0]))
    legal = choice.pop("legal_refs", None)
    if not isinstance(legal, list) or refs[0] not in legal:
        return None
    if stable_json(choice) != stable_json(energy_reanimation_sacrifice_choice().compact()):
        return None
    return source, refs[0]


def energy_reanimation_replacement_entries(response, entries):
    refs = _energy_reanimation_cost_refs(response)
    if refs is None:
        raise ReplacementEffectError("Energy reanimation replacement requires its two exact cost objects")
    by_ref = {}
    for event_id, values in entries:
        if type(event_id) is not str or not event_id.startswith("zone.change:"):
            raise ReplacementEffectError("Energy reanimation replacement requires a zone-cost event")
        matches = tuple(ref for ref in refs if event_id.endswith(f":{ref}"))
        if (len(matches) != 1 or matches[0] in by_ref or not isinstance(values, (list, tuple))
                or any(type(value) is not str or not value for value in values)):
            raise ReplacementEffectError("Energy reanimation replacement journal does not match its costs")
        by_ref[matches[0]] = (event_id, values)
    return refs, by_ref


def selected_activation_zone_cost_count(response: Mapping[str, Any], choice: Mapping[str, Any]) -> int | None:
    """Keep a retained fixed count or bind the exact positive announced artifact count."""
    count = choice.get("n")
    if "count_parameter" not in choice:
        return count if type(count) is int and 1 <= count <= 10 else None
    from .announced_artifact_sacrifice_return import announced_artifact_predicate
    from ..replacement.immutable import thaw_value

    value = response.get("x")
    if (choice["count_parameter"] != "x" or choice.get("k") != "sacrifice_one"
            or type(count) is not int or count != 1 or "other" in choice or choice.get("z") != "battlefield"
            or thaw_value(choice.get("q")) != announced_artifact_predicate().to_dict()
            or type(value) is not int or value < 1):
        return None
    return value


def activation_zone_change_cost_reference(
    response: Mapping[str, Any],
    *,
    origin: Any,
    destination: Any,
    object_ref: Any,
) -> str | None:
    """Return the exact cost object pinned by a resumable activation."""

    if (
        type(origin) is not str
        or not origin
        or type(destination) is not str
        or not destination
        or type(object_ref) is not str
        or not object_ref
    ):
        return None
    cost_summary = response.get("cost_summary")
    if not isinstance(cost_summary, Mapping):
        return None
    if ENERGY_REANIMATION_FIELD in cost_summary:
        refs = _energy_reanimation_cost_refs(response)
        if refs is None or origin != "battlefield":
            return None
        source, creature = refs
        return object_ref if ((destination, object_ref) in {("hand", source), ("graveyard", creature)}) else None
    if 'random_discard' in cost_summary and any(field in cost_summary for field in ('choose_cost','discard_self','exile_self','sac_self',SOURCE_LIBRARY_BOTTOM_OFFER)):
        return None
    source_ref = response.get("source")
    source_flags = {
        field
        for field in ("discard_self", "exile_self", "sac_self", SOURCE_LIBRARY_BOTTOM_OFFER)
        if field in cost_summary
    }
    if _source_activation_zone_cost_matches(
        response, cost_summary, source_flags, source_ref=source_ref, origin=origin,
        destination=destination, object_ref=object_ref,
    ):
        return object_ref
    if source_flags or type(source_ref) is not str or not source_ref:
        return None
    if 'granting_attachment' in cost_summary:
        payment = cost_summary['granting_attachment']
        return object_ref if (
            isinstance(payment, Mapping) and payment == {'kind': 'sacrifice', 'card': object_ref}
            and object_ref != source_ref and response.get('from') == 'battlefield'
            and origin == 'battlefield' and destination == 'graveyard'
            and not {'random_discard', 'choose_cost'}.intersection(cost_summary)
        ) else None
    if "mill_library" in cost_summary:
        refs = response.get("_mill_library_cost_refs")
        count = cost_summary["mill_library"]
        return object_ref if (
            origin == "library" and destination == "graveyard" and response.get("from") == "battlefield"
            and type(count) is int and count == 1
            and isinstance(refs, (list, tuple)) and tuple(refs) == (object_ref,)
            and not {"random_discard", "choose_cost", "granting_attachment"}.intersection(cost_summary)
        ) else None
    if 'random_discard' in cost_summary:
        refs=response.get('_random_discard_cost_refs')
        payment_id=response.get('_mana_payment_id')
        count=cost_summary['random_discard']
        return object_ref if (origin=='hand' and destination=='graveyard'
            and type(count) is int and 1<=count<=4 and type(payment_id) is str and payment_id
            and isinstance(refs,(list,tuple)) and len(refs)==count
            and all(type(ref) is str and ref for ref in refs) and len(set(refs))==count
            and object_ref in refs) else None

    choices = cost_summary.get("choose_cost")
    raw_refs = response.get("cost_objects") or response.get("cost_cards")
    if (
        not isinstance(choices, (list, tuple))
        or len(choices) != 1
        or not isinstance(choices[0], Mapping)
        or not isinstance(raw_refs, (list, tuple))
        or any(type(ref) is not str for ref in raw_refs)
        or len(set(raw_refs)) != len(raw_refs)
        or object_ref not in raw_refs
    ):
        return None
    choice = choices[0]
    if choice.get("k") in ANNOUNCED_ZONE_COST_ORIGINS:
        refs = announced_zone_cost_replay_refs(choice, response)
        expected_origin = ANNOUNCED_ZONE_COST_ORIGINS[choice["k"]]
        return object_ref if (origin, destination) == (expected_origin, "graveyard") and refs and object_ref in refs else None
    cost_kind = choice.get("k")
    from ..combat_entry_activations import FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND

    if cost_kind == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND:
        from ..cast_lifecycles import combat_return_cost_descriptor

        alternate_refs = response.get("cost_cards")
        if (
            response.get("from") not in ("hand", "command")
            or object_ref == source_ref
            or set(choice) != {"k", "n", "z", "q", "legal_refs"}
            or choice["n"] != 1
            or (alternate_refs and (
                not isinstance(alternate_refs, (list, tuple))
                or tuple(alternate_refs) != tuple(raw_refs)
            ))
        ):
            return None
        combat_return = combat_return_cost_descriptor(unblocked_attacker=True)
        if stable_json(thaw_value(choice["q"])) != stable_json(combat_return["predicate"]):
            return None
        cost_kind = combat_return["operation"]
    contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(str(cost_kind or ""))
    legal_refs = choice.get("legal_refs")
    count = selected_activation_zone_cost_count(response, choice)
    if (
        contract is None
        or contract[0] != origin
        or contract[1] != destination
        or choice.get("z") != origin
        or len(raw_refs) != count
        or not isinstance(choice.get("q"), Mapping)
        or not isinstance(legal_refs, (list, tuple))
        or any(ref not in legal_refs for ref in raw_refs)
    ):
        return None
    return object_ref


def _source_activation_zone_cost_matches(response, cost_summary, source_flags, *, source_ref, origin, destination, object_ref):
    mana = cost_summary.get("m")
    return bool(
        type(source_ref) is str
        and source_ref
        and response.get("from") == origin
        and object_ref == source_ref
        and (
            (
                source_flags == {"discard_self"}
                and type(cost_summary["discard_self"]) is int
                and cost_summary["discard_self"] == 1
                and origin == "hand"
                and destination == "graveyard"
            )
            or (
                source_flags == {"exile_self"}
                and type(cost_summary["exile_self"]) is int
                and cost_summary["exile_self"] == 1
                and destination == "exile"
            )
            or (
                source_flags == {SOURCE_LIBRARY_BOTTOM_OFFER}
                and type(cost_summary[SOURCE_LIBRARY_BOTTOM_OFFER]) is int
                and cost_summary[SOURCE_LIBRARY_BOTTOM_OFFER] == 1
                and origin == "battlefield"
                and destination == "library"
                and set(cost_summary) == {SOURCE_LIBRARY_BOTTOM_OFFER, "m", "tap"}
                and type(cost_summary["tap"]) is int and cost_summary["tap"] == 1
                and isinstance(mana, Mapping) and bool(mana)
                and all(key in {"GENERIC", "W", "U", "B", "R", "G", "C"}
                        and type(amount) is int and amount > 0 for key, amount in mana.items())
            )
            or (
                source_flags == {"sac_self"}
                and type(cost_summary["sac_self"]) is int
                and cost_summary["sac_self"] == 1
                and origin == "battlefield"
                and destination == "graveyard"
            )
        )
    )


def counted_activation_replacement_selections(response: Mapping[str, Any], object_refs: tuple[str, ...]) -> tuple[Any, ...]:
    """Read one chronological, server-authored counted-cost choice sequence."""
    summary = response.get("cost_summary")
    if isinstance(summary, Mapping) and ENERGY_REANIMATION_FIELD in summary:
        journal = response.get("_mana_replacement_selections", {})
        if not isinstance(journal, Mapping):
            raise ReplacementEffectError("Energy reanimation replacement journal is malformed")
        refs, scoped = energy_reanimation_replacement_entries(response, (
            (event_id, values) for event_id, values in journal.items()
            if type(event_id) is str and event_id.startswith("zone.change:")
        ))
        if object_refs != (refs[1],):
            raise ReplacementEffectError("Energy reanimation selects exactly one creature to sacrifice")
        return tuple(scoped.get(refs[1], ("", ()))[1])
    sequence = response.get('_counted_cost_replacement_sequence', ())
    if not isinstance(sequence, (list, tuple)):
        raise ReplacementEffectError('Counted activation replacement sequence is malformed')
    selections = []
    by_ref = set(object_refs)
    for row in sequence:
        if not isinstance(row, Mapping) or set(row) != {'object_ref', 'selection'} or type(row['object_ref']) is not str or row['object_ref'] not in by_ref:
            raise ReplacementEffectError('Counted activation replacement object is malformed')
        selection = row['selection']
        if type(selection) is str and selection:
            selections.append(selection)
        else:
            raise ReplacementEffectError('Counted activation replacement selection is malformed')
    if not sequence and len(object_refs)==1:
        journal=response.get('_mana_replacement_selections',{})
        if not isinstance(journal,Mapping):
            raise ReplacementEffectError('Singleton activation replacement journal is malformed')
        for event_id,values in journal.items():
            if type(event_id) is not str or not event_id.startswith('zone.change:'):continue
            if not event_id.endswith(':'+object_refs[0]) or not isinstance(values,(list,tuple)) or any(type(v) is not str or not v for v in values):
                raise ReplacementEffectError('Singleton activation replacement identity is malformed')
            selections.extend(values)
    return tuple(selections)


__all__ = ["activation_zone_change_cost_reference"]

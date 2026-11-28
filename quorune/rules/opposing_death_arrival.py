"""Materialize the counter and characteristic riders of a committed death return."""

from ..entry_counter_model import EffectEntryCounter
from ..entry_results import EntryCharacteristicAddition
from ..zone_transitions import ZoneTransitionOwner


def commit_opposing_death_return(
    host, cards, *, actor, runtime_source, reason, replacement_selections=(), aura_target_refs=None,
):
    """Return the supplied current graveyard cards through the normal entry transaction."""
    addition = EntryCharacteristicAddition(runtime_source, colors=("B",), creature_subtypes=("zombie",))
    counter = EffectEntryCounter("+1/+1", 1, actor, runtime_source.stack_ref, "122.6a", additional=True)
    changes = tuple((card.object_id, "battlefield") for card in cards)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, destination_controllers=dict.fromkeys((oid for oid, _ in changes), actor),
        putting_players=dict.fromkeys((oid for oid, _ in changes), actor),
        effect_entry_counters={oid: (counter,) for oid, _ in changes},
        entry_characteristic_additions={oid: addition for oid, _ in changes},
        replacement_selections=replacement_selections, aura_target_refs=aura_target_refs,
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")

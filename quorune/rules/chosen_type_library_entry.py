from __future__ import annotations

"""Reveal the resolving player's prefix through the first chosen-type creature."""

from ..creature_subtypes import canonical_creature_subtype
from ..library_battlefield_model import LIBRARY_SHUFFLE_REMAINDER, RevealedCreatureEntryFollowup
from ..object_predicate import ObjectQuerySpec
from ..reveal_until_library_selection_model import read_reveal_until_prefix
from ..semantic_runtime.intents import LibrarySelectionIntent, RevealLibraryCardsIntent
from .library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity


CHOSEN_TYPE_LIBRARY_ENTRY_CAPABILITY = "choice.creature_type.reveal_until_entry"


def chosen_type_revealed_entry_plan(query, *, actor, subtype):
    chosen = canonical_creature_subtype(subtype)
    if not chosen or actor not in query.active_seats:
        raise ValueError("A revealed entry requires one canonical creature type and its live controller")
    predicate = ObjectQuerySpec(zones=("library",), types_all=("creature",), subtypes_all=(chosen,))
    rows, selected = read_reveal_until_prefix(query, player=actor, predicates=(predicate,), stop_count=1)
    looked = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    return LibrarySelectionArrangement(
        looked=looked, selected_refs=selected, remainder_refs=tuple(row.ref for row in rows if row.ref not in selected),
        remainder_destination=LIBRARY_SHUFFLE_REMAINDER, remainder_order="random",
        selected_destination="battlefield", entry_followup=RevealedCreatureEntryFollowup(None, shuffle_only=True),
    )


def chosen_type_library_entry_intents(query, *, actor, subtype, stack_ref, reason):
    plan = chosen_type_revealed_entry_plan(query, actor=actor, subtype=subtype)
    reveal = (RevealLibraryCardsIntent(actor=actor, player=actor, viewer=actor, public=True,
                                      refs_top_first=tuple(value.ref for value in plan.looked), reason=reason),
              ) if plan.looked else ()
    return (*reveal, LibrarySelectionIntent(actor=actor, player=actor, arrangement=plan, reason=reason,
                                           source_stack_ref=stack_ref, looked_are_public=True,
                                           selected_are_public=False))

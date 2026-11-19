"""Read current typed characteristics only within the actor's legal search window."""

from ..object_query import object_query_result, object_matches_query
from ..qualified_library_search_model import QualifiedLibrarySearchSpec
from .library_search_rules import library_search_object_ids


def qualified_library_card_matches(spec, row):
    return (object_matches_query(row, spec.query)
            and (spec.mana_value_max is None or row.mana_value <= spec.mana_value_max)
            and (not spec.mana_values_any or row.mana_value in spec.mana_values_any))


def qualified_library_search_options(host, player, effect):
    spec = QualifiedLibrarySearchSpec.from_effect(effect)
    options = []
    for oid in library_search_object_ids(host, player, "library"):
        card = host.state.cards[oid];effective = host._effective_card_data(card)
        row = object_query_result(card, effective, type_parts=host._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True, attached_to_ref=None)
        if qualified_library_card_matches(spec, row):
            options.append({"id": card.ref, "name": card.printed_name})
    return options

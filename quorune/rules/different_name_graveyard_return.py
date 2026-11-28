"""Pairwise rules-name constraints for an optional simultaneous graveyard return."""

from collections import defaultdict

from ..object_predicate import ObjectQuerySpec
from .graveyard_card_targets import GraveyardCardTargetKind


CAPABILITY = "zone.return.own_graveyard_different_name_choice"
SELECTION_VERSION = 7
CHOSEN_MOVE_VERSION = 11
PERMANENT_TYPES = GraveyardCardTargetKind.PERMANENT_CARD.types_any
PERMANENT_QUERY = ObjectQuerySpec(zones=("graveyard",), types_any=PERMANENT_TYPES, token=False)


def different_name_limits(ref_names):
    """Describe each common name and each pair that would include a nameless card."""
    refs = tuple(ref for ref, _names in ref_names)
    by_name = defaultdict(set)
    bounds = set()
    for ref, names in ref_names:
        if not names:
            bounds.update(tuple(sorted((ref, other))) for other in refs if other != ref)
        for name in names:
            by_name[name].add(ref)
    bounds.update(tuple(sorted(group)) for group in by_name.values() if len(group) > 1)
    return tuple({"refs": list(group), "maximum": 1} for group in sorted(bounds))


def chosen_permanents_have_different_names(ref_names):
    return not different_name_limits(ref_names)


def graveyard_front_faces(host, objects):
    from ..errors import GameRuleError

    faces = {}
    for value in objects:
        record = host.card_record(host.state.cards[value.object_id])
        if record is None:
            raise GameRuleError("A returned permanent card requires its original card record")
        if record.layout not in {"modal_dfc", "transform", "battle"}:
            continue
        face = record.faces[0] if record.faces else {}
        types, _, _ = host._type_parts(face.get("type_line") or "")
        name = face.get("name")
        if not set(types).intersection(PERMANENT_TYPES) or type(name) is not str or not name:
            raise GameRuleError("A double-faced permanent return requires its printed permanent front")
        faces[value.object_id] = name
    return faces

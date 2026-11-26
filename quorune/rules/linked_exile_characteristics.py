from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

from ..continuous_effect_model import ContinuousObjectIdentity, Layer
from ..creature_subtypes import canonical_creature_subtype
from ..linked_ability_exile_state import current_linked_ability_exile
from ..linked_exile_characteristic_model import HANDLER_ID, linked_exile_characteristic_candidates
from ..linked_exile_return_model import ExiledObjectIdentity, validate_linked_ability_binding
from ..object_query import exact_numeric_characteristic


def descriptor_for_link(binding):
    validate_linked_ability_binding(binding)
    return {"handler_id": HANDLER_ID, "schema_version": 1, "event": "characteristics.evaluate", "binding_id": binding}


def characteristic_link_binding(value):
    if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
        raise ValueError("A linked battlefield setting requires its exact descriptor")
    binding = value.get("binding_id")
    if dict(value) != descriptor_for_link(binding):
        raise ValueError("The linked battlefield setting has unexpected fields")
    return binding


def face_up_characteristic_links(state, source, binding):
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    linked = current_linked_ability_exile(SimpleNamespace(state=state), source=identity, ability_pair=binding)
    public = (row for row in linked if not state.cards[row.object_id].face_down)
    return tuple(sorted(public, key=lambda row: (state.cards[row.object_id].zone_timestamp, row.ref), reverse=True))


def resolve_linked_exile_characteristics(host, raw, *, statistics):
    layer = Layer.POWER_TOUGHNESS if statistics else Layer.COLOR
    for row in linked_exile_characteristic_candidates(raw):
        identity = ExiledObjectIdentity.from_dict(row)
        card = host.state.cards.get(identity.object_id)
        if not identity.matches_exiled_card(card) or card.face_down:
            continue
        data = host._effective_card_data(card, maximum_layer=layer)
        types, subtypes, _supertypes = host._type_parts(str(data.get("type_line") or ""))
        if "creature" not in types:
            continue
        if statistics:
            return tuple(exact_numeric_characteristic(card, data, field) or 0 for field in ("power", "toughness"))
        copied = {canonical_creature_subtype(word) for word in subtypes}
        return tuple(sorted((copied - {None}) | {"shapeshifter"}))
    return None

"""Delegate one validated face-up attempt to the shared status/reveal owner."""
from ..errors import GameRuleError
from ..face_up_effect_model import validate_face_up_instruction


def apply_targeted_face_up(host,effect,*,actor,operation,reason):
    from ..zone_transitions import ZoneTransitionOwner
    try:validate_face_up_instruction(effect,allow_runtime_source=True)
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    try:card=host._resolve_object(actor,effect['card'],zones={'battlefield'})
    except GameRuleError:return None
    if card.phased_out or not card.face_down:return None
    if not ZoneTransitionOwner(host).turn_face_up(card,viewers=tuple(host.seats)):return card.ref
    host._log(actor,'permanent.turn_face_up','A permanent was turned face up.',
        {'object':card.ref,'actor':actor,'controller':card.controller,'reason':reason},
        changed_objects=[card.object_id],changed_players=[card.controller],importance=1)
    host._dispatch_semantic_event('permanent.turned_face_up',
        {'card':card.ref,'object_ref':card.ref,'object_id':card.object_id,
         'logical_object_id':card.logical_object_id,'controller':card.controller},
        sources=host._semantic_event_sources(zones={'battlefield'}))
    return card.ref

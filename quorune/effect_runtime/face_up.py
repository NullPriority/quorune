"""Delegate one validated face-up attempt to the shared status/reveal owner."""
from ..errors import GameRuleError
from ..face_up_effect_model import validate_face_up_instruction


def apply_targeted_face_up(host,effect,*,actor,operation,reason):
    from ..rules.morph_actions import dispatch_permanent_turn_face_up
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
    dispatch_permanent_turn_face_up(host, card, actor)
    return card.ref

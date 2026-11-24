"""Delegate one validated face-up attempt to the shared status/reveal owner."""
from ..errors import GameRuleError
from ..face_up_effect_model import SOURCE_FACE_DOWN_SCHEMA_VERSION, validate_face_up_instruction


def apply_targeted_face_up(host,effect,*,actor,operation,reason):
    if effect.get("schema_version") == SOURCE_FACE_DOWN_SCHEMA_VERSION:
        return apply_source_face_down(host,effect,actor=actor,operation=operation,reason=reason)
    from ..rules.morph_actions import dispatch_permanent_turn_face_up
    from ..zone_transitions import ZoneTransitionOwner
    try:validate_face_up_instruction(effect,allow_runtime_source=True)
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    try:card=host._resolve_object(actor,effect['card'],zones={'battlefield'})
    except GameRuleError:return None
    if card.phased_out or not card.face_down:return None
    if not ZoneTransitionOwner(host).turn_face_up(card,viewers=tuple(host.seats),
            copy_selections=effect.get('_replacement_selections',())):
        return card.ref
    host._log(actor,'permanent.turn_face_up','A permanent was turned face up.',
        {'object':card.ref,'actor':actor,'controller':card.controller,'reason':reason},
        changed_objects=[card.object_id],changed_players=[card.controller],importance=1)
    dispatch_permanent_turn_face_up(host, card, actor)
    return card.ref


def apply_source_face_down(host, effect, *, actor, operation, reason):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..face_up_effect_model import validate_source_face_down_instruction
    from ..zone_transitions import ZoneTransitionOwner

    try:
        validate_source_face_down_instruction(effect, runtime=True)
        source = ResolutionEffectSource.from_effect(effect)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    card = host.state.cards.get(source.object_id)
    if (card is None or card.logical_object_id != source.logical_object_id
            or card.ref != effect["card"] or card.ref != source.card_ref):
        return None
    if ZoneTransitionOwner(host).turn_face_down(card):
        host._log(actor,"permanent.turn_face_down","A permanent was turned face down.",
                  {"object":card.ref,"reason":reason},changed_objects=[card.object_id],importance=1)
    return card.ref

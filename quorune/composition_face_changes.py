"""Atomic physical face changes on one retained composed permanent."""
from .errors import GameRuleError
from .rules.composition_faces import PreparedCompositionTransform,prepare_composition_transform


def commit_composition_transform(owner,prepared):
    if not isinstance(prepared,PreparedCompositionTransform):
        raise GameRuleError('Physical transform preparation is missing')
    with owner.host.transaction():
        try:
            current=prepare_composition_transform(owner.host,prepared.object_identity,
                expected_transform_count=prepared.root_state.get('transform_count',0))
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        if current!=prepared:
            raise GameRuleError('Physical transform preparation is stale or changed')
        root=owner.state.cards[prepared.object_identity.object_id]
        root.permanent_composition=prepared.after
        root.transform_count+=1
        prepared.after.validate_carrier(root)
        return prepared.after


def prepare_composition_transform_instruction(host,card,*,expected_transform_count,day_night_instruction):
    from .composition_characteristics import composition_day_night_bound_modes
    from .continuous_effect_model import ContinuousObjectIdentity
    if day_night_instruction or composition_day_night_bound_modes(host,card):
        return None
    return prepare_composition_transform(host,
        ContinuousObjectIdentity(card.object_id,card.logical_object_id),
        expected_transform_count=expected_transform_count)


def commit_composition_face_up(owner,prepared):
    from .rules.composition_faces import PreparedCompositionFaceUp,prepare_composition_face_up
    if not isinstance(prepared,PreparedCompositionFaceUp):
        raise GameRuleError('Physical face-up preparation is missing')
    with owner.host.transaction():
        try:current=prepare_composition_face_up(owner.host,prepared.object_identity)
        except ValueError as exc:raise GameRuleError(str(exc)) from exc
        if current!=prepared:raise GameRuleError('Physical face-up preparation is stale or changed')
        root=owner.state.cards[prepared.object_identity.object_id]
        if not prepared.can_turn_face_up:
            from dataclasses import replace
            from .replacement.immutable import FrozenMap
            parts=[];disclosed=[]
            for part in prepared.before.parts:
                if not part.card_state['face_down']:parts.append(part);continue
                card=part.detached_card()
                card.known_to=sorted(set(card.known_to).union(prepared.viewers))
                parts.append(replace(part,card_state=FrozenMap(card.to_dict())))
                disclosed.append({'object':card.ref,'oracle_id':card.oracle_id,'card':card.printed_name})
            root.permanent_composition=replace(prepared.before,parts=tuple(parts))
            owner.reveal_face_up_refusal(root,viewers=prepared.viewers,components=tuple(disclosed))
            root.permanent_composition.validate_carrier(root)
            return root.permanent_composition
        owner._turn_face_up_status(root,viewers=prepared.viewers)
        root.permanent_composition=prepared.after
        prepared.after.validate_carrier(root)
        return prepared.after

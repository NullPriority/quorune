"""Atomic face-status changes and physical face changes for composed permanents."""
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


def commit_face_up_status(owner,card,*,viewers,copy_selections=(),prospective_composition=None):
    from dataclasses import replace
    from .entry_results import begin_face_status_copy
    from .errors import StateInvariantError
    from .face_status_timestamp_model import planned_face_up_timestamp
    from .model import CardInstance
    from .semantic_runtime.entry_copy import resolve_face_up_copy
    from .zone_object_state import turn_card_face_up

    if not isinstance(card,CardInstance) or owner.state.cards.get(card.object_id) is not card:
        raise GameRuleError('Turning face up requires its exact current object')
    try:
        timestamp=planned_face_up_timestamp(owner.state)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    preview=replace(card,face_down=False,permanent_composition=prospective_composition)
    result=resolve_face_up_copy(owner.host,card,preview=preview,selections=copy_selections)
    turn_card_face_up(card,viewers=viewers,timestamp=timestamp)
    if timestamp is not None and owner.next_timestamp()!=timestamp:
        raise StateInvariantError('Face-up timestamp changed during commitment')
    if result is not None and result.values is not None:
        begin_face_status_copy(card, result.values)


def commit_composition_face_up(owner,prepared,*,copy_selections=()):
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
        owner._turn_face_up_status(root,viewers=prepared.viewers,copy_selections=copy_selections,
                                   prospective_composition=prepared.after)
        root.permanent_composition=prepared.after
        prepared.after.validate_carrier(root)
        return prepared.after


def commit_face_down_status(owner, card):
    """Resolve the complete physical face-down change at the canonical timestamp."""
    from dataclasses import replace
    from .errors import StateInvariantError
    from .face_status_timestamp_model import planned_face_up_timestamp
    from .model import CardInstance
    from .replacement.immutable import FrozenMap
    from .rules.composition_faces import _component_characteristic_view
    from .zone_object_state import turn_card_face_down

    if not isinstance(card, CardInstance) or owner.state.cards.get(card.object_id) is not card:
        raise GameRuleError("Turning face down requires its exact current object")
    if card.zone != "battlefield" or card.face_down or card.phased_out:
        return False
    composition = card.permanent_composition
    parts = tuple((snapshot.detached_card(), component.is_token)
                  for snapshot, component in zip(composition.parts, composition.topology.components, strict=True)
                  ) if composition is not None else ((card, card.is_token),)
    if any(_double_faced_part(owner.host, physical, token=token) for physical, token in parts):
        return False
    after = None
    if composition is not None:
        changed = []
        for snapshot, (physical, token) in zip(composition.parts, parts, strict=True):
            if physical.face_down:
                changed.append(snapshot)
                continue
            view = replace(physical, zone="battlefield")
            turn_card_face_down(view)
            stored = replace(view, zone=physical.zone)
            values = owner.host._copyable_characteristics(_component_characteristic_view(view,is_token=token))
            changed.append(replace(snapshot, card_state=FrozenMap(stored.to_dict()),copiable_values=FrozenMap(values)))
        after = replace(composition, parts=tuple(changed))
    timestamp = planned_face_up_timestamp(owner.state)
    with owner.host.transaction():
        turn_card_face_down(card, timestamp=timestamp)
        if after is not None:
            card.permanent_composition = after
            after.validate_carrier(card)
        if timestamp is not None and owner.next_timestamp()!=timestamp:
            raise StateInvariantError("Face-down timestamp changed during commitment")
    return True


def _double_faced_part(host, physical, *, token):
    if token:
        from .defined_token_faces import defined_token_definition
        from .rules.composition_faces import _component_characteristic_view
        return defined_token_definition(_component_characteristic_view(physical,is_token=True)) is not None
    record = host.card_db.by_oracle_id(physical.oracle_id)
    return record is not None and record.layout in {"transform", "modal_dfc", "meld", "reversible_card"}

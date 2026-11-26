"""Materialize replacement-aware token preview and combat-entry choice facts."""
from typing import Any, Mapping
from ..replacement.immutable import FrozenMap
from ..token_creation import preview_token_creation


def semantic_choice_token_creation_preview(
    self,
    actor: str,
    effect: Mapping[str, Any],
) -> tuple[FrozenMap, ...]:
    operation = str(effect.get("op") or "")
    common: dict[str, Any]
    from ..rules.attached_role_batch import ATTACHED_ROLE_BATCH_OPERATION, PREVIEW_FIELD, resolve_attached_role_batch

    if operation == ATTACHED_ROLE_BATCH_OPERATION:
        base = {key: value for key, value in effect.items() if key != PREVIEW_FIELD}
        resolved, _ = resolve_attached_role_batch(self, actor, base)
        return tuple(FrozenMap(token) for token in resolved.specs)
    if operation == 'create_role_token':
        common = {'name': str(effect.get('name') or ''), 'quantity': 1, 'attacking_groups': (),
            'characteristics': dict(effect.get('characteristics') or {})}
    elif operation == 'choose_fixed_attacking_token_destinations' and effect.get('token_entry_version') == 3:
        from ..rules.per_opponent_attacking_tokens import per_opponent_token_inputs
        _opponents, _groups, common = per_opponent_token_inputs(actor, effect,
            seats=self.seats, active_seats=self.active_seats,
            attacking=self.state.phase == 'combat' and self.state.active_player == actor)
    elif operation == 'choose_fixed_attacking_token_destinations' and effect.get('token_entry_version') == 4:
        common = {'name': '', 'quantity': 1, 'tapped': True, 'attacking_groups': ('any',),
                  'copy_of': effect['copy_of'], 'copy_snapshot': dict(effect['copy_snapshot'])}
    elif operation in {"choose_attacking_token_destinations", 'choose_fixed_attacking_token_destinations'}:
        quantity = effect.get("quantity")
        if type(quantity) is not int or quantity < 0:
            return ()
        common = {
            "name": str(effect.get("name") or ""),
            "quantity": quantity,
            "tapped": effect.get('tapped', True),
            "attacking_groups": (tuple("any" for _ in range(quantity)) if operation == 'choose_attacking_token_destinations'
                or (self.state.phase == 'combat' and self.state.active_player == actor) else ()),
            "characteristics": dict(
                effect.get("characteristics") or {}
            ),
        }
    elif (
        operation == "choose_myriad_token_destinations"
        and effect.get("stage") == "destinations"
    ):
        opponents = effect.get("opponents")
        if not isinstance(opponents, (list, tuple)):
            return ()
        groups = tuple(str(value) for value in opponents)
        common = {
            "name": "",
            "quantity": len(groups),
            "tapped": True,
            "attacking_groups": groups,
            "copy_of": str(effect.get("copy_of") or ""),
            "copy_snapshot": (
                dict(effect["copy_snapshot"])
                if isinstance(effect.get("copy_snapshot"), Mapping)
                else None
            ),
        }
    else:
        return ()
    selections = effect.get("_replacement_selections", ())
    if not isinstance(selections, (list, tuple)):
        return ()
    resolved = preview_token_creation(
        self,
        actor,
        **common,
        replacement_selections=tuple(selections),
    )
    return tuple(FrozenMap(value) for value in resolved.specs)


def attached_token_candidate_refs(
    host, effect: Mapping[str, Any], *, attachment_kind: str = "equipment"
) -> tuple[str, ...]:
    """Materialize exact current legal recipients from the committed result."""
    from ..effect_runtime.zones_and_attachments import _attachment_target_is_legal

    refs, source_ref = effect.get("cards"), effect.get("source")
    if not isinstance(refs, (list, tuple)) or any(type(ref) is not str for ref in refs) or type(source_ref) is not str:
        return ()
    battlefield = {
        card.ref: card for card in host.state.cards.values()
        if card.zone == "battlefield" and not card.phased_out
    }
    source = battlefield.get(source_ref)
    if source is None:
        return ()
    return tuple(
        ref for ref in refs
        if ref in battlefield and battlefield[ref].is_token
        and _attachment_target_is_legal(host, source, battlefield[ref], attachment_kind=attachment_kind)
    )


def token_creation_choice_facts(host, actor, effect):
    return {"materialized_token_creation_preview": host._semantic_choice_token_creation_preview(actor, effect),
        "attacking_token_entry_allowed": host.state.phase == "combat" and host.state.active_player == actor}

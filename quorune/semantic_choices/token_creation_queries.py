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
    if operation == 'create_role_token':
        common = {'name': str(effect.get('name') or ''), 'quantity': 1, 'attacking_groups': (),
            'characteristics': dict(effect.get('characteristics') or {})}
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


def token_creation_choice_facts(host, actor, effect):
    return {"materialized_token_creation_preview": host._semantic_choice_token_creation_preview(actor, effect),
        "attacking_token_entry_allowed": host.state.phase == "combat" and host.state.active_player == actor}

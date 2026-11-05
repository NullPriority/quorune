"""Canonical token-creation intent bridge with retained legacy aftercare."""
from __future__ import annotations
from typing import Any
from ..replacement.immutable import thaw_value
from ..semantic_runtime.intents import CreateTokenIntent
from ..trigger_processing import schedule_delayed_trigger


def execute_token_creation_intent(
    self,
    intent: CreateTokenIntent,
) -> tuple[str, ...]:
    creator = self.create_token
    if intent.token_cleanup is not None:
        from functools import partial
        from ..token_creation import create_tokens
        creator = partial(create_tokens, self, token_cleanup=intent.token_cleanup)
    created_refs = tuple(
        creator(
            intent.controller,
            name=intent.name,
            quantity=intent.quantity,
            copy_of=intent.copy_of,
            copy_snapshot=(
                thaw_value(intent.copy_snapshot)
                if intent.copy_snapshot is not None
                else None
            ),
            characteristics=thaw_value(intent.characteristics),
            temporary_keywords=intent.temporary_keywords,
            tapped=intent.tapped,
            attacking=(
                intent.attacking_assignments
                if intent.attacking_assignments
                else None
            ),
            attacking_groups=intent.attacking_groups,
            reason=intent.reason,
            replacement_selections=intent.replacement_selections,
        )
    )
    if not (
        intent.sacrifice_at_end_step
        or intent.sacrifice_on_controller_end_step
        or intent.exile_at_end_of_combat
    ):
        return created_refs
    for created_ref in created_refs:
        created = self._resolve_object(
            intent.actor,
            created_ref,
            zones={"battlefield"},
            controlled_only=True,
        )
        condition: dict[str, Any] = (
            {"phase": "combat", "step": "end_combat"}
            if intent.exile_at_end_of_combat
            else {"phase": "ending", "step": "end_step"}
        )
        if intent.sacrifice_on_controller_end_step:
            condition["player"] = "controller"
        schedule_delayed_trigger(
            self,
            controller=intent.controller,
            label=(
                f"Exile {created.ref}"
                if intent.exile_at_end_of_combat
                else f"Sacrifice {created.ref}"
            ),
            event_kind="step.begin",
            condition=condition,
            stack_template={
                "label": (
                    f"Myriad — exile {created.ref}"
                    if intent.exile_at_end_of_combat
                    else f"Sacrifice {created.ref}"
                ),
                **(
                    {
                        "context": {
                            "dynamic_effects": [
                                {
                                    "op": "move_if_in_zone",
                                    "card": created.ref,
                                    "from": "battlefield",
                                    "destination": "exile",
                                    "expected_zone_change_counter": (
                                        created.zone_change_counter
                                    ),
                                    "expected_object_identity": (
                                        created.logical_object_id
                                    ),
                                }
                            ]
                        }
                    }
                    if intent.exile_at_end_of_combat
                    else {"semantic_key": "builtin:sacrifice-source"}
                ),
            },
            source_object_id=created.object_id,
            referred_object_ids=(created.object_id,),
            once=True,
        )
    return created_refs

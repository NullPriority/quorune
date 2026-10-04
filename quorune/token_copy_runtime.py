from __future__ import annotations

"""Copy recipe preparation and aftercare through existing token/zone owners."""

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .token_copy_recipes import TokenCopyRecipeError, TokenCopyRecipeSpec, apply_copy_exception
from .token_copy_references import copy_reference_snapshot


COPY_AFTERCARE = "token_copy_aftercare_v1"


def apply_copy_recipe(host: Any, effect: Mapping[str, Any], *, actor: str, reason: str) -> list[str]:
    recipe = TokenCopyRecipeSpec.from_dict(effect["copy_spec"])
    source = effect.get("_runtime_source")
    stack_ref = source.get("stack_ref") if isinstance(source, Mapping) else None
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    if item is None or item.controller != actor:
        raise TokenCopyRecipeError("Copy recipe requires its authoritative resolving stack item")
    reference = copy_reference_snapshot(host, item, recipe)
    snapshot = deepcopy(dict(reference["snapshot"]))
    modified = apply_copy_exception(snapshot["characteristics"], recipe.exception)
    snapshot["characteristics"] = modified
    snapshot["annotations"]["copy_overrides"] = deepcopy(modified)
    if recipe.cleanup != "none" or recipe.temporary_keywords:
        snapshot["annotations"][COPY_AFTERCARE] = {"cleanup": recipe.cleanup, "controller": actor,
            "binding": f"{item.stack_id}:token-copy", "keywords": list(recipe.temporary_keywords),
            "keyword_duration": recipe.keyword_duration, "source": dict(source)}
    from .token_creation import create_tokens
    return create_tokens(host, actor, name="", quantity=effect["quantity"],
        copy_of=reference["ref"], copy_snapshot=snapshot, copy_source_zone=reference["zone"],
        tapped=effect.get("tapped", False),
        reason=reason, replacement_selections=tuple(effect.get("_replacement_selections") or ()))


def finish_copy_aftercare(host: Any, controller: str, created: Sequence[str]) -> None:
    from .trigger_processing import schedule_delayed_trigger
    groups: dict[str, tuple[Mapping[str, Any], list[Any]]] = {}
    for object_id in created:
        card = host.state.cards[object_id]
        metadata = card.annotations.pop(COPY_AFTERCARE, None)
        if metadata is None:
            continue
        if not isinstance(metadata, Mapping) or set(metadata) != {"cleanup", "controller", "binding", "keywords", "keyword_duration", "source"} or metadata["controller"] != controller:
            raise TokenCopyRecipeError("Copy aftercare metadata is malformed")
        group = groups.setdefault(metadata["binding"], (metadata, []))
        group[1].append(card)
    for metadata, cards in groups.values():
        if metadata["keywords"]:
            from .continuous_effects import ContinuousEffectDuration, ContinuousOperation, Layer
            from .continuous_effect_state import ResolutionEffectSource, create_resolution_continuous_effect
            create_resolution_continuous_effect(host, source=ResolutionEffectSource(**metadata["source"]),
                targets=cards, layer=Layer.ABILITY, sublayer="6",
                operations=tuple(ContinuousOperation("add_ability", keyword) for keyword in metadata["keywords"]),
                duration=ContinuousEffectDuration(metadata["keyword_duration"]))
        if metadata["cleanup"] == "none":
            continue
        sacrifice = metadata["cleanup"] == "sacrifice_next_end_step"
        if metadata["cleanup"] not in {"sacrifice_next_end_step", "exile_next_end_step"}:
            raise TokenCopyRecipeError("Copy aftercare lifecycle is unsupported")
        movement = {"op": "move_if_in_zone", "cards": [{"card": card.ref,
            "expected_zone_change_counter": card.zone_change_counter,
            "expected_object_identity": card.logical_object_id} for card in cards],
            "from": "battlefield", "destination": "graveyard" if sacrifice else "exile",
            **({"transition_kind": "sacrifice", "required_controller": controller} if sacrifice else {})}
        schedule_delayed_trigger(host, controller=controller, label="Copied token cleanup",
            event_kind="step.begin", condition={"phase": "ending", "step": "end_step"},
            stack_template={"label": "Copied token cleanup", "context": {"dynamic_effects": [movement]}},
            referred_object_ids=tuple(card.object_id for card in cards), once=True)

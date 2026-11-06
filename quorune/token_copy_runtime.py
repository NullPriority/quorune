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
    if recipe.creation_origin=='embalm':
        from .conditional_copy_model import EMBALMED_DESIGNATION
        snapshot['annotations'][EMBALMED_DESIGNATION]=True
    if recipe.cleanup != "none" or recipe.temporary_keywords:
        snapshot["annotations"][COPY_AFTERCARE] = {"cleanup": recipe.cleanup, "controller": actor,
            "binding": f"{item.stack_id}:token-copy", "keywords": list(recipe.temporary_keywords),
            "keyword_duration": recipe.keyword_duration, "source": dict(source)}
    from .copied_token_aura_entry import copied_aura_token_selections, clear_copied_aura_token_selections
    from .token_creation import create_tokens
    created = create_tokens(host, actor, name="", quantity=effect["quantity"],
        copy_of=reference["ref"], copy_snapshot=snapshot, copy_source_zone=reference["zone"],
        tapped=effect.get("tapped", False),
        reason=reason, replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        copied_aura_selections=copied_aura_token_selections(item))
    clear_copied_aura_token_selections(item)
    return created


def finish_copy_aftercare(host: Any, controller: str, created: Sequence[str]) -> None:
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
        if metadata["cleanup"] not in {"sacrifice_next_end_step", "exile_next_end_step"}:
            raise TokenCopyRecipeError("Copy aftercare lifecycle is unsupported")
        from .created_token_cleanup_model import CreatedTokenCleanupSpec
        from .created_token_cleanup import schedule_created_token_cleanup
        schedule_created_token_cleanup(host, controller, cards,
            CreatedTokenCleanupSpec('sacrifice' if metadata['cleanup'] == 'sacrifice_next_end_step' else 'exile', 'next_end_step'),
            label='Copied token cleanup')

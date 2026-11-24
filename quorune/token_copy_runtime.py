from __future__ import annotations

"""Copy recipe preparation and aftercare through existing token/zone owners."""

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .token_copy_recipes import TokenCopyBatchSpec, TokenCopyRecipeError, TokenCopyRecipeSpec, apply_copy_exception
from .token_copy_recipient_model import has_occurrence_copy_recipient
from .token_copy_references import copy_reference_snapshot


COPY_AFTERCARE = "token_copy_aftercare_v1"


def apply_copy_recipe(host: Any, effect: Mapping[str, Any], *, actor: str, reason: str) -> list[str]:
    recipe = TokenCopyRecipeSpec.from_dict(effect["copy_spec"])
    source = effect.get("_runtime_source")
    stack_ref = source.get("stack_ref") if isinstance(source, Mapping) else None
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    if item is None or item.controller != actor:
        raise TokenCopyRecipeError("Copy recipe requires its authoritative resolving stack item")
    recipient = actor
    occurrence_copy = has_occurrence_copy_recipient(effect)
    if occurrence_copy:
        from .rules.token_copy_recipients import resolved_occurrence_copy_recipient

        recipient = resolved_occurrence_copy_recipient(host, effect, item)
        if recipient not in host.active_seats:
            return []
    elif 'copy_recipient_binding' in effect:
        from .rules.token_copy_recipients import resolved_copy_recipient
        recipient = resolved_copy_recipient(effect, item)
        if recipient is None:return []
        effect = {**effect, '_copy_target': {'schema_version': 1, 'index': 1}}
    target=effect.get('_copy_target')
    if target is not None and (not isinstance(target,Mapping) or set(target)!={'schema_version','index'}
            or type(target['schema_version']) is not int or target['schema_version']!=1
            or recipe.origin!='target' or type(target['index']) is not int or not 0<=target['index']<len(item.targets)):
        raise TokenCopyRecipeError('Modal copy target lost its bound announcement slot')
    if target is not None and item.targets[target['index']] is None:return []
    reference = copy_reference_snapshot(host, item, recipe, target_index=None if target is None else target['index'],
                                        use_phased_lki=occurrence_copy)
    if reference is None:
        return []
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
    created = create_tokens(host, recipient, name="", quantity=effect["quantity"],
        copy_of=reference["ref"], copy_snapshot=snapshot, copy_source_zone=reference["zone"],
        tapped=effect.get("tapped", False),
        reason=reason, replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        copied_aura_selections=copied_aura_token_selections(item))
    clear_copied_aura_token_selections(item)
    return created


def apply_copy_batch(host: Any, effect: Mapping[str, Any], *, actor: str, reason: str) -> list[str]:
    from .continuous_effect_state import matching_battlefield_objects
    from .copied_token_aura_entry import copied_aura_token_selections, clear_copied_aura_token_selections
    from .token_creation import _create_token_specs

    spec = TokenCopyBatchSpec.from_effect(effect, actor=actor)
    source = effect.get("_runtime_source")
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.controller != actor or item.context.get("currently_resolving") is not True
            or not isinstance(source, Mapping) or source.get("stack_ref") != item.ref):
        raise TokenCopyRecipeError("Copy batch requires its current resolving stack item")
    if spec.uses_announced_models:
        if item.context.get("targets_revalidated") is not True:
            raise TokenCopyRecipeError("Announced copy batch requires its revalidated targets")
        models = tuple(host._resolve_object(actor, ref, zones={"battlefield"})
                       for ref in item.targets if ref is not None)
    else:
        if spec.targets_player:
            if len(item.targets) != 1 or item.targets[0] not in host.active_seats:
                raise TokenCopyRecipeError("Copy batch requires its one current player target")
            player = item.targets[0]
        else:
            if item.targets:
                raise TokenCopyRecipeError("Controller's model set requires an untargeted instruction")
            player = actor
        models = matching_battlefield_objects(host, spec.model_query(player))
    if not models:
        return []
    tokens, card_types, subtypes, sources = _prepare_copy_batch(host, actor, models, spec.exception)
    created = _create_token_specs(
        host, actor, token_specs=tokens, created_types=card_types, created_subtypes=subtypes,
        replacement_sources=sources, base_name="", base_quantity=len(tokens), reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        copied_aura_selections=copied_aura_token_selections(item),
    )
    clear_copied_aura_token_selections(item)
    return created


def _prepare_copy_batch(host, actor, models, exception):
    from .token_creation import _token_specification_inputs, token_copy_snapshot

    tokens = []
    card_types: set[str] = set()
    subtypes: set[str] = set()
    sources: dict[str, Any] = {}
    for model in models:
        snapshot = token_copy_snapshot(host, model)
        if exception.remove_legendary:
            values = apply_copy_exception(snapshot["characteristics"], exception)
            snapshot["characteristics"] = values
            snapshot["annotations"]["copy_overrides"] = values
        specs, model_types, model_subtypes, model_sources = _token_specification_inputs(
            host, actor, name="", quantity=1, tapped=False, attacking=None, attacking_groups=(),
            battle_protector=None, copy_of=model.ref, copy_source_zone="battlefield",
            copy_snapshot=snapshot, characteristics=None, temporary_keywords=(), aura_target_ref=None,
        )
        tokens.extend(specs)
        card_types.update(model_types)
        subtypes.update(model_subtypes)
        sources.update((source.object_id, source) for source in model_sources)
    return tuple(tokens), card_types, subtypes, tuple(sources.values())


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

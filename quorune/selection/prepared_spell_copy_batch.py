"""Keep an already prepared simultaneous set of stack-copy intents together."""

from dataclasses import replace

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.event_spell_copy import resolve_event_spell_copy_source
from ..semantic_runtime.intents import CopyStackItemIntent
from ..rules.casting.spell_text import CAST_TEXT_COPY_CHARACTERISTICS
from ..storm import spell_copy_template
from ..target_source import spell_copy_targeting_source
from .copy_targets import prepare_copy_targets


def prepared_intent_copy_spell(host, intents):
    if not intents or not all(isinstance(intent, CopyStackItemIntent) for intent in intents):
        return None
    first = intents[0]
    if any(
        (intent.controller, intent.target_stack_ref, intent.copy_source, intent.permanent_copy)
        != (first.controller, first.target_stack_ref, first.copy_source, first.permanent_copy)
        for intent in intents
    ):
        return None
    bound = resolve_event_spell_copy_source(host, {
        "stack": first.target_stack_ref, "copy_source": first.copy_source,
    }) if first.copy_source is not None else None
    target = bound.item() if bound is not None else next(
        (item for item in host.state.stack if item.ref == first.target_stack_ref), None,
    )
    if target is None or target.kind not in {"spell", "spell_copy"}:
        return None
    program = host.semantics.get(target.semantic_key) if target.semantic_key else None
    if bound is not None:
        return {
            "controller": first.controller, "target_schema": host._stack_target_schema(target, program),
            "modes": list(target.modes), "targets": list(target.targets),
            "target_groups": target.context.get("target_groups", {}),
            "target_snapshots": target.context.get("target_snapshots", {}),
            CAST_TEXT_COPY_CHARACTERISTICS: thaw_value(bound.characteristics),
        }
    return {**spell_copy_template(host, target, program), "controller": first.controller}


def ordered_copy_intents(host, item, originals, template, roles, target_answers, *, retarget_originals=False):
    """Retain prepared original assignments while lowering only new targets."""
    first = originals[0]
    result = []
    for label, submitted in zip(roles, target_answers, strict=True):
        role, _, index = label.partition(":")
        if role == "original":
            original = originals[int(index)]
            if not retarget_originals:
                if submitted not in (None, list(original.targets)):
                    raise ValueError("The original spell copy already has its chosen targets")
                result.append(original)
                continue
        elif role == "additional" and index:
            original = first
        else:
            raise ValueError("A copy placement role is malformed")
        prepared = prepare_copy_targets(
            host, actor=original.controller, schema=template["target_schema"],
            modes=template["modes"], source_ref=item.ref, original_targets=template["targets"],
            original_groups=template["target_groups"], original_snapshots=template["target_snapshots"],
            submitted=submitted, acting_source=spell_copy_targeting_source(host, template),
        )
        result.append(replace(
            original, targets=prepared.targets, target_groups=prepared.groups,
            target_snapshots=prepared.snapshots,
        ))
    return tuple(result)


def read_prepared_spell_copy_batch(host, item, saved, controller):
    from ..errors import GameRuleError
    from ..semantic_choices.completed_choice import decode_remaining_completion

    if set(saved) != {"completion", "retarget_originals"} or type(saved["retarget_originals"]) is not bool:
        raise GameRuleError("A prepared copy batch requires its immutable target policy")
    continuation, _, _, completion = decode_remaining_completion(saved["completion"])
    if continuation.stack_ref != item.ref or completion is None or not completion.intents:
        raise GameRuleError("The prepared spell-copy request lost its original resolution")
    host._validate_semantic_frame(continuation.semantic_frame.to_dict(), item)
    template = prepared_intent_copy_spell(host, completion.intents)
    if template is None or completion.intents[0].controller != controller:
        raise GameRuleError("The prepared spell-copy request changed its original source")
    return completion, {**template, "commander_copy_new_targets": saved["retarget_originals"]}


def begin_prepared_spell_copy_batch(host, item, continuation, actor, response, completion, *, retarget_originals=False):
    from ..rules.additional_spell_copy import additional_spell_copy_sources
    from ..semantic_choices.completed_choice import encode_remaining_completion
    from .storm_copy_replacements import _advance

    intents = completion.intents
    if not intents or not isinstance(intents[0], CopyStackItemIntent):
        return False
    sources = additional_spell_copy_sources(host, intents[0].controller)
    if not sources:
        return False
    template = prepared_intent_copy_spell(host, intents)
    if template is None:
        return False
    saved = {"completion": encode_remaining_completion(continuation, actor, response, completion, 0),
             "retarget_originals": retarget_originals}
    _advance(host, item, len(intents), {**template, "commander_copy_new_targets": retarget_originals}, sources, prepared=saved)
    return True


def commit_prepared_spell_copy_batch(host, item, saved, template, roles, target_answers):
    from ..semantic_choices.completed_choice import decode_remaining_completion
    from ..semantic_choices.counter_coordination import continue_semantic_completion

    continuation, actor, response, completion = decode_remaining_completion(saved["completion"])
    ordered = ordered_copy_intents(
        host, item, completion.intents, template, roles, target_answers,
        retarget_originals=saved["retarget_originals"],
    )
    return continue_semantic_completion(
        host, item=item, continuation=continuation, actor=actor, response=response,
        completion=replace(completion, intents=ordered), copy_replacements_applied=True,
    )



def begin_repeated_spell_copy_batch(host, actor, continuation, preparation):
    """Batch identical copies produced by a closed registered spell-copy producer."""
    from ..rules.additional_spell_copy import additional_spell_copy_sources
    from ..semantic_choices.model import SemanticChoiceCompletion

    effects = preparation.auto_continue.prepend_effects
    if not effects or not additional_spell_copy_sources(host, actor):
        return False
    effect = effects[0]
    if (set(effect) != {"op", "player", "stack", "copy_source"} or effect["op"] != "copy_stack_item"
            or effect["player"] != actor or any(effect != value for value in effects)):
        return False
    item = next((row for row in host.state.stack if row.ref == continuation.stack_ref), None)
    if item is None:
        return False
    bound = resolve_event_spell_copy_source(host, effect)
    original = bound.item()
    if original.kind not in {"spell", "spell_copy"}:
        return False
    intent = CopyStackItemIntent(
        actor=actor, controller=actor, target_stack_ref=original.ref, targets=tuple(original.targets),
        target_groups=FrozenMap(original.context.get("target_groups", {})), reason=item.label,
        target_snapshots=FrozenMap(original.context.get("target_snapshots", {})), copy_source=effect["copy_source"],
    )
    return begin_prepared_spell_copy_batch(
        host, item, continuation, actor, {}, SemanticChoiceCompletion(intents=(intent,) * len(effects)),
        retarget_originals=True,
    )

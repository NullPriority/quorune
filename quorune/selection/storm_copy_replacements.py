"""Prepare replacement, placement and target choices for an intrinsic spell-copy batch."""

from ..errors import GameRuleError
from ..replacement import replacement_choice_payload
from ..replacement.immutable import thaw_value
from ..rules.additional_spell_copy import additional_spell_copy_sources
from ..replacement.spell_copy_occurrence import SpellCopyOccurrence, ordered_spell_copy_roles
from ..storm import validated_spell_copy_trigger
from ..target_source import spell_copy_targeting_source
from .copy_targets import copy_target_public_schema

CONTINUATION = "spell_copy_replacement"


def _issue(host, item, count, sources, stage, selections, order, payload, schema, *, actor, prepared):
    saved = {"version": 1, "stack": item.ref, "stage": stage,
             "selections": list(selections), "order": list(order),
             "original_count": count, "sources": [source.effect_id for source in sources]}
    if prepared is not None:
        saved["prepared"] = prepared
    host.permissions.issue(
        kind="semantic.storm", role="pilot", actors=[actor], allowed_actions=["choose"],
        payload_by_actor={actor: {
            "stack": item.ref, **payload,
            "legal_actions": [{"id": "choose", "action": "choose", "choice_schema": schema}],
        }},
        continuation={CONTINUATION: saved},
    )


def _advance(host, item, count, template, sources, selections=(), order=(), targets=None, *, prepared=None):
    actor = sources[0].controller if sources else item.controller
    if not sources or count < 1:
        raise GameRuleError("The spell-copy replacement batch lost its applicable sources")
    event = SpellCopyOccurrence(f"stack.copy.spell:{item.ref}:{actor}", actor, count)
    progress = event.advance(sources, host.apnap_order(), selections)
    if progress.pending is not None:
        payload = replacement_choice_payload(progress.pending, (source.effect() for source in sources))
        _issue(host, item, count, sources, "replacement", selections, (), {
            **payload, "prompt": "Choose the next spell-copy replacement to apply.",
        }, {"field": "choice", "legal_values": list(progress.pending.choice.legal_selections)}, actor=actor, prepared=prepared)
        return
    resolved = progress.batch.events[0]
    if not order:
        roles = [*(f"original:{i}" for i in range(count)), *(f"additional:{i}" for i in resolved.applied_effects)]
        _issue(host, item, count, sources, "order", selections, (), {"prompt": "Order the copies from bottom to top."},
               {"field": "order", "legal_refs": roles, "minimum": len(roles), "maximum": len(roles), "distinct": True},
               actor=actor, prepared=prepared)
        return
    labels = ordered_spell_copy_roles(count, resolved.applied_effects, order)
    if resolved.payload["count"] != len(labels):
        raise GameRuleError("The ordered copies do not match the replacement result")
    target_schema = template.get("target_schema")
    if targets is None and target_schema and template.get("targets"):
        schema = copy_target_public_schema(
            host, actor, target_schema, list(template.get("modes") or []),
            list(template["targets"]), dict(template.get("target_groups") or {}), item.ref,
            acting_source=spell_copy_targeting_source(host, template),
            original_snapshots=template.get("target_snapshots"),
        )
        _issue(host, item, count, sources, "targets", selections, labels, {
            "prompt": "Choose targets for each retargetable copy or retain its defaults.",
            "copies": _copy_target_rows(labels, template, schema, prepared),
        }, {"field": "copy_targets", "copy_count": len(labels), "may_keep_default": True,
            "may_retain_each_default": True}, actor=actor, prepared=prepared)
        return
    if targets is None:
        targets = [None] * len(labels)
    if not isinstance(targets, list) or len(targets) != len(labels):
        raise GameRuleError("Submit one target assignment per ordered spell copy")
    if prepared is not None:
        from .prepared_spell_copy_batch import commit_prepared_spell_copy_batch

        commit_prepared_spell_copy_batch(host, item, prepared, template, labels, targets)
        return
    defaults = list(template.get("targets") or [])
    assignments = []
    for label, submitted in zip(labels, targets, strict=True):
        if not _can_retarget(template, label):
            if submitted not in (None, defaults):
                raise GameRuleError("The original copy must retain its targets")
            submitted = None
        assignments.append(submitted)
    from .storm import StormCompletionContext

    context = StormCompletionContext(item.controller, {"copy_targets": assignments}, item, len(labels), template)
    copies = host._build_storm_copies(context)
    host._commit_storm_copies(context, copies)


def _copy_target_rows(labels, template, schema, prepared):
    fixed = (prepared["completion"]["intents"] if prepared is not None
             and not prepared["retarget_originals"] else ())
    result = []
    for index, label in enumerate(labels):
        can_change = _can_retarget(template, label)
        if fixed and label.startswith("original:"):
            original = fixed[int(label.removeprefix("original:"))]["fields"]
            defaults, groups = original["targets"], original["target_groups"]
        else:
            defaults, groups = template["targets"], template.get("target_groups") or {}
        result.append({"copy_index": index, "copy_role": label, "may_retarget": can_change,
                       "default_targets": list(defaults), "default_target_groups": groups,
                       "modes": list(template.get("modes") or []),
                       "target_schema": schema if can_change else {}})
    return result


def _can_retarget(template, label):
    return label.startswith("additional:") or template.get("commander_copy_new_targets") is not False


def begin_replaced_storm_batch(host, item, count, template):
    sources = additional_spell_copy_sources(host, item.controller)
    if not sources:
        return False
    _advance(host, item, count, template, sources)
    return True


def complete_replaced_storm_batch(host, decision):
    saved = thaw_value(decision.continuation[CONTINUATION])
    fields = {"version", "stack", "stage", "selections", "order", "original_count", "sources"}
    prepared = saved.get("prepared")
    if (set(saved) != fields | ({"prepared"} if prepared is not None else set())
            or type(saved["version"]) is not int or saved["version"] != 1
            or type(saved["original_count"]) is not int
            or any(not isinstance(saved[field], list) or any(type(value) is not str for value in saved[field])
                   for field in ("selections", "order", "sources"))):
        raise GameRuleError("Malformed spell-copy replacement continuation")
    seat, = decision.actors
    item = next((row for row in host.state.stack if row.ref == saved["stack"]), None)
    if item is None:
        raise GameRuleError("The spell-copy replacement source left the stack")
    if prepared is None:
        if item.controller != seat:
            raise GameRuleError("The spell-copy replacement controller changed")
        count, template = validated_spell_copy_trigger(item, host)
    else:
        from .prepared_spell_copy_batch import read_prepared_spell_copy_batch

        completion, template = read_prepared_spell_copy_batch(host, item, prepared, seat)
        count = len(completion.intents)
    sources = additional_spell_copy_sources(host, seat)
    if count != saved["original_count"] or [source.effect_id for source in sources] != saved["sources"]:
        raise GameRuleError("The spell-copy replacement batch changed while its choice waited")
    response = decision.responses[seat]
    selections, order, targets = saved["selections"], saved["order"], None
    if saved["stage"] == "replacement" and not order:
        choice = response.get("choice")
        if type(choice) is not str:
            raise GameRuleError("Choose a current spell-copy replacement")
        selections = [*selections, choice]
    elif saved["stage"] == "order" and not order:
        order = response.get("order")
        if not isinstance(order, (tuple, list)) or not order:
            raise GameRuleError("Order all current spell copies")
    elif saved["stage"] == "targets" and order:
        targets = response.get("copy_targets", [None] * len(order))
    else:
        raise GameRuleError("The spell-copy replacement continuation changed stage")
    try:
        _advance(host, item, count, template, sources, selections, order, targets, prepared=prepared)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc

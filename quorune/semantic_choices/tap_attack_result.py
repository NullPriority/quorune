"""Resolve a subtype tap choice against the original declared attack."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..attack_transition_model import AttackRecipientKind, AttackTransitionEvent
from ..damage_source import DamageSourceSnapshot
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.tap_attack_result import DEPENDENCIES, OPERATION, tap_attack_effect
from ..semantic_runtime.intents import SetPermanentsTappedIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_RETAINED = {"_candidate_incarnations", "_stack_label"}


def _bound(effect, actor):
    try:
        expected = tap_attack_effect(effect.get("subtype"))
        if (set(effect) - _RETAINED != set(expected)
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or effect["op"] != OPERATION or effect["player"] != actor):
            raise ValueError("Subtype attack payment has a changed actor or instruction")
        declaration = AttackTransitionEvent.from_dict(thaw_value(effect["declaration"]))
        terms = effect["damage_template"]
        snapshot = DamageSourceSnapshot.from_dict(thaw_value(terms["source_snapshot"]))
        assignment = next(row for row in declaration.assignments if row.attacker_object_id == snapshot.object_id)
        if declaration.active_player != actor or snapshot.zone != "battlefield":
            raise ValueError("Subtype attack payment lost its original declaration")
        if FrozenMap(terms) != FrozenMap({
            "op":"damage", "source":snapshot.ref, "target":assignment.recipient.reference,
            "amount":1, "source_snapshot":snapshot.to_dict(),
        }):
            raise ValueError("Attack damage no longer matches its declared source and defender")
        return snapshot, assignment.recipient
    except (StopIteration, KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc) or "The attack declaration lost its original source") from exc


def _eligible(row, actor, subtype):
    return row is not None and row.controller == actor and not row.tapped and not row.phased_out and subtype in row.subtypes


def _result_effects(query, source, recipient, amount):
    outputs = []
    current = query.object(source.ref, zones=("battlefield",))
    if current is not None and not current.phased_out and current.logical_object_id == source.logical_object_id:
        outputs.append(FrozenMap({"op":"modify_stats_until_end_of_turn","card":current.ref,"power":amount,"toughness":0}))
    if source.permanent_state is not None and not source.permanent_state.attacking:
        return tuple(outputs)
    if recipient.kind is AttackRecipientKind.PLAYER:
        legal = recipient.reference in query.active_seats
    elif recipient.kind is AttackRecipientKind.PLANESWALKER:
        defender = query.object(recipient.reference, zones=("battlefield",))
        legal = bool(defender and not defender.phased_out and "planeswalker" in defender.types
                     and defender.logical_object_id == recipient.logical_object_id)
    else:
        legal = False
    if legal:
        outputs.append(FrozenMap({
            "op":"damage","source":source.ref,"target":recipient.reference,"amount":amount,
            "source_snapshot":source.to_dict(),
        }))
    return tuple(outputs)


@dataclass(frozen=True, slots=True)
class TapAttackResultChoiceHandler:
    operation: str = OPERATION
    handler_id: str = "choice.attack.subtype-tap-result.v1"
    schema_version: int = 1
    capability_dependencies: tuple[str, ...] = DEPENDENCIES
    rule_references: tuple[str, ...] = ("CR 508.1", "CR 603.3", "CR 608.2d", "CR 608.2h", "CR 613.1g")
    continuation_fields: tuple[str, ...] = (
        "schema_version","player","subtype","declaration","damage_template",
        "_candidate_incarnations","_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("objects","legal_actions","prompt")
    mutation_path: tuple[str, ...] = ("SetPermanentsTappedIntent","modify_stats_until_end_of_turn","damage")
    replay_fixture: str = "subtype-tap-for-attack"
    test_modules: tuple[str, ...] = ("tests.test_typed_trigger_participation",)

    def prepare(self, effect, context):
        source, _recipient = _bound(effect, context.actor)
        if (context.actor != context.stack_controller or (context.source_object_id is not None
                and context.source_object_id != source.object_id)):
            raise SemanticChoiceError("Subtype attack payment belongs to its original source and resolving controller")
        rows = sorted(
            (row for row in context.query.objects(zones=("battlefield",), controller=context.actor)
             if _eligible(row, context.actor, effect["subtype"])),key=lambda row:row.ref,
        )
        if not rows:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="No eligible untapped permanents."))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Choose how many untapped {effect['subtype'].title()} to tap.",
                choice=ObjectChoice(field_name="objects",legal_refs=tuple(row.ref for row in rows),
                    zones=("battlefield",),minimum=0,maximum=len(rows),optional=True),
                public_context=FrozenMap({"stack":context.stack_ref,
                    "objects":[{"id":row.ref,"name":row.printed_name} for row in rows]}),
            ),
            continuation_effect=FrozenMap({**effect,"_stack_label":context.stack_label,
                "_candidate_incarnations":{row.ref:row.logical_object_id for row in rows}}),
        )

    def complete(self, continuation, response, query):
        actor = continuation.semantic_frame.controller
        effect = continuation.effect
        source, recipient = _bound(effect, actor)
        values = response.get("objects", ())
        candidates = effect["_candidate_incarnations"]
        if (not isinstance(candidates, Mapping) or not isinstance(values, (tuple,list))
                or any(type(ref) is not str or ref not in candidates for ref in values)
                or len(values) != len(set(values))):
            raise SemanticChoiceError("Choose a distinct subset of the offered objects")
        for ref in values:
            row = query.object(ref, zones=("battlefield",))
            if not _eligible(row, actor, effect["subtype"]) or row.logical_object_id != candidates[ref]:
                raise SemanticChoiceError("An attack payment object is stale or ineligible")
        if not values:
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(
            intents=(SetPermanentsTappedIntent(tuple(values),actor,True,effect["_stack_label"]),),
            prepend_effects=_result_effects(query,source,recipient,len(values)),
        )

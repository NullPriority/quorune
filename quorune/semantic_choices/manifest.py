from __future__ import annotations

"""Principal-private selection and sequential Manifest entry continuation."""

from dataclasses import dataclass

from ..manifest import (
    MANIFEST_ATTACHMENT_RESULT, MANIFEST_ATTACHMENT_VERSION, MANIFEST_CAPABILITY, MANIFEST_COUNTERS_CAPABILITY,
    MANIFEST_COUNTERS_FIELD, MANIFEST_COUNTERS_OPERATION,
)
from ..replacement.immutable import FrozenMap
from ..rules.manifest_shapes import validate_manifest_instruction
from ..semantic_runtime.domain_intent import DomainEffectIntent
from ..semantic_runtime.intents import PlaceCountersIntent, RevealLibraryCardsIntent
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest)


def _manifest_intent(ref, incarnation, actor, reason):
    return ZoneMoveIntent(actor=actor, object_ref=ref, expected_zones=('library',),
        destination='battlefield', new_controller=actor, reason=reason,
        expected_zone_change_counter=int(incarnation.rsplit('@', 1)[1]), manifest_entry=True)


def _manifest_result_incarnation(row, intent):
    object_id = row.logical_object_id.rpartition("@")[0]
    return f"{object_id}@{intent.expected_zone_change_counter + 1}"


def _manifest_counter_effects(effect, row, intent):
    if MANIFEST_COUNTERS_FIELD not in effect or effect[MANIFEST_COUNTERS_FIELD]["count"] == 0:
        return ()
    return (FrozenMap({
        "op": MANIFEST_COUNTERS_OPERATION, "player": intent.actor,
        "card": row.ref, "incarnation": _manifest_result_incarnation(row, intent),
        "count": effect[MANIFEST_COUNTERS_FIELD]["count"],
    }),)


def _prepare_top_two(effect, context):
    payload = dict(effect)
    entries = payload.pop("_manifest_top_entries", None)
    try:
        validate_manifest_instruction(payload, context.actor, resolved=True)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if "_manifest_top_entries" not in effect:
        refs = context.query.library_refs(context.actor, top_first=True)[:2]
        rows = tuple(context.query.object(ref, zones=("library",)) for ref in refs)
        if any(row is None for row in rows):
            raise SemanticChoiceError("Manifest library snapshot is unavailable")
        entries = tuple((row.ref, row.logical_object_id) for row in rows)
    if (not isinstance(entries, (list, tuple)) or len(entries) > 2
            or any(not isinstance(entry, (list, tuple)) or len(entry) != 2
                   or any(type(value) is not str or not value for value in entry)
                   or not entry[1].rpartition("@")[0] or not entry[1].rpartition("@")[2].isdecimal()
                   for entry in entries)
            or len(entries) != len({entry[0] for entry in entries})):
        raise SemanticChoiceError("Manifest needs the original ordered top two library cards")
    moves = tuple(_manifest_intent(ref, identity, context.actor, context.stack_label) for ref, identity in entries)
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap({**payload, "_manifest_top_entries": entries}),
        preparation_intents=moves, auto_continue=AutoContinue(reason="Manifest the original top library cards in order."),
    )


@dataclass(frozen=True, slots=True)
class ManifestChoiceHandler:
    operation: str = 'manifest_card'
    handler_id: str = 'choice.manifest.controller.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 701.40', 'CR 701.62a', 'CR 708.2', 'CR 708.5')
    capability_dependencies: tuple[str, ...] = (MANIFEST_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        'player', 'mode', '_choice_actor', '_looked_refs', '_looked_incarnations',
        MANIFEST_ATTACHMENT_VERSION, '_equipment_incarnation', MANIFEST_COUNTERS_FIELD, '_manifest_top_entries',
    )
    private_data: tuple[str, ...] = ('actor top library identities',)
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('RevealLibraryCardsIntent', 'ZoneMoveIntent manifested entry then remainder graveyard')
    replay_fixture: str = 'manifest-controller-private-choice'
    test_modules: tuple[str, ...] = ('tests.test_manifest',)

    def prepare(self, effect, context):
        if effect.get("mode") == "top_two":
            return _prepare_top_two(effect, context)
        try:
            attaches = validate_manifest_instruction(effect, context.actor, resolved=True)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        refs = context.query.library_refs(context.actor, top_first=True)[:2 if effect['mode'] == 'dread' else 1]
        if not refs:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='empty library manifests no card'))
        rows = tuple(context.query.object(ref, zones=('library',)) for ref in refs)
        if any(row is None for row in rows):
            raise SemanticChoiceError('Manifest library snapshot is unavailable')
        if effect['mode'] == 'top':
            row, = rows
            move = _manifest_intent(row.ref, row.logical_object_id, context.actor, context.stack_label)
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=(move,), auto_continue=AutoContinue(reason='manifest the original top library card',
                    prepend_effects=_manifest_counter_effects(effect, row, move)))
        choice = ObjectChoice(field_name='card', legal_refs=refs, zones=('library',), minimum=1, maximum=1,
            visibility='actor_private')
        return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Choose a card to manifest.', choice=choice,
            public_context=FrozenMap({'stack': context.stack_ref, 'objects': [
                {'id': row.ref, 'name': row.printed_name} for row in rows]})),
            continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                '_looked_refs': refs, '_looked_incarnations': [row.logical_object_id for row in rows],
                **({'_equipment_incarnation': context.source_logical_object_id} if attaches else {})}),
            preparation_intents=(RevealLibraryCardsIntent(actor=context.actor, player=context.actor,
                viewer=context.actor, refs_top_first=refs, reason=context.stack_label),))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = str(effect['_choice_actor'])
        refs = tuple(effect['_looked_refs'])
        submitted = response.get('card')
        if isinstance(submitted, (list, tuple)) and len(submitted) == 1:
            submitted = submitted[0]
        if submitted not in refs:
            raise SemanticChoiceError('Manifest selection must be one original looked-at card')
        rows = tuple(query.object(ref, zones=('library',)) for ref in refs)
        if (any(row is None for row in rows) or tuple(row.logical_object_id for row in rows) != tuple(effect['_looked_incarnations'])
                or query.library_refs(actor, top_first=True)[:len(refs)] != refs):
            raise SemanticChoiceError('Manifest looked-at library incarnations changed')
        chosen = next(row for row in rows if row.ref == submitted)
        remainder = tuple(row for row in rows if row.ref != submitted)
        attaches = MANIFEST_ATTACHMENT_VERSION in effect
        counters = MANIFEST_COUNTERS_FIELD in effect
        reason = continuation.note or ("Manifest dread" if attaches or counters else "")
        entered = _manifest_intent(chosen.ref, chosen.logical_object_id, actor, reason)
        moves = (entered,
            *(ZoneMoveIntent(actor=actor, object_ref=row.ref, expected_zones=('library',), destination='graveyard',
                reason=reason, expected_zone_change_counter=int(row.logical_object_id.rsplit('@', 1)[1]))
                for row in remainder))
        if not attaches and not counters:
            return SemanticChoiceCompletion(intents=moves)
        fields = {'_choice_actor', '_looked_refs', '_looked_incarnations', '_equipment_incarnation'}
        try:
            validate_manifest_instruction(
                {key: value for key, value in effect.items() if key not in fields}, actor, resolved=True,
            )
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if counters:
            return SemanticChoiceCompletion(intents=moves, prepend_effects=_manifest_counter_effects(effect, chosen, entered))
        if '_equipment_incarnation' not in effect:
            raise SemanticChoiceError("Manifest attachment lost its source binding")
        source = effect['_equipment_incarnation']
        if source is None:
            return SemanticChoiceCompletion(intents=moves)
        source_id, _, counter = source.rpartition("@") if type(source) is str else ("", "", "")
        if not source_id or not counter.isdecimal():
            raise SemanticChoiceError("Manifest attachment source incarnation is malformed")
        target_id = chosen.logical_object_id.rpartition("@")[0]
        return SemanticChoiceCompletion(intents=(*moves, DomainEffectIntent(
            actor=actor, operation="attach", reason=reason, effect=FrozenMap({
                "op": "attach", "attachment_kind": "equipment", "source": source_id,
                "target": target_id, MANIFEST_ATTACHMENT_RESULT: {
                    "source": source, "target": _manifest_result_incarnation(chosen, entered),
                },
            }),
        )))


@dataclass(frozen=True, slots=True)
class ManifestCounterResultHandler:
    operation: str = MANIFEST_COUNTERS_OPERATION
    handler_id: str = "choice.manifest.result-counters.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("122.6", "608.2c", "701.40a", "701.62a")
    capability_dependencies: tuple[str, ...] = (MANIFEST_COUNTERS_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "card", "incarnation", "count")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("PlaceCountersIntent",)
    replay_fixture: str = "manifest-result-counters"
    test_modules: tuple[str, ...] = ("tests.test_manifest",)

    def prepare(self, effect, context):
        if (set(effect) != {"op", "player", "card", "incarnation", "count"}
                or effect["op"] != self.operation or effect["player"] != context.actor
                or any(type(effect[key]) is not str or not effect[key] for key in ("card", "incarnation"))
                or not effect["incarnation"].rpartition("@")[0]
                or not effect["incarnation"].rpartition("@")[2].isdecimal()
                or type(effect["count"]) is not int or effect["count"] <= 0):
            raise SemanticChoiceError("Manifest counter result requires its committed subject and count")
        row = context.query.object(effect["card"], zones=("battlefield",))
        intent = () if row is None or row.logical_object_id != effect["incarnation"] or row.phased_out else (
            PlaceCountersIntent(actor=context.actor, object_refs=(row.ref,), counter_name="+1/+1",
                                amount=effect["count"], reason=context.stack_label, source_ref=context.source_ref),
        )
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=intent,
            auto_continue=AutoContinue(reason="Apply counters to the committed Manifest result."),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("The Manifest counter result requires no player choice")


MANIFEST_CHOICE_HANDLERS = (ManifestChoiceHandler(), ManifestCounterResultHandler())

"""Actor-scoped, non-target card choice followed by the existing return owner."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from ..public_characteristic_aggregates import reduce_public_characteristic_values
from ..replacement.immutable import FrozenMap
from ..public_zone_moves import PublicZoneMoveObject
from ..rules.graveyard_return_choices import (
    OPERATION, CAPABILITY, RETURN_PAIR_FIELD, paired_graveyard_return_move, validate_return_choice,
)
from ..semantic_runtime.intents import RecordChoiceIntent, ReturnGraveyardCardToOwnerHandIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoicePreparation, SemanticChoiceRequest,
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError)


def _return_identity(row):
    if any(type(value) is not str or not value for value in (row.ref, row.object_id, row.logical_object_id)):
        raise SemanticChoiceError('The selected graveyard identity is incomplete')
    prefix = row.object_id+'@'
    suffix = row.logical_object_id.removeprefix(prefix)
    if not row.logical_object_id.startswith(prefix) or re.fullmatch(r'0|[1-9][0-9]*', suffix) is None:
        raise SemanticChoiceError('The selected graveyard incarnation is malformed')
    return FrozenMap({'ref':row.ref, 'object_id':row.object_id, 'zone_change_counter':int(suffix)})


def _eligible_return_rows(query, actor, selection):
    refs = query.choice_candidate_refs()
    rows = tuple(query.object(ref, zones=('graveyard',)) for ref in refs)
    if (len(refs) != len(set(refs)) or any(row is None or row.owner != actor
            or row.token or not row.logical_object_id or not selection.matches(row) for row in rows)):
        raise SemanticChoiceError('Graveyard return candidates do not match their owned public query')
    if not selection.greatest_power or not rows:
        return rows
    powers = tuple(0 if row.effective_power is None else row.effective_power for row in rows)
    maximum = reduce_public_characteristic_values(powers, 'maximum')
    return tuple(row for row, power in zip(rows, powers) if power == maximum)


def _return_intent(row, *, actor, reason):
    return ReturnGraveyardCardToOwnerHandIntent(actor=actor, object_ref=row.ref, reason=reason,
                                               target_snapshot=_return_identity(row))


@dataclass(frozen=True, slots=True)
class GraveyardReturnChoiceHandler:
    operation: str = OPERATION
    handler_id: str = 'choice.graveyard.own-return.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('109.5', '115.10a', '400.3', '400.7', '608.2c', '608.2d')
    capability_dependencies: tuple[str, ...] = (CAPABILITY, 'card.return.own_graveyard_to_owner_hand')
    continuation_fields: tuple[str, ...] = ('player', 'selection', '_choice_actor', '_legal_refs',
        '_legal_logical_ids', '_stack_label')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('ReturnGraveyardCardToOwnerHandIntent', 'return_to_hand')
    replay_fixture: str = 'optional-own-graveyard-return'
    test_modules: tuple[str, ...] = ('tests.test_graveyard_return_choices',)

    def prepare(self, effect, context):
        try:
            selection = validate_return_choice(effect, actor=context.actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if (selection.required or selection.returns_to_battlefield) and context.actor != context.stack_controller:
            raise SemanticChoiceError('Graveyard return requires the resolving controller')
        rows = _eligible_return_rows(context.query, context.actor, selection)
        if not rows:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='No eligible card in your graveyard'))
        if selection.greatest_power and len(rows) == 1:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=(_return_intent(rows[0], actor=context.actor, reason=context.stack_label),),
                auto_continue=AutoContinue(reason='Return the sole greatest-power creature card'),
            )
        for row in rows:
            _return_identity(row)
        refs = tuple(row.ref for row in rows)
        optional = not selection.required
        if selection.returns_to_battlefield:
            prompt = 'You may return a creature card from your graveyard to the battlefield.'
        elif selection.greatest_power:
            prompt = 'Choose a tied greatest-power creature card to return to your hand.'
        elif selection.mandatory_hand_return:
            prompt = 'Choose a creature or planeswalker card to return to your hand.'
        else:
            prompt = 'You may return a card from your graveyard to your hand.'
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(prompt=prompt,
                choice=ObjectChoice(field_name='card', legal_refs=refs, zones=('graveyard',),
                    minimum=0 if optional else 1, maximum=1, optional=optional, owner_relation='actor'),
                public_context=FrozenMap({'stack': context.stack_ref, 'operation': self.operation,
                    'objects': [{'id': row.ref, 'name': row.printed_name} for row in rows]})),
            continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                '_legal_refs': refs, '_legal_logical_ids': {row.ref: row.logical_object_id for row in rows},
                '_stack_label': context.stack_label}))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get('_choice_actor')
        try:
            selection = validate_return_choice(effect, actor=actor, continuation=True)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        refs, identities = effect['_legal_refs'], effect['_legal_logical_ids']
        label = effect['_stack_label']
        if (type(actor) is not str or not actor or not isinstance(refs, (tuple, list))
                or any(type(ref) is not str or not ref for ref in refs) or len(refs) != len(set(refs))
                or not isinstance(identities, Mapping) or set(identities) != set(refs)
                or any(type(value) is not str or not value for value in identities.values())
                or type(label) is not str or not label):
            raise SemanticChoiceError('Graveyard return continuation identities are malformed')
        selected = response.get('card')
        if selection.greatest_power:
            current = _eligible_return_rows(query, actor, selection)
            if (len(current) < 2 or tuple(refs) != tuple(row.ref for row in current)
                    or dict(identities) != {row.ref: row.logical_object_id for row in current}):
                raise SemanticChoiceError('The greatest-power graveyard choices changed')
            if type(selected) is not str or selected not in refs:
                raise SemanticChoiceError('Choose one of the tied greatest-power creature cards')
        elif not selection.required and (selected is None or selected == ''):
            return SemanticChoiceCompletion()
        elif type(selected) is not str or selected not in refs or selected not in query.choice_candidate_refs():
            prompt = ('Choose one of the eligible graveyard cards' if selection.required
                      else 'Choose one of the eligible graveyard cards or decline')
            raise SemanticChoiceError(prompt)
        row = query.object(selected, zones=('graveyard',))
        if (row is None or row.owner != actor or row.token or row.logical_object_id != identities[selected]
                or not selection.matches(row)):
            raise SemanticChoiceError('The selected graveyard incarnation is no longer eligible')
        if selection.returns_to_battlefield:
            effect = paired_graveyard_return_move((PublicZoneMoveObject.from_row(row),))
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap(effect),))
        return SemanticChoiceCompletion(intents=(_return_intent(row, actor=actor, reason=label),))


_PAIR_FIELDS = {"schema_version", "step", "controller", "opponent", "opponent_card", "own_card"}
_PAIR_PREPARED_FIELDS = {"_choice_actor", "_pair_prompt", "_pair_options"}


def _paired_return_state(effect, controller):
    marker = effect.get(RETURN_PAIR_FIELD)
    if (
        set(effect) != {"op", "player", RETURN_PAIR_FIELD} or effect.get("op") != "choose_option"
        or not isinstance(marker, Mapping) or type(marker.get("schema_version")) is not int
        or marker["schema_version"] != 1 or type(controller) is not str or not controller
    ):
        raise SemanticChoiceError("Paired graveyard return requires its exact controller and instruction")
    if set(marker) == {"schema_version"} and effect["player"] == controller:
        return {"step": "initial", "controller": controller, "opponent": None, "opponent_card": None, "own_card": None}
    if set(marker) != _PAIR_FIELDS or marker["controller"] != controller:
        raise SemanticChoiceError("The paired graveyard return continuation changed")
    step, opponent = marker["step"], marker["opponent"]
    if (
        step not in ("own_card", "confirm") or type(opponent) is not str or not opponent or opponent == controller
        or effect["player"] != (opponent if step == "own_card" else controller)
        or step == "own_card" and marker["own_card"] is not None
    ):
        raise SemanticChoiceError("The paired graveyard chooser changed")
    values = {key: marker[key] for key in ("step", "controller", "opponent")}
    for key, owner in (("opponent_card", opponent), ("own_card", controller)):
        raw = marker[key]
        if raw is None:
            values[key] = None
            continue
        try:
            card = PublicZoneMoveObject.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if card.owner != owner:
            raise SemanticChoiceError("The original graveyard owner changed")
        values[key] = card
    return values


def _paired_return_stage(state, step):
    controller = state["controller"]
    marker = {
        "schema_version": 1, "step": step, "controller": controller, "opponent": state["opponent"],
        "opponent_card": state["opponent_card"].to_dict() if state["opponent_card"] is not None else None,
        "own_card": state["own_card"].to_dict() if state["own_card"] is not None else None,
    }
    return FrozenMap({"op": "choose_option",
        "player": state["opponent"] if step == "own_card" else controller, RETURN_PAIR_FIELD: marker})


def _public_creature_cards(query, owners):
    refs = frozenset(query.choice_candidate_refs())
    return tuple(sorted(
        (row for row in query.objects(zones=("graveyard",))
         if row.ref in refs and row.owner in owners and "creature" in row.types and not row.token),
        key=lambda row: (row.owner, row.ref),
    ))


def _current_pair_card(query, identity):
    if identity is None or identity.owner not in query.active_seats or identity.ref not in query.choice_candidate_refs():
        return None
    row = query.object(identity.ref, zones=("graveyard",))
    return identity if identity.matches_row(row) and "creature" in row.types and not row.token else None


def _paired_request(effect, context, prompt, mode, options):
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=prompt,
            choice=ScalarChoice(field_name="choice", legal_values=tuple(options)),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": "choose_option",
                "options": [{"id": key, "label": label} for key, (label, _) in options.items()],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), "_choice_actor": context.actor, "_pair_prompt": mode,
            "_pair_options": {key: card.to_dict() if card is not None else None for key, (_, card) in options.items()},
        }),
    )


def _paired_card_options(rows):
    return {row.ref: (row.printed_name, PublicZoneMoveObject.from_row(row)) for row in rows}


def prepare_paired_graveyard_return(effect, context):
    state = _paired_return_state(effect, context.stack_controller)
    query = context.query
    if context.actor != effect["player"] or state["controller"] not in query.active_seats:
        raise SemanticChoiceError("Paired graveyard return requires its current chooser")
    if state["step"] == "initial":
        opponents = tuple(seat for seat in query.active_seats if seat != context.stack_controller)
        rows = _public_creature_cards(query, opponents)
        if rows:
            return _paired_request(effect, context, "Choose a creature card in an opponent's graveyard.",
                                   "opponent_card", _paired_card_options(rows))
        if opponents:
            return _paired_request(effect, context, "Choose the opponent who will choose your creature card.",
                                   "opponent", {seat: (seat, None) for seat in opponents})
    elif state["step"] == "own_card":
        rows = _public_creature_cards(query, (context.stack_controller,)) if context.actor in query.active_seats else ()
        if rows:
            return _paired_request(effect, context, "Choose a creature card in the resolving controller's graveyard.",
                                   "own_card", _paired_card_options(rows))
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue("The second selection has no eligible card",
                prepend_effects=(_paired_return_stage(state, "confirm"),)))
    elif any(_current_pair_card(query, state[key]) for key in ("opponent_card", "own_card")):
        return _paired_request(effect, context, "Return the chosen creature cards under their owners' control?",
            "confirm", {"return": ("Return the chosen cards", None), "decline": ("Leave them in their graveyards", None)})
    return SemanticChoicePreparation(None, FrozenMap(effect),
        auto_continue=AutoContinue("No chosen creature card remains to return"))


def complete_paired_graveyard_return(continuation, response, query):
    effect = continuation.effect
    controller = continuation.semantic_frame.controller
    state = _paired_return_state({key: value for key, value in effect.items() if key not in _PAIR_PREPARED_FIELDS}, controller)
    actor, mode, options = effect.get("_choice_actor"), effect.get("_pair_prompt"), effect.get("_pair_options")
    selected = response.get("choice")
    valid_modes = {"initial": ("opponent", "opponent_card"), "own_card": ("own_card",), "confirm": ("confirm",)}
    if (
        actor != effect["player"] or actor not in query.active_seats or mode not in valid_modes[state["step"]]
        or not isinstance(options, Mapping) or type(selected) is not str or selected not in options
    ):
        raise SemanticChoiceError("Choose a currently authorized public option")
    record = RecordChoiceIntent(actor=actor, event_code="semantic.option.chosen",
        message=f"{actor} chose {selected}.", details=FrozenMap({"stack": continuation.stack_ref, "choice": selected}))
    if mode == "opponent":
        opponents = tuple(seat for seat in query.active_seats if seat != controller)
        if (selected not in opponents or options[selected] is not None
                or _public_creature_cards(query, opponents)):
            raise SemanticChoiceError("An eligible opponent creature card must be chosen when there is one")
        state["opponent"] = selected
        return SemanticChoiceCompletion(intents=(record,), repeat_effect=_paired_return_stage(state, "own_card"))
    if mode in ("opponent_card", "own_card"):
        try:
            card = PublicZoneMoveObject.from_dict(options[selected])
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if (card.ref != selected or (card.owner == controller) != (mode == "own_card")
                or _current_pair_card(query, card) is None):
            raise SemanticChoiceError("The chosen creature card is no longer eligible for its original role")
        state[mode] = card
        if mode == "opponent_card":
            state["opponent"] = card.owner
        next_step = "own_card" if mode == "opponent_card" else "confirm"
        return SemanticChoiceCompletion(intents=(record,), repeat_effect=_paired_return_stage(state, next_step))
    if selected not in ("return", "decline") or options[selected] is not None:
        raise SemanticChoiceError("Choose whether to return the original creature cards")
    cards = tuple(card for key in ("opponent_card", "own_card")
                  if (card := _current_pair_card(query, state[key])) is not None)
    effects = (FrozenMap(paired_graveyard_return_move(cards)),) if selected == "return" and cards else ()
    return SemanticChoiceCompletion(intents=(record,), prepend_effects=effects)

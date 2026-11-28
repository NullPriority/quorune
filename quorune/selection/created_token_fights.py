from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import matching_battlefield_objects
from ..creature_power_damage import CreaturePowerDamageError, resolve_simultaneous_fights
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement import ReplacementChoiceRequired
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.created_token_fights import created_token_fight_payload_dependencies, created_token_fight_program_dependencies
from ..semantic_choices.model import DecisionMapChoice
from ..token_creation import TokenCreationError, create_grouped_tokens
from .model import SelectionContinuation, SelectionContract, SelectionModelError, decode_selection_continuation


OPERATION = "selection.nontarget.created_token_fights.v1"
_FRAME = "created_token_fights"
_STAGE = "_created_token_fight_stage"
_REPLACEMENTS = "_replacement_selections"
_EVENT_IDS = "_replacement_event_ids"


@dataclass(frozen=True, slots=True)
class CreatedTokenFightRound:
    opponents: tuple[ContinuousObjectIdentity, ...]
    fighters: tuple[ContinuousObjectIdentity, ...] = ()
    assignments: tuple[tuple[str, str], ...] = ()
    stage: str = "create"

    def __post_init__(self):
        identities = (*self.opponents, *self.fighters)
        if (not self.opponents or any(not isinstance(identity, ContinuousObjectIdentity) for identity in identities)
                or len({identity.object_id for identity in identities}) != len(identities)
                or len(self.fighters) > len(self.opponents)
                or self.stage not in {"create", "pair", "fight"}
                or self.stage == "create" and self.fighters
                or self.stage != "fight" and self.assignments
                or self.stage == "fight" and not 1 <= len(self.assignments) <= len(self.fighters)
                or any(not isinstance(pair, tuple) or len(pair) != 2
                       or any(type(ref) is not str or not ref for ref in pair) for pair in self.assignments)):
            raise ValueError("A created-token fight requires its closed distinct object identities")

    def to_dict(self):
        return {
            "opponents": [identity.to_dict() for identity in self.opponents],
            "fighters": [identity.to_dict() for identity in self.fighters],
            "assignments": [list(pair) for pair in self.assignments],
            "stage": self.stage,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"opponents", "fighters", "assignments", "stage"}
                or any(not isinstance(value[key], (list, tuple)) for key in ("opponents", "fighters", "assignments"))
                or any(not isinstance(pair, (list, tuple)) for pair in value["assignments"])):
            raise ValueError("A created-token fight continuation is malformed")
        return cls(
            opponents=tuple(ContinuousObjectIdentity.from_dict(row) for row in value["opponents"]),
            fighters=tuple(ContinuousObjectIdentity.from_dict(row) for row in value["fighters"]),
            assignments=tuple(tuple(pair) for pair in value["assignments"]),
            stage=value["stage"],
        )


def _require_item(host, resume, effect):
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.ref != resume["stack_ref"] or item.kind not in {"spell", "spell_copy"}
            or item.targets or item.context.get("currently_resolving") is not True or item.controller not in host.active_seats):
        raise GameRuleError("Created-token fights require the original resolving spell")
    host._validate_semantic_frame(resume["semantic_frame"], item)
    base = {key: value for key, value in effect.items() if key not in {_STAGE, _REPLACEMENTS, _EVENT_IDS}}
    program = host.semantics.get(item.semantic_key)
    try:
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not created_token_fight_program_dependencies(program)
                or thaw_value(program.effects[0]) != thaw_value(base)
                or not created_token_fight_payload_dependencies(base)):
            raise ValueError("Created-token fights require their original complete program")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return item


def _keep(item, current):
    item.context[_FRAME] = current.to_dict()


def _round(item, stage):
    try:
        current = CreatedTokenFightRound.from_dict(item.context.get(_FRAME))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if current.stage != stage:
        raise GameRuleError("The created-token fight changed its original resolution stage")
    return current


def _identity(card):
    return ContinuousObjectIdentity(card.object_id, card.logical_object_id)


def _current_refs(host, identities):
    result = []
    for identity in identities:
        card = host.state.cards.get(identity.object_id)
        if (card is not None and card.zone == "battlefield" and not card.phased_out
                and card.logical_object_id == identity.logical_object_id):
            result.append(card.ref)
    return tuple(result)


def _opponents(host, actor):
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), excluded_controllers=(actor,))
    return tuple(_identity(card) for card in matching_battlefield_objects(host, query)
                 if card.controller in host.active_seats)


def _continue(host, resume, again=None):
    effects = list(resume["effects"])
    if again is not None:
        effects.insert(0, again)
    host._continue_resolution(
        stack_ref=resume["stack_ref"], effects=effects, destination=resume["destination"], note=resume["note"],
        instruction_pointer=resume["semantic_frame"]["instruction_pointer"] + int(again is None),
    )


def _pause_for_replacement(host, item, resume, effect, required):
    from ..replacement_decisions import issue_replacement_order_choice

    issue_replacement_order_choice(
        host, item=item, effect=effect, remaining=resume["effects"], destination=resume["destination"],
        note=resume["note"], instruction_pointer=resume["semantic_frame"]["instruction_pointer"], required=required,
    )


def _pairing_choice(host, item, current, effect, resume):
    tokens = _current_refs(host, current.fighters)
    opponents = _current_refs(host, current.opponents)
    count = min(len(tokens), len(opponents))
    if count == 0:
        _continue(host, resume)
        return
    if len(tokens) == len(opponents) == 1:
        current = replace(current, assignments=((tokens[0], opponents[0]),), stage="fight")
        _keep(item, current)
        _continue(host, resume, {**effect, _STAGE: "fight"})
        return
    selection = SelectionContinuation(
        contract=SelectionContract.NONTARGET_CHOICE, operation_id=OPERATION, actor=item.controller,
        state_revision=host.state.revision, stack_ref=item.ref, source_ref=host._stack_source_ref(item),
        visibility="public", payload=FrozenMap({
            "effect": effect, "resume": resume, "tokens": tokens, "opponents": opponents,
        }),
    )
    choice = DecisionMapChoice("fight_pairs", tokens, count, opponents)
    host.permissions.issue(
        kind="choice.apnap", role="pilot", actors=[item.controller], allowed_actions=["choose"],
        payload_by_actor={item.controller: {
            "prompt": "Assign each fighting token a different opposing creature.",
            "fighters": list(tokens), "opponents": list(opponents),
            "legal_actions": [{"id": "choose", "action": "choose", "choice_schema": choice.choice_schema()}],
        }},
        continuation={"selection": selection.to_dict()},
    )


def _create(host, item, effect, resume, current):
    groups = tuple(f"fight-group:{index}" for index in range(len(current.opponents)))
    try:
        created = create_grouped_tokens(
            host, item.controller, groups=groups, name=effect["token"]["name"],
            characteristics=effect["token"]["characteristics"], reason=item.label,
            replacement_selections=tuple(effect.get(_REPLACEMENTS, ())),
        )
    except ReplacementChoiceRequired as required:
        _pause_for_replacement(host, item, resume, {**effect, _STAGE: "create"}, required)
        return
    except TokenCreationError as exc:
        raise GameRuleError(str(exc)) from exc
    leaders = {}
    for ref, group in created.grouped_refs:
        if group in groups:
            leaders.setdefault(group, ref)
    fighters = tuple(_identity(host._resolve_object(item.controller, leaders[group], zones={"battlefield"}))
                     for group in groups if group in leaders)
    current = replace(current, fighters=fighters, stage="pair")
    _keep(item, current)
    base = {key: value for key, value in effect.items() if key not in {_STAGE, _REPLACEMENTS, _EVENT_IDS}}
    _pairing_choice(host, item, current, base, resume)


def complete_created_token_fights(host, decision):
    try:
        selection = decode_selection_continuation(
            decision.continuation, expected_contract=SelectionContract.NONTARGET_CHOICE, expected_operation_id=OPERATION,
        )
    except SelectionModelError as exc:
        raise GameRuleError(str(exc)) from exc
    payload = thaw_value(selection.payload)
    effect, resume = payload["effect"], payload["resume"]
    item = _require_item(host, resume, effect)
    current = _round(item, "pair")
    actor, = decision.actors
    if (selection.actor != actor or actor != item.controller or selection.stack_ref != item.ref
            or selection.source_ref != host._stack_source_ref(item) or selection.state_revision != decision.created_revision
            or selection.visibility != "public"):
        raise GameRuleError("The original fight selection changed")
    tokens = _current_refs(host, current.fighters)
    opponents = _current_refs(host, current.opponents)
    pairs = decision.responses[actor].get("fight_pairs")
    if (list(tokens) != payload["tokens"] or list(opponents) != payload["opponents"]
            or not isinstance(pairs, Mapping) or len(pairs) != min(len(tokens), len(opponents))
            or not set(pairs) <= set(tokens) or any(type(ref) is not str or ref not in opponents for ref in pairs.values())
            or len(set(pairs.values())) != len(pairs)):
        raise GameRuleError("Pair the current original tokens with different original opponents")
    _keep(item, replace(current, stage="fight", assignments=tuple((ref, pairs[ref]) for ref in tokens if ref in pairs)))
    _continue(host, resume, {**effect, _STAGE: "fight"})


def _fight(host, item, effect, resume, current):
    fighters = set(_current_refs(host, current.fighters))
    opponents = set(_current_refs(host, current.opponents))
    pairs = tuple((token, opponent) for token, opponent in current.assignments if token in fighters and opponent in opponents)
    if (len({token for token, _ in current.assignments}) != len(current.assignments)
            or len({opponent for _, opponent in current.assignments}) != len(current.assignments)):
        raise GameRuleError("A retained fight cannot use the same object twice")
    try:
        resolve_simultaneous_fights(host, pairs, actor=item.controller, reason=item.label, batch_ref=item.ref, effect=effect)
    except ReplacementChoiceRequired as required:
        _pause_for_replacement(host, item, resume, effect, required)
        return
    except CreaturePowerDamageError as exc:
        raise GameRuleError(str(exc)) from exc
    _continue(host, resume)


def begin_created_token_fights(host, effect, resume):
    item = _require_item(host, resume, effect)
    stage = effect.get(_STAGE)
    if stage is None:
        if _FRAME in item.context or {_REPLACEMENTS, _EVENT_IDS} & effect.keys():
            raise GameRuleError("The initial token fight was already prepared")
        opponents = _opponents(host, item.controller)
        if not opponents:
            _continue(host, resume)
            return
        current = CreatedTokenFightRound(opponents)
        _keep(item, current)
        _create(host, item, effect, resume, current)
    elif stage == "create" and _REPLACEMENTS in effect and _EVENT_IDS not in effect:
        _create(host, item, effect, resume, _round(item, "create"))
    elif stage == "fight":
        _fight(host, item, effect, resume, _round(item, "fight"))
    else:
        raise GameRuleError("The created-token fight stage is unsupported")

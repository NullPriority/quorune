from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeAlias

from .damage import damage_proposal, DamageError, resolve_damage_batch
from .errors import GameRuleError
from .fixed_mana_results import GAIN_CONTROLLER_LIFE, PLACE_MANA_POINT_COUNTER
from .life_state import LifeStateError, pay_life_cost
from .rules.activation_counter_cost import SourceCounterRemovalCost
from .rules.next_cast_library_access import NEXT_CAST_LIBRARY_ACCESS


class ManaModeEffectHost(Protocol):
    state: Any

    def move_card(
        self,
        object_id: str,
        destination: str,
        *,
        reason: str,
        semantic_events: bool,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class DealDamageToController:
    effect_index: int
    amount: int


@dataclass(frozen=True, slots=True)
class PayLife:
    effect_index: int
    amount: int


@dataclass(frozen=True, slots=True)
class GainControllerLife:
    effect_index: int
    amount: int


@dataclass(frozen=True, slots=True)
class SacrificeManaSource:
    effect_index: int


@dataclass(frozen=True, slots=True)
class ExileHandManaSource:
    effect_index: int


@dataclass(frozen=True, slots=True)
class ExertManaSource:
    effect_index: int


@dataclass(frozen=True, slots=True)
class SkipActorUntap:
    effect_index: int


@dataclass(frozen=True, slots=True)
class ArmNextCastLibraryAccess:
    effect_index: int


@dataclass(frozen=True, slots=True)
class RemoveManaSourceCounters:
    effect_index: int
    cost: SourceCounterRemovalCost


@dataclass(frozen=True, slots=True)
class SacrificeDepletedManaSource:
    effect_index: int
    counter_name: str


@dataclass(frozen=True, slots=True)
class PlaceManaPointCounter:
    effect_index: int


ManaModeEffect: TypeAlias = (DealDamageToController | PayLife | GainControllerLife | SacrificeManaSource
                             | ExileHandManaSource | ExertManaSource | SkipActorUntap | RemoveManaSourceCounters | SacrificeDepletedManaSource
                             | PlaceManaPointCounter | ArmNextCastLibraryAccess)


def _exact_fields(
    effect: Mapping[str, Any],
    expected: set[str],
    *,
    message: str,
) -> None:
    if set(effect) != expected:
        raise GameRuleError(message)


def _amount(effect: Mapping[str, Any], *, operation: str) -> int:
    _exact_fields(
        effect,
        {"op", "amount"},
        message=f"{operation} mana effects require only op and amount",
    )
    amount = effect["amount"]
    if type(amount) is not int or amount < 0:
        raise GameRuleError(
            f"{operation} mana-effect amounts must be nonnegative integers"
        )
    return amount


def _decode_damage(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    return DealDamageToController(index, _amount(effect, operation="Damage"))


def _decode_life_gain(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    amount = _amount(effect, operation="Life gain")
    if not 1 <= amount <= 20:
        raise GameRuleError("Mana life gain requires a fixed positive amount")
    return GainControllerLife(index, amount)


def _decode_life_payment(
    effect: Mapping[str, Any], index: int
) -> ManaModeEffect:
    return PayLife(index, _amount(effect, operation="Life-payment"))


def _decode_sacrifice(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(
        effect,
        {"op"},
        message="Sacrifice-source mana effects require only op",
    )
    return SacrificeManaSource(index)


def _decode_hand_exile(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect, {"op"}, message="Hand-source exile mana requires only its operation")
    return ExileHandManaSource(index)


def _decode_exert_mana(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect, {"op"}, message="Mana exert only admits its closed operation")
    return ExertManaSource(index)


def _decode_skip_untap(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect,{'op'},message='Next-actor untap mana result requires only its operation')
    return SkipActorUntap(index)


def _decode_next_cast_library_access(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect, {"op"}, message="Next-cast library mana requires only its operation")
    return ArmNextCastLibraryAccess(index)


def _decode_counter_cost(effect,index):
    from .rules.activation_counter_cost import SourceCounterRemovalCost
    _exact_fields(effect,{'op','cost'},message='Mana counter costs require only their operation and typed cost')
    try:cost=SourceCounterRemovalCost.from_dict(effect['cost'])
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if cost.schema_version!=1:raise GameRuleError('Automatic mana counter costs require a fixed named counter')
    return RemoveManaSourceCounters(index,cost)


def _decode_counter_depletion(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    from .fixed_mana_results import FixedManaResultSpec
    _exact_fields(effect,{'op','counter_name'},message='Counter-depletion mana results require their exact named counter')
    try:result=FixedManaResultSpec('sacrifice_if_no_counters',schema_version=2,counter_name=effect['counter_name'])
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    return SacrificeDepletedManaSource(index,result.counter_name)


def _decode_point_counter(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect, {"op"}, message="Mana point placement requires only its operation")
    return PlaceManaPointCounter(index)


_MODE_EFFECT_DECODERS: Mapping[
    str, Callable[[Mapping[str, Any], int], ManaModeEffect]
] = {
    "damage_self": _decode_damage,
    GAIN_CONTROLLER_LIFE: _decode_life_gain,
    PLACE_MANA_POINT_COUNTER: _decode_point_counter,
    "pay_life": _decode_life_payment,
    "sacrifice_source": _decode_sacrifice,
    "exile_hand_source": _decode_hand_exile,
    "exert_mana_source": _decode_exert_mana,
    'skip_actor_untap': _decode_skip_untap,
    NEXT_CAST_LIBRARY_ACCESS: _decode_next_cast_library_access,
    'remove_source_counters': _decode_counter_cost,
    'sacrifice_if_no_counters': _decode_counter_depletion,
}


def compile_mana_mode_effects(
    effects: Sequence[Mapping[str, Any]],
) -> tuple[ManaModeEffect, ...]:
    """Lower the closed JSON-compatible mana-effect vocabulary to typed values."""

    compiled: list[ManaModeEffect] = []
    for effect_index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            raise GameRuleError("Mana-mode effects must be mappings")
        operation = effect.get("op")
        decoder = (
            _MODE_EFFECT_DECODERS.get(operation)
            if isinstance(operation, str)
            else None
        )
        if decoder is None:
            raise GameRuleError("Mana mode contains an unsupported side effect")
        compiled.append(decoder(effect, effect_index))
    return tuple(compiled)


def _mana_damage_payment_prefix(payment_id: str) -> str:
    return f"damage.mana:{payment_id}:"


def mana_damage_event_belongs_to_payment(event_id: str, payment_id: str) -> bool:
    if type(event_id) is not str or type(payment_id) is not str or not payment_id:
        return False
    prefix = _mana_damage_payment_prefix(payment_id)
    if not event_id.startswith(prefix):
        return False
    source, separator, index = event_id[len(prefix):].rpartition(":")
    return bool(source and separator and index.isascii() and index.isdecimal()
                and (index == "0" or not index.startswith("0")))


def mana_result_event_belongs_to_payment(event_id: str, payment_id: str) -> bool:
    from .rules.mana_life_result import mana_life_event_belongs_to_payment
    from .rules.mana_point_result import mana_point_event_belongs_to_payment

    return (mana_damage_event_belongs_to_payment(event_id, payment_id)
            or mana_life_event_belongs_to_payment(event_id, payment_id)
            or mana_point_event_belongs_to_payment(event_id, payment_id))


def _damage_proposal_id(
    host: ManaModeEffectHost,
    effect: DealDamageToController,
    *,
    seat: str,
    source: Any | None,
    payment_id: str | None,
) -> str:
    if payment_id:
        source_identity = source.object_id if source is not None else seat
        return f"{_mana_damage_payment_prefix(payment_id)}{source_identity}:{effect.effect_index}"
    return (
        f"damage.mana:{host.state.revision}:"
        f"{host.state.event_sequence + 1}:{effect.effect_index}"
    )


def _replacement_selections(
    selections_by_event: Mapping[str, Any] | None,
    event_id: str,
) -> tuple[Any, ...]:
    raw = (
        selections_by_event.get(event_id, ())
        if isinstance(selections_by_event, Mapping)
        else ()
    )
    if not isinstance(raw, (list, tuple)):
        raise GameRuleError("Mana-payment replacement selections are malformed")
    return tuple(raw)


def _apply_damage(
    host: ManaModeEffectHost,
    effect: DealDamageToController,
    *,
    seat: str,
    source: Any | None,
    payment_id: str | None,
    replacement_selections_by_event: Mapping[str, Any] | None,
) -> None:
    if effect.amount == 0:
        return
    proposal_id = _damage_proposal_id(
        host,
        effect,
        seat=seat,
        source=source,
        payment_id=payment_id,
    )
    proposal = damage_proposal(
        host,
        proposal_id=proposal_id,
        actor=seat,
        source_ref=(source.ref if source is not None else None),
        target=seat,
        amount=effect.amount,
        combat=False,
        reason="mana ability damage",
    )
    try:
        resolve_damage_batch(
            host,
            (proposal,),
            replacement_selections=_replacement_selections(
                replacement_selections_by_event,
                proposal_id,
            ),
        )
    except DamageError as exc:
        raise GameRuleError(str(exc)) from exc


def _apply_life_payment(
    host: ManaModeEffectHost,
    effect: PayLife,
    *,
    seat: str,
) -> None:
    try:
        pay_life_cost(host, seat, effect.amount)
    except LifeStateError as exc:
        raise GameRuleError(str(exc)) from exc


def mana_sacrifice_payment_event_id(payment_id: str, source_ref: str) -> str:
    return f"zone.mana:{payment_id}:{source_ref}"


def _apply_sacrifice(
    host: ManaModeEffectHost,
    *,
    seat: str,
    source: Any | None,
    payment_id: str | None,
    replacement_selections_by_event: Mapping[str, Any] | None,
) -> None:
    from .sacrifice_prohibition_model import sacrifice_is_prohibited

    if (source is None or source.controller != seat or source.zone != "battlefield"
            or sacrifice_is_prohibited(host.state, source)):
        raise GameRuleError("The mana source cannot be sacrificed")
    selections = _replacement_selections(
        replacement_selections_by_event, mana_sacrifice_payment_event_id(payment_id, source.ref),
    ) if payment_id else ()
    host.move_card(
        source.object_id,
        "graveyard",
        reason="mana ability cost",
        semantic_events=True,
        **({"replacement_selections": selections} if selections else {}),
    )


def pay_hand_exile_mana_cost(host, seat, source, *, replacement_selections=()):
    from .effect_runtime.hand_inspection import commit_selected_hand_reveal

    if (source is None or source.zone != "hand" or source.owner != seat
            or source.object_id not in host.state.players[seat].zones["hand"]):
        raise GameRuleError("Hand exile mana requires the actor's current hand card")
    record = host.card_record(source)
    if record is None:
        raise GameRuleError("Hand exile mana lacks its pinned source")
    commit_selected_hand_reveal(
        host,
        {"object_id": source.object_id, "logical_object_id": source.logical_object_id,
         "ref": source.ref, "player": seat, "oracle_id": record.oracle_id, "name": record.name},
        source_ref=source.ref,
        summary="A card was revealed to activate a mana ability.",
    )
    host.move_card(source.object_id, "exile", reason="mana ability cost",
                   semantic_events=True, replacement_selections=replacement_selections)


def apply_mana_mode_effects(
    host: ManaModeEffectHost,
    seat: str,
    effects: Sequence[Mapping[str, Any]],
    *,
    source: Any | None = None,
    payment_id: str | None = None,
    replacement_selections_by_event: Mapping[str, Any] | None = None,
) -> None:
    """Validate the complete effect vocabulary, then apply it in printed order."""

    compiled = compile_mana_mode_effects(effects)
    for effect in compiled:
        if isinstance(effect, DealDamageToController):
            _apply_damage(
                host,
                effect,
                seat=seat,
                source=source,
                payment_id=payment_id,
                replacement_selections_by_event=replacement_selections_by_event,
            )
        elif isinstance(effect, PayLife):
            _apply_life_payment(host, effect, seat=seat)
        elif isinstance(effect, GainControllerLife):
            from .rules.mana_life_result import apply_mana_life_gain

            apply_mana_life_gain(host, seat, effect.amount, effect.effect_index, source=source,
                                 payment_id=payment_id, selections_by_event=replacement_selections_by_event)
        elif isinstance(effect, PlaceManaPointCounter):
            from .rules.mana_point_result import apply_mana_point_counter

            apply_mana_point_counter(host, seat, effect.effect_index, source=source,
                                     payment_id=payment_id, selections_by_event=replacement_selections_by_event)
        elif isinstance(effect,RemoveManaSourceCounters):
            from .rules.activation.counter_costs import plan_source_counter_removal_payment,SourceCounterActivationCostError
            from .counter_removal import commit_counter_removals,CounterRemovalError
            if source is None or source.zone!='battlefield' or source.controller!=seat:
                raise GameRuleError('Mana counter cost requires its controlled battlefield source')
            try:commit_counter_removals(host,plan_source_counter_removal_payment(host,source,effect.cost))
            except (SourceCounterActivationCostError,CounterRemovalError) as exc:raise GameRuleError(str(exc)) from exc
        elif isinstance(effect,SacrificeDepletedManaSource):
            card = host.state.cards.get(source.object_id) if source is not None else None
            if (card is not None and card.zone == 'battlefield' and not card.phased_out
                    and card.logical_object_id == source.logical_object_id and card.controller == seat
                    and not card.counters.get(effect.counter_name,0)):
                host.apply_effect({'op':'sacrifice','card':card.ref,'reason':'mana ability result'},actor=seat)
        elif isinstance(effect, ExileHandManaSource):
            selections = _replacement_selections(
                replacement_selections_by_event, mana_sacrifice_payment_event_id(payment_id, source.ref),
            ) if payment_id and source is not None else ()
            pay_hand_exile_mana_cost(host, seat, source, replacement_selections=selections)
        elif isinstance(effect, ExertManaSource):
            from .rules.exert_mana import apply_mana_exert

            apply_mana_exert(host, source, actor=seat)
        elif isinstance(effect, ArmNextCastLibraryAccess):
            from .rules.next_cast_library_access import arm_next_cast_library_access

            arm_next_cast_library_access(host, seat, source)
        elif isinstance(effect,SkipActorUntap):
            from .tap_state import prohibit_next_player_untap
            if source is None:raise GameRuleError('Next-actor untap mana result requires its original source')
            prohibit_next_player_untap(host,source,actor=seat)
        else:
            _apply_sacrifice(
                host, seat=seat, source=source, payment_id=payment_id,
                replacement_selections_by_event=replacement_selections_by_event,
            )


__all__ = [
    "apply_mana_mode_effects",
    "compile_mana_mode_effects",
    "DealDamageToController",
    "ManaModeEffect",
    "ManaModeEffectHost",
    "PayLife",
    "SacrificeManaSource",
]

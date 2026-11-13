from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeAlias

from .damage import damage_proposal, DamageError, resolve_damage_batch
from .errors import GameRuleError
from .life_state import LifeStateError, pay_life_cost
from .rules.activation_counter_cost import SourceCounterRemovalCost


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
class SacrificeManaSource:
    effect_index: int


@dataclass(frozen=True, slots=True)
class SkipActorUntap:
    effect_index: int


@dataclass(frozen=True, slots=True)
class RemoveManaSourceCounters:
    effect_index: int
    cost: SourceCounterRemovalCost


ManaModeEffect: TypeAlias = DealDamageToController | PayLife | SacrificeManaSource | SkipActorUntap | RemoveManaSourceCounters


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


def _decode_skip_untap(effect: Mapping[str, Any], index: int) -> ManaModeEffect:
    _exact_fields(effect,{'op'},message='Next-actor untap mana result requires only its operation')
    return SkipActorUntap(index)


def _decode_counter_cost(effect,index):
    from .rules.activation_counter_cost import SourceCounterRemovalCost
    _exact_fields(effect,{'op','cost'},message='Mana counter costs require only their operation and typed cost')
    try:cost=SourceCounterRemovalCost.from_dict(effect['cost'])
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if cost.schema_version!=1:raise GameRuleError('Automatic mana counter costs require a fixed named counter')
    return RemoveManaSourceCounters(index,cost)


_MODE_EFFECT_DECODERS: Mapping[
    str, Callable[[Mapping[str, Any], int], ManaModeEffect]
] = {
    "damage_self": _decode_damage,
    "pay_life": _decode_life_payment,
    "sacrifice_source": _decode_sacrifice,
    'skip_actor_untap': _decode_skip_untap,
    'remove_source_counters': _decode_counter_cost,
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
    if (
        source is None
        or source.controller != seat
        or source.zone != "battlefield"
    ):
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
        elif isinstance(effect,RemoveManaSourceCounters):
            from .rules.activation.counter_costs import plan_source_counter_removal_payment,SourceCounterActivationCostError
            from .counter_removal import commit_counter_removals,CounterRemovalError
            if source is None or source.zone!='battlefield' or source.controller!=seat:
                raise GameRuleError('Mana counter cost requires its controlled battlefield source')
            try:commit_counter_removals(host,plan_source_counter_removal_payment(host,source,effect.cost))
            except (SourceCounterActivationCostError,CounterRemovalError) as exc:raise GameRuleError(str(exc)) from exc
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

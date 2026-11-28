from __future__ import annotations

"""Immutable facts for the represented ordinary Saga final-chapter SBA."""

from dataclasses import dataclass


class SagaLifecycleError(ValueError):
    """A Saga final-chapter snapshot is malformed or contradictory."""


@dataclass(frozen=True, slots=True)
class SagaFinalChapterSnapshot:
    """The complete represented CR 704.5s/714.4 state for one Saga.

    ``chapter_trigger_pending`` is true only for a chapter ability from this
    exact logical incarnation.  Keeping physical and logical identity in the
    immutable value prevents an older incarnation's trigger from protecting a
    Saga that left the battlefield and returned.
    """

    object_id: str
    logical_object_id: str
    controller: str
    lore_counters: int
    chapter_numbers: tuple[int, ...]
    chapter_trigger_pending: bool

    def __post_init__(self) -> None:
        for field in ("object_id", "logical_object_id", "controller"):
            value = getattr(self, field)
            if type(value) is not str or not value or value != value.strip():
                raise SagaLifecycleError(
                    f"Saga lifecycle {field} must be a canonical identity"
                )
        if type(self.lore_counters) is not int or self.lore_counters < 0:
            raise SagaLifecycleError(
                "Saga lifecycle lore counters must be a nonnegative integer"
            )
        chapters = tuple(self.chapter_numbers)
        if (
            not chapters
            or any(type(value) is not int or value < 1 for value in chapters)
            or chapters != tuple(sorted(set(chapters)))
        ):
            raise SagaLifecycleError(
                "Saga lifecycle chapters must be unique positive integers "
                "in canonical order"
            )
        if type(self.chapter_trigger_pending) is not bool:
            raise SagaLifecycleError(
                "Saga lifecycle pending-trigger state must be boolean"
            )
        object.__setattr__(self, "chapter_numbers", chapters)

    @property
    def final_chapter_number(self) -> int:
        return self.chapter_numbers[-1]

    @property
    def requires_sacrifice(self) -> bool:
        return (
            self.lore_counters >= self.final_chapter_number
            and not self.chapter_trigger_pending
        )


__all__ = [
    "SagaFinalChapterSnapshot",
    "SagaLifecycleError",
]


FINAL_SAGA_CHAPTER_RESOLVED_EVENT = "saga.final_chapter.resolved"
FINAL_SAGA_CHAPTER_RESOLVED_CAPABILITY = "trigger.event.final_saga_chapter_resolved"
FINAL_SAGA_CHAPTER_CONTROLLED_CONDITION = {
    "field": "controller", "op": "eq", "value": "$source.controller",
}


def final_saga_chapter_runtime_dependencies(program, subscriptions) -> tuple[str, ...]:
    event = FINAL_SAGA_CHAPTER_RESOLVED_EVENT
    from .rules.effect_shape_walkers import nested_effects

    saga_drain = any(
        uses_final_saga_life_drain(effect) for effect in nested_effects((*program.effects, *program.handlers))
    )
    has_drain_marker = FINAL_SAGA_LIFE_AMOUNT_MECHANIC in program.coverage
    if saga_drain or has_drain_marker:
        if (program.event != event or not saga_drain or not has_drain_marker
                or len(program.effects) != 1 or not uses_final_saga_life_drain(program.effects[0])
                or program.target_schema is not None or program.cost_schema is not None
                or program.trigger_limit is not None or program.handlers):
            raise ValueError("Saga mana-value drain requires the exact controlled final chapter subscription")
    if program.event != event:
        if subscriptions is not None and subscriptions.subscription_for(event) is not None:
            raise ValueError("Final Saga chapter observations require a standalone subscription")
        return ()
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    if (program.active_zone != "battlefield" or not program.ability_id.startswith("trigger:")
            or program.event_condition != FINAL_SAGA_CHAPTER_CONTROLLED_CONDITION
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("A final Saga chapter observation requires the current battlefield ability and controller")
    return (FINAL_SAGA_CHAPTER_RESOLVED_CAPABILITY,)


FINAL_SAGA_LIFE_AMOUNT_MECHANIC = "final-saga-mana-value-life"


def final_saga_life_drain_effect():
    from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return {"op": "drain_each_opponent", "amount": ScalarEffectAmountSpec(
        ScalarAmountOrigin.EVENT_OBJECT, characteristic="mana_value",
    ).to_dict()}


def uses_final_saga_life_drain(effect):
    from .replacement.immutable import thaw_value

    return effect.get("op") == "drain_each_opponent" and thaw_value(effect) == final_saga_life_drain_effect()


def final_saga_life_drain_context(program, event, context, cards):
    if FINAL_SAGA_LIFE_AMOUNT_MECHANIC not in program.coverage:
        return {}
    from .scalar_effect_amount_model import SCALAR_REFERENCE_CONTEXT, ScalarAmountOrigin

    ref, identity = context.get("card"), context.get("card_object_identity")
    saga = next((card for card in cards if card.ref == ref
                 and card.logical_object_id == identity and card.zone == "battlefield"), None)
    mana_value = context.get("mana_value")
    if (event != FINAL_SAGA_CHAPTER_RESOLVED_EVENT or saga is None
            or type(mana_value) is not int or mana_value < 0):
        raise ValueError("The final Saga chapter life result requires its committed occurrence")
    return {SCALAR_REFERENCE_CONTEXT: {ScalarAmountOrigin.EVENT_OBJECT.value: {
        "ref": ref, "object_id": saga.object_id, "logical_object_id": identity, "zone": "battlefield",
        "power": None, "toughness": None, "mana_value": mana_value,
    }}}

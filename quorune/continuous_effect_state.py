from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from .continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from .declaration_rule_effects import (
    ContinuousJournalEffect,
    ResolutionDeclarationRuleEffect,
)
from .declaration_fragments import DeclarationRestrictionTemplate
from .object_predicate import ObjectQuerySpec
from .object_query import object_matches_query, object_query_result


class ContinuousEffectStateError(ValueError):
    """Persistent CR 611 state could not be constructed safely."""


@dataclass(frozen=True, slots=True)
class ResolutionContinuousComponent:
    """One layer component of a single resolution-created effect."""

    layer: Layer
    sublayer: str
    operations: tuple[ContinuousOperation, ...]

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "layer", Layer(self.layer))
            operations = tuple(self.operations)
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectStateError(
                "Resolution continuous components require typed layers"
            ) from exc
        if not operations or any(
            not isinstance(operation, ContinuousOperation)
            for operation in operations
        ):
            raise ContinuousEffectStateError(
                "Resolution continuous components require typed operations"
            )
        object.__setattr__(self, "operations", operations)


@dataclass(frozen=True, slots=True)
class ResolutionEffectSource:
    stack_ref: str
    object_id: str | None = None
    logical_object_id: str | None = None
    card_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.stack_ref) is not str or not self.stack_ref:
            raise ContinuousEffectStateError(
                "A resolution-created effect requires its stack identity"
            )
        if (self.object_id is None) != (self.logical_object_id is None):
            raise ContinuousEffectStateError(
                "A resolution source requires both physical and logical IDs"
            )
        for value in (
            self.object_id,
            self.logical_object_id,
            self.card_ref,
        ):
            if value is not None and (type(value) is not str or not value):
                raise ContinuousEffectStateError(
                    "Resolution source identities must be nonempty strings or null"
                )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "stack_ref": self.stack_ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "card_ref": self.card_ref,
        }

    @classmethod
    def from_effect(
        cls, effect: Mapping[str, Any]
    ) -> "ResolutionEffectSource":
        raw = effect.get("_runtime_source")
        if not isinstance(raw, Mapping) or set(raw) != {
            "stack_ref",
            "object_id",
            "logical_object_id",
            "card_ref",
        }:
            raise ContinuousEffectStateError(
                "A resolving continuous effect requires typed source context"
            )
        for field_name in (
            "stack_ref",
            "object_id",
            "logical_object_id",
            "card_ref",
        ):
            value = raw[field_name]
            if value is not None and (type(value) is not str or not value):
                raise ContinuousEffectStateError(
                    f"Resolution source {field_name} must be a nonempty string or null"
                )
        return cls(
            stack_ref=raw["stack_ref"],
            object_id=raw["object_id"],
            logical_object_id=raw["logical_object_id"],
            card_ref=raw["card_ref"],
        )


def resolution_effect_source(
    host: ContinuousEffectStateHost,
    effect: Mapping[str, Any],
    *,
    fallback_card: Any | None = None,
) -> ResolutionEffectSource:
    """Use typed stack context, with an explicit arbiter/direct-call fallback."""

    if "_runtime_source" in effect:
        return ResolutionEffectSource.from_effect(effect)
    if fallback_card is not None:
        return ResolutionEffectSource(
            stack_ref=f"direct:{fallback_card.ref}",
            object_id=fallback_card.object_id,
            logical_object_id=fallback_card.logical_object_id,
            card_ref=fallback_card.ref,
        )
    return ResolutionEffectSource(
        stack_ref=(
            f"direct:{host.state.turn_sequence}:"
            f"{host.state.event_sequence}:{effect.get('op') or 'effect'}"
        )
    )


class ContinuousEffectStateHost(Protocol):
    state: Any

    @property
    def active_seats(self) -> Sequence[str]: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def commit_continuous_effect(
    state: Any,
    effect: ContinuousJournalEffect,
) -> ContinuousJournalEffect:
    """Append one validated effect through the canonical journal owner."""

    if not isinstance(
        effect,
        (ContinuousEffect, ResolutionDeclarationRuleEffect),
    ):
        raise ContinuousEffectStateError(
            "Continuous-effect commits require a typed effect"
        )
    journal = state.continuous_effects
    if journal is None:
        raise ContinuousEffectStateError(
            "Continuous-effect state is unavailable"
        )
    if any(current.effect_id == effect.effect_id for current in journal):
        raise ContinuousEffectStateError(
            "Continuous-effect identity is already committed"
        )
    journal.append(effect)
    return effect


def matching_battlefield_objects(
    host: ContinuousEffectStateHost,
    predicate: ObjectQuerySpec,
) -> tuple[Any, ...]:
    """Snapshot the current CR 611.2c affected set using effective facts."""

    if not isinstance(predicate, ObjectQuerySpec):
        raise ContinuousEffectStateError(
            "Continuous-effect selection requires ObjectQuerySpec"
        )
    if predicate.zones and predicate.zones != ("battlefield",):
        raise ContinuousEffectStateError(
            "Represented resolution-created characteristic effects use the battlefield"
        )
    matches: list[Any] = []
    for seat in host.active_seats:
        for object_id in tuple(
            host.state.players[seat].zones["battlefield"]
        ):
            card = host.state.cards[object_id]
            effective = host._effective_card_data(card)
            types, subtypes, supertypes = host._type_parts(
                str(effective.get("type_line") or "")
            )
            row = object_query_result(
                card,
                effective,
                type_parts=(types, subtypes, supertypes),
                known_to_actor=True,
                attached_to_ref=(
                    host.state.cards[card.attached_to].ref
                    if card.attached_to in host.state.cards
                    else None
                ),
            )
            if object_matches_query(row, predicate):
                matches.append(card)
    return tuple(matches)


def create_resolution_continuous_effect(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    layer: Layer,
    sublayer: str,
    operations: Sequence[ContinuousOperation],
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
) -> ContinuousEffect | None:
    """Commit one immutable effect whose current affected set is locked."""

    journal = host.state.continuous_effects
    if journal is None:
        return None
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities:
        return None
    effect = ContinuousEffect(
        effect_id=host._next_ref("CE"),
        source_id=source.object_id or source.stack_ref,
        layer=layer,
        sublayer=sublayer,
        timestamp=host._next_zone_timestamp(),
        operations=tuple(operations),
        origin=ContinuousEffectOrigin.RESOLUTION,
        duration=duration,
        applies=ObjectQuerySpec(zones=("battlefield",)),
        locked_objects=identities,
    )
    return commit_continuous_effect(host.state, effect)


def create_resolution_continuous_effect_components(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    components: Sequence[ResolutionContinuousComponent],
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
) -> tuple[ContinuousEffect, ...]:
    """Atomically commit one multi-layer effect with one shared timestamp."""

    if not isinstance(source, ResolutionEffectSource):
        raise ContinuousEffectStateError(
            "Resolution continuous components require a typed source"
        )
    values = tuple(components)
    if not values or any(
        not isinstance(value, ResolutionContinuousComponent)
        for value in values
    ):
        raise ContinuousEffectStateError(
            "Resolution continuous component sets must be typed and nonempty"
        )
    if len({(value.layer, value.sublayer) for value in values}) != len(values):
        raise ContinuousEffectStateError(
            "Resolution continuous components require unique layer positions"
        )
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities:
        return ()
    source_id = source.object_id or source.stack_ref
    prototypes = tuple(
        ContinuousEffect(
            effect_id=f"resolution-component:{index}",
            source_id=source_id,
            layer=component.layer,
            sublayer=component.sublayer,
            timestamp=0,
            operations=component.operations,
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=duration,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=identities,
        )
        for index, component in enumerate(values, start=1)
    )
    journal = host.state.continuous_effects
    if journal is None:
        raise ContinuousEffectStateError(
            "Continuous-effect state is unavailable"
        )
    timestamp = host._next_zone_timestamp()
    base_ref = host._next_ref("CE")
    effects = tuple(
        replace(
            prototype,
            effect_id=f"{base_ref}:{index}",
            timestamp=timestamp,
        )
        for index, prototype in enumerate(prototypes, start=1)
    )
    effect_ids = {effect.effect_id for effect in effects}
    if any(current.effect_id in effect_ids for current in journal):
        raise ContinuousEffectStateError(
            "Continuous-effect identity is already committed"
        )
    journal.extend(effects)
    return effects


def create_resolution_declaration_rule_effect(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    restriction: DeclarationRestrictionTemplate,
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
) -> ResolutionDeclarationRuleEffect | None:
    """Commit one typed declaration rule for a locked affected-object set."""

    journal = host.state.continuous_effects
    if journal is None:
        return None
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities:
        return None
    effect = ResolutionDeclarationRuleEffect(
        effect_id=host._next_ref("DR"),
        source_id=source.object_id or source.stack_ref,
        timestamp=host._next_zone_timestamp(),
        restriction=restriction,
        duration=duration,
        locked_objects=identities,
    )
    committed = commit_continuous_effect(host.state, effect)
    if not isinstance(committed, ResolutionDeclarationRuleEffect):
        raise ContinuousEffectStateError(
            "Declaration-rule commit returned the wrong effect type"
        )
    return committed


def active_resolution_effects(
    state: Any, card: Any
) -> tuple[ContinuousEffect, ...]:
    journal = state.continuous_effects
    if not journal:
        return ()
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    return tuple(
        effect
        for effect in journal
        if isinstance(effect, ContinuousEffect)
        and identity in effect.locked_objects
    )


def active_resolution_declaration_rule_effects(
    state: Any,
    card: Any,
) -> tuple[ResolutionDeclarationRuleEffect, ...]:
    """Return declaration rules locked to this battlefield incarnation."""

    journal = state.continuous_effects
    if not journal:
        return ()
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    return tuple(
        effect
        for effect in journal
        if isinstance(effect, ResolutionDeclarationRuleEffect)
        and identity in effect.locked_objects
    )


def expire_end_of_turn_continuous_effects(state: Any) -> int:
    journal = state.continuous_effects
    if journal is None:
        return 0
    retained = [
        effect
        for effect in journal
        if effect.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
    ]
    expired = len(journal) - len(retained)
    journal[:] = retained
    return expired


def expire_control_change_continuous_effects(state: Any, card: Any) -> int:
    """End identity-pinned effects at the first control-change boundary."""

    journal = state.continuous_effects
    if journal is None:
        return 0
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    retained = [
        effect
        for effect in journal
        if not (
            isinstance(effect, ContinuousEffect)
            and effect.duration
            is ContinuousEffectDuration.UNTIL_CONTROL_CHANGE
            and identity in effect.locked_objects
        )
    ]
    expired = len(journal) - len(retained)
    journal[:] = retained
    return expired


__all__ = [
    "ContinuousEffectStateError",
    "ResolutionEffectSource",
    "ResolutionContinuousComponent",
    "active_resolution_declaration_rule_effects",
    "active_resolution_effects",
    "create_resolution_declaration_rule_effect",
    "create_resolution_continuous_effect",
    "create_resolution_continuous_effect_components",
    "expire_control_change_continuous_effects",
    "expire_end_of_turn_continuous_effects",
    "matching_battlefield_objects",
    "resolution_effect_source",
]

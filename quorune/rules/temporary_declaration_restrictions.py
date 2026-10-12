from __future__ import annotations

"""Typed resolution-created combat declaration rules."""

from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from ..ability_fragments import (
    StaticAbilityFragment,
    declaration_restriction_specs,
)
from ..continuous_effect_model import (
    ContinuousEffectDuration,
    ContinuousEffectError,
)
from ..continuous_effect_state import (
    active_resolution_declaration_rule_effects,
    ContinuousEffectStateError,
    ResolutionEffectSource,
    create_resolution_declaration_rule_effect,
)
from ..declaration_fragments import DeclarationRestrictionTemplate
from ..declaration_rule_effects import ResolutionDeclarationRuleEffect
from ..object_query import ObjectQueryResult


TemporaryDeclarationRestrictionKind = Literal[
    "cant_attack",
    "cant_block",
    "cant_attack_or_block",
    "unblockable",
]

TEMPORARY_DECLARATION_RESTRICTION_KINDS = frozenset(
    {
        "cant_attack",
        "cant_block",
        "cant_attack_or_block",
        "unblockable",
    }
)


class TemporaryDeclarationRestrictionError(ValueError):
    """A temporary combat declaration restriction was malformed or stale."""


class TemporaryDeclarationRestrictionHost(Protocol):
    state: Any

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...

    def _public_object_query_result(self, card: Any) -> ObjectQueryResult: ...

    def _combat_keywords(self, card: Any) -> frozenset[str]: ...

    def _effective_ability_fragments(
        self,
        card: Any,
        *,
        error_type: type[Exception] | None = None,
    ) -> tuple[StaticAbilityFragment, ...]: ...


@dataclass(frozen=True, slots=True)
class CurrentDeclarationRestriction:
    """One restriction with the battlefield object that anchors its scope."""

    participant_id: str
    source: Any
    template: DeclarationRestrictionTemplate


def temporary_declaration_restriction(
    kind: TemporaryDeclarationRestrictionKind | str,
) -> DeclarationRestrictionTemplate:
    """Return one of the four closed declaration fragments this owner grants."""

    if kind == "cant_attack":
        return DeclarationRestrictionTemplate(
            template_id="intrinsic-attack-prohibition-v1",
            declarations=("attack",),
            scope="self",
        )
    if kind == "cant_block":
        return DeclarationRestrictionTemplate(
            template_id="intrinsic-block-prohibition-v1",
            declarations=("block",),
            scope="self",
        )
    if kind == "cant_attack_or_block":
        return DeclarationRestrictionTemplate(
            template_id="intrinsic-attack-block-prohibition-v1",
            declarations=("attack", "block"),
            scope="self",
        )
    if kind == "unblockable":
        return DeclarationRestrictionTemplate(
            template_id="intrinsic-unblockable-v1",
            declarations=("block",),
            scope="source_option",
        )
    raise TemporaryDeclarationRestrictionError(
        f"Unsupported temporary declaration restriction {kind!r}"
    )


def commit_temporary_declaration_restriction(
    host: TemporaryDeclarationRestrictionHost,
    *,
    card: Any,
    source: ResolutionEffectSource,
    kind: TemporaryDeclarationRestrictionKind | str,
) -> ResolutionDeclarationRuleEffect:
    """Create one locked declaration rule that lasts until cleanup."""

    if getattr(card, "zone", None) != "battlefield" or bool(
        getattr(card, "phased_out", False)
    ):
        raise TemporaryDeclarationRestrictionError(
            "Temporary declaration restrictions require a phased-in battlefield permanent"
        )
    if not isinstance(source, ResolutionEffectSource):
        raise TemporaryDeclarationRestrictionError(
            "Temporary declaration restrictions require typed resolution source identity"
        )
    restriction = temporary_declaration_restriction(kind)
    try:
        effect = create_resolution_declaration_rule_effect(
            host,
            source=source,
            targets=(card,),
            restriction=restriction,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        )
    except (ContinuousEffectError, ContinuousEffectStateError) as exc:
        raise TemporaryDeclarationRestrictionError(str(exc)) from exc
    if effect is None:
        raise TemporaryDeclarationRestrictionError(
            "Temporary declaration restrictions require continuous-effect state"
        )
    return effect


def current_declaration_restrictions(
    host: TemporaryDeclarationRestrictionHost,
    *,
    error_type: type[Exception] | None = None,
) -> tuple[CurrentDeclarationRestriction, ...]:
    """Compose live static restrictions with resolution-created rules."""

    result: list[CurrentDeclarationRestriction] = []
    live_rules = any(
        isinstance(effect, ResolutionDeclarationRuleEffect) and effect.live_query is not None
        for effect in host.state.continuous_effects or ()
    )
    for source in sorted(
        host.state.cards.values(), key=lambda value: value.ref
    ):
        if source.zone != "battlefield" or source.phased_out:
            continue
        for index, template in enumerate(
            declaration_restriction_specs(
                host._effective_ability_fragments(
                    source,
                    error_type=error_type,
                )
            )
        ):
            result.append(
                CurrentDeclarationRestriction(
                    participant_id=f"{source.ref}:{index}",
                    source=source,
                    template=template,
                )
            )
        resolved = sorted(
            active_resolution_declaration_rule_effects(
                host.state,
                source,
                query_row=(
                    replace(
                        host._public_object_query_result(source),
                        keywords=tuple(sorted(host._combat_keywords(source))),
                    )
                    if live_rules else None
                ),
            ),
            key=lambda effect: (effect.timestamp, effect.effect_id),
        )
        for index, effect in enumerate(resolved):
            result.append(
                CurrentDeclarationRestriction(
                    participant_id=f"{source.ref}:resolved:{index}",
                    source=source,
                    template=effect.restriction,
                )
            )
    return tuple(result)


__all__ = [
    "TEMPORARY_DECLARATION_RESTRICTION_KINDS",
    "CurrentDeclarationRestriction",
    "TemporaryDeclarationRestrictionError",
    "TemporaryDeclarationRestrictionKind",
    "commit_temporary_declaration_restriction",
    "current_declaration_restrictions",
    "temporary_declaration_restriction",
]

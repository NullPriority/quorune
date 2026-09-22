from __future__ import annotations

"""Closed keyword triggers over normalized public event owners."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..object_predicate import ObjectQuerySpec
from ..rules.capabilities import CapabilityRegistry
from ..rules.graveyard_card_targets import OwnGraveyardCardTargetSpec
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


FIXED_KEYWORD_EVENT_EFFECT_MECHANIC = "fixed-keyword-event-effect"
_KEYWORDS = {
    "afflict",
    "annihilator",
    "firebending",
    "ingest",
    "mobilize",
    "soulshift",
}
_PARAMETERIZED = re.compile(
    r"^(?P<keyword>Afflict|Annihilator|Firebending|Mobilize|Soulshift) "
    r"(?P<amount>[1-9]\d*)\.?$",
    re.IGNORECASE,
)


def fixed_keyword_event_effect_mechanics(
    material_line: str,
    keywords: Sequence[str],
) -> tuple[str, ...] | None:
    """Recover closed keyword mechanics before generic fallback lowering."""

    if re.match(
        r"^Cycling(?:\s+\{|[\-\u2013\u2014])",
        material_line,
        re.IGNORECASE,
    ):
        return ("cycling",)
    keyword_values = {str(keyword).casefold() for keyword in keywords}
    event_effect = fixed_keyword_event_effect_spec(material_line)
    if event_effect is not None and event_effect.keyword in keyword_values:
        return (event_effect.keyword,)
    for mechanic in ("cascade", "storm"):
        if mechanic in keyword_values and re.match(
            rf"^{mechanic}\b",
            material_line,
            re.IGNORECASE,
        ):
            return (mechanic,)
    return None


@dataclass(frozen=True, slots=True)
class FixedKeywordEventEffectSpec:
    keyword: str
    amount: int = 1

    def __post_init__(self) -> None:
        if self.keyword not in _KEYWORDS:
            raise ValueError("Keyword event-effect kind is unsupported")
        if type(self.amount) is not int or not 1 <= self.amount <= 20:
            raise ValueError("Keyword event-effect amount is unsupported")
        if self.keyword == "ingest" and self.amount != 1:
            raise ValueError("Ingest has no numeric parameter")

    @property
    def capability_id(self) -> str:
        return f"trigger.keyword.{self.keyword}.fixed"

    @property
    def event(self) -> str:
        return {
            "afflict": "creature.becomes_blocked",
            "annihilator": "creature.attacks",
            "firebending": "creature.attacks",
            "ingest": "damage.dealt.self",
            "mobilize": "creature.attacks",
            "soulshift": "permanent.graveyard.self",
        }[self.keyword]

    @property
    def event_condition(self) -> Mapping[str, Any] | None:
        if self.keyword == "ingest":
            return {
                "all": [
                    {"field": "target_kind", "op": "eq", "value": "player"},
                    {"field": "combat", "op": "truthy", "value": True},
                ]
            }
        if self.keyword in {
            "afflict",
            "annihilator",
            "firebending",
            "mobilize",
        }:
            return {"field": "card", "op": "eq", "value": "$source.ref"}
        return None

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        if self.keyword == "afflict":
            return (
                {
                    "op": "lose_life",
                    "player": "$context.defending_player",
                    "amount": self.amount,
                },
            )
        if self.keyword == "annihilator":
            return (
                {
                    "op": "choose_cards_apnap",
                    "actor": "$controller",
                    "players": ["$context.defending_player"],
                    "zone": "battlefield",
                    "predicate": ObjectQuerySpec(
                        zones=("battlefield",),
                    ).to_dict(),
                    "count": self.amount,
                    "then": "sacrifice",
                    "prompt": "Choose the required permanent(s) to sacrifice.",
                },
            )
        if self.keyword == "firebending":
            return (
                {
                    "op": "mana",
                    "player": "$controller",
                    "color": "R",
                    "amount": self.amount,
                    "retain_until": "end_of_combat",
                    "source": "$source",
                },
            )
        if self.keyword == "ingest":
            return (
                {
                    "op": "exile_top_library_card",
                    "player": "$context.target",
                },
            )
        if self.keyword == "mobilize":
            return (
                {
                    "op": "choose_attacking_token_destinations",
                    "player": "$controller",
                    "quantity": self.amount,
                    "name": "Warrior",
                    "characteristics": {
                        "type_line": "Creature — Warrior",
                        "power": "1",
                        "toughness": "1",
                        "colors": ["R"],
                    },
                },
            )
        return (
            {
                "op": "offer_optional_effect",
                "player": "$controller",
                "effects": [
                    {
                        "op": "return_graveyard_card_to_owner_hand",
                        "card": "$target.0",
                    }
                ],
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.keyword != "soulshift":
            return None
        return OwnGraveyardCardTargetSpec(
            None,
            subtypes_any=("spirit",),
            mana_value_max=self.amount,
        ).to_target_schema()


def fixed_keyword_event_effect_spec(
    material_line: str,
) -> FixedKeywordEventEffectSpec | None:
    normalized = " ".join(material_line.strip().split())
    if re.fullmatch(r"Ingest\.?", normalized, re.IGNORECASE):
        return FixedKeywordEventEffectSpec("ingest")
    match = _PARAMETERIZED.fullmatch(normalized)
    if match is None:
        return None
    try:
        return FixedKeywordEventEffectSpec(
            match.group("keyword").casefold(),
            int(match.group("amount")),
        )
    except ValueError:
        return None


def fixed_keyword_event_effect_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one exact public keyword trigger through existing event owners."""

    spec = fixed_keyword_event_effect_spec(material_line)
    if spec is None or mechanics != (spec.keyword,):
        return None
    gate = explicit_capability_gate(
        spec.capability_id,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    f"{spec.keyword.title()} depends on its typed public "
                    "event-effect owner"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event=spec.event,
        event_condition=spec.event_condition,
        lowerable=True,
        exact=not residual_ids,
        template_id=f"fixed-keyword-{spec.keyword}-event-effect-v1",
        effects=spec.effects,
        target_schema=spec.target_schema,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=(
            spec.keyword,
            FIXED_KEYWORD_EVENT_EFFECT_MECHANIC,
            "cr-603-handling-triggered-abilities",
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = [
    "FIXED_KEYWORD_EVENT_EFFECT_MECHANIC",
    "FixedKeywordEventEffectSpec",
    "fixed_keyword_event_effect_mechanics",
    "fixed_keyword_event_effect_node",
    "fixed_keyword_event_effect_spec",
]

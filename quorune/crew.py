from __future__ import annotations

"""Typed ordinary Crew descriptors and aggregate-power cost coordination.

The represented grammar is the printed ``Crew N`` family from CR 702.122a.
Effects that prohibit a creature from crewing, alternative crew costs, granted
or copied Crew, and "becomes crewed" triggers remain separate capabilities.
"""

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

from .ability_fragment_primitives import CREW_POWER_BONUS_KIND, CrewPowerBonusSpec
from .object_query import exact_numeric_characteristic
from .replacement.immutable import FrozenMap
from .tap_state import dispatch_tapped_cost_group, set_permanent_tapped
from .util import normalize_mana_bundle

if TYPE_CHECKING:
    from .abilities import ActivatedAbility


CREW_HANDLER_ID = "ability.activated.crew.v1"
CREW_MECHANIC_ID = "crew"
CREW_CAPABILITY_ID = "activation.crew.fixed_power"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_CREW = re.compile(
    r"^Crew\s+(?P<threshold>0|[1-9]\d*)\.?$",
    re.IGNORECASE,
)


class CrewAbilityError(ValueError):
    """A Crew descriptor or submitted aggregate-power cost is invalid."""


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise CrewAbilityError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise CrewAbilityError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


@dataclass(frozen=True, slots=True)
class OrdinaryCrewAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    threshold: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ability_id, str)
            or _ABILITY_ID.fullmatch(self.ability_id) is None
        ):
            raise CrewAbilityError("Crew ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise CrewAbilityError(
                "Crew ability line_index must be nonnegative"
            )
        if not isinstance(self.oracle_line, str) or not self.oracle_line:
            raise CrewAbilityError("Crew oracle_line must be nonempty")
        if type(self.threshold) is not int or self.threshold < 0:
            raise CrewAbilityError(
                "Crew threshold must be an exact nonnegative integer"
            )

    @property
    def cost_text(self) -> str:
        return f"Crew {self.threshold}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "OrdinaryCrewAbilitySpec":
        _exact_fields(
            value,
            {"ability_id", "line_index", "oracle_line", "threshold"},
            field="ordinary Crew ability",
        )
        if not isinstance(value["ability_id"], str) or not isinstance(
            value["oracle_line"], str
        ):
            raise CrewAbilityError("Crew text fields must be strings")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            threshold=value["threshold"],
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=(
                "This permanent becomes an artifact creature until end of turn."
            ),
            zones=("battlefield",),
            mana=normalize_mana_bundle(None),
            crew_threshold=self.threshold,
        )


def compile_ordinary_crew_ability(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> OrdinaryCrewAbilitySpec | None:
    """Compile one closed printed Crew line or return ``None``."""

    match = _ORDINARY_CREW.fullmatch(material_line.strip())
    if match is None:
        return None
    return OrdinaryCrewAbilitySpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        threshold=int(match.group("threshold")),
    )


def ordinary_crew_handler_descriptor(
    spec: OrdinaryCrewAbilitySpec,
) -> dict[str, Any]:
    return {
        "handler_id": CREW_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


def legacy_crew_keyword_ability(line: str, line_index: int) -> ActivatedAbility | None:
    """Decode historical Game Record v3 Crew text after typed lines are masked."""
    from .abilities import ActivatedAbility

    match = re.fullmatch(r"Crew\s+(?P<power>\d+)", line, re.IGNORECASE)
    if match is None:
        return None
    threshold = int(match.group("power"))
    return ActivatedAbility(
        ability_id="crew",
        line_index=line_index,
        oracle_line=line,
        cost_text=f"Crew {threshold}",
        effect_text=(
            "This Vehicle becomes an artifact creature until end of turn. "
            f"Crew {threshold}."
        ),
        zones=("battlefield",),
        mana=normalize_mana_bundle(None),
        crew_threshold=threshold,
    )


class CrewCostHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: list[str] | None = None,
        changed_players: list[str] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CrewCandidate:
    """A candidate and its signed contribution to this Crew cost."""

    object_id: str
    object_ref: str
    logical_object_id: str
    power: int

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.object_id,
                self.object_ref,
                self.logical_object_id,
            )
        ):
            raise CrewAbilityError("Crew candidate identity is required")
        if type(self.power) is not int:
            raise CrewAbilityError("Crew candidate power must be an integer")

    @property
    def ref(self) -> str:
        """Compatibility projection for the existing action-offer facade."""

        return self.object_ref


@dataclass(frozen=True, slots=True)
class CrewCostPlan:
    seat: str
    source_object_id: str
    source_logical_object_id: str
    threshold: int
    selected: tuple[CrewCandidate, ...]
    total_power: int
    kind: str = "crew"

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in {"crew", "saddle"}:
            raise CrewAbilityError("Unknown power tap cost")
        label = self.kind.title()
        if not isinstance(self.seat, str) or not self.seat:
            raise CrewAbilityError(f"{label} cost seat is required")
        if not isinstance(self.source_object_id, str) or not self.source_object_id:
            raise CrewAbilityError(f"{label} source identity is required")
        if (
            not isinstance(self.source_logical_object_id, str)
            or not self.source_logical_object_id
        ):
            raise CrewAbilityError(f"{label} source logical identity is required")
        if type(self.threshold) is not int or self.threshold < 0:
            raise CrewAbilityError(f"{label} threshold must be nonnegative")
        if type(self.total_power) is not int:
            raise CrewAbilityError(f"{label} total power must be an integer")
        identities = [candidate.object_id for candidate in self.selected]
        if len(identities) != len(set(identities)):
            raise CrewAbilityError(f"{label} candidates must be distinct")
        if self.total_power != sum(
            candidate.power for candidate in self.selected
        ):
            raise CrewAbilityError(f"{label} total power does not match candidates")
        if self.total_power < self.threshold:
            raise CrewAbilityError(f"Selected creatures do not meet {label} power")
        if not self.selected and self.threshold > 0:
            raise CrewAbilityError(f"Positive {label} costs require a creature")

    def context_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "total_power": self.total_power,
            "source_logical_object_id": self.source_logical_object_id,
            ("crewed_by" if self.kind == "crew" else "saddled_by"): [
                {
                    "object_id": candidate.object_id,
                    "object_ref": candidate.object_ref,
                    "logical_object_id": candidate.logical_object_id,
                    "power": candidate.power,
                }
                for candidate in self.selected
            ],
        }


def crew_candidates(
    host: CrewCostHost,
    seat: str,
    source: Any,
    *,
    kind: str = "crew",
) -> tuple[CrewCandidate, ...]:
    """Return current Crew or Saddle cost candidates in zone order."""

    if not isinstance(seat, str) or seat not in host.state.players:
        raise CrewAbilityError("Crew seat is unavailable")
    if (
        getattr(source, "zone", None) != "battlefield"
        or bool(getattr(source, "phased_out", False))
    ):
        return ()
    result: list[CrewCandidate] = []
    source_is_vehicle = None
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if (
            object_id == source.object_id
            or card.controller != seat
            or card.phased_out
            or card.tapped
        ):
            continue
        effective = host._effective_card_data(card)
        card_types = host._type_parts(
            str(effective.get("type_line") or "")
        )[0]
        if "creature" not in card_types:
            continue
        power = exact_numeric_characteristic(card, effective, "power")
        if power is None:
            raise CrewAbilityError("Effective Crew power is unresolved")
        bonus = sum(
            CrewPowerBonusSpec.from_dict(fragment["value"]).amount
            for fragment in effective.get("ability_fragments", ())
            if fragment["kind"] == CREW_POWER_BONUS_KIND
        ) if kind == "crew" else 0
        if bonus:
            if source_is_vehicle is None:
                source_data = host._effective_card_data(source)
                _, source_subtypes, _ = host._type_parts(str(source_data.get("type_line") or ""))
                source_is_vehicle = "vehicle" in source_subtypes
            if source_is_vehicle:
                power += bonus
        result.append(
            CrewCandidate(
                object_id=card.object_id,
                object_ref=card.ref,
                logical_object_id=card.logical_object_id,
                power=power,
            )
        )
    return tuple(result)


def available_crew_power(candidates: Sequence[CrewCandidate]) -> int:
    """Return the maximum payable total by omitting negative-power objects."""

    return sum(max(0, candidate.power) for candidate in candidates)


def activation_power_tap_cost(host: Any, seat: str, source: Any, ability: Any):
    """Read the same cost and candidate set for the catalog and availability."""
    saddle = getattr(ability, "saddle_threshold", None)
    if saddle is not None:
        return "saddle", saddle, crew_candidates(host, seat, source, kind="saddle")
    crew = host._crew_threshold(ability)
    if crew is None:
        return None
    return "crew", crew, host._crew_candidates(seat, source)


def activation_power_tap_hint(host: Any, seat: str, source: Any, ability: Any):
    cost = activation_power_tap_cost(host, seat, source, ability)
    if cost is None:
        return None
    kind, threshold, candidates = cost
    return {
        "k": kind, "z": "battlefield", "minimum": 0 if threshold == 0 else 1,
        "minimum_total_power": threshold, "legal_refs": [candidate.ref for candidate in candidates],
    }


def _submitted_refs(response: Mapping[str, Any], *, label: str) -> tuple[str, ...]:
    has_cards = "cost_cards" in response
    has_objects = "cost_objects" in response
    if has_cards and has_objects:
        raise CrewAbilityError(f"Submit only one {label} cost-object field")
    raw = (
        response.get("cost_cards")
        if has_cards
        else response.get("cost_objects") if has_objects else ()
    )
    if raw is None:
        raw = ()
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(value, str) or not value for value in raw
    ):
        raise CrewAbilityError(f"{label} cost objects must be nonempty references")
    values = tuple(raw)
    if len(values) != len(set(values)):
        raise CrewAbilityError(f"{label} cost objects must be distinct")
    return values


def prepare_crew_cost(
    host: CrewCostHost,
    *,
    seat: str,
    source: Any,
    threshold: int,
    response: Mapping[str, Any],
    kind: str = "crew",
) -> CrewCostPlan:
    """Validate one submitted Crew or Saddle cost before mutation."""

    if kind not in {"crew", "saddle"}:
        raise CrewAbilityError("Unknown power tap cost")
    label = kind.title()
    if type(threshold) is not int or threshold < 0:
        raise CrewAbilityError(f"{label} threshold is not compiled")
    if (
        source.zone != "battlefield"
        or source.controller != seat
        or source.phased_out
    ):
        raise CrewAbilityError(f"{label} source is no longer available")
    refs = _submitted_refs(response, label=label)
    candidates = crew_candidates(host, seat, source, kind=kind)
    available = {candidate.object_ref: candidate for candidate in candidates}
    if any(ref not in available for ref in refs):
        raise CrewAbilityError(
            f"{label} cost objects must be other untapped creatures you control"
        )
    selected_refs = frozenset(refs)
    selected = tuple(
        candidate
        for candidate in candidates
        if candidate.object_ref in selected_refs
    )
    total_power = sum(candidate.power for candidate in selected)
    return CrewCostPlan(
        seat=seat,
        source_object_id=source.object_id,
        source_logical_object_id=source.logical_object_id,
        threshold=threshold,
        selected=selected,
        total_power=total_power,
        kind=kind,
    )


def commit_crew_cost(
    host: CrewCostHost,
    plan: CrewCostPlan,
) -> list[str]:
    """Revalidate and atomically commit a prepared power tap cost."""

    source = host.state.cards.get(plan.source_object_id)
    if (
        source is None
        or source.zone != "battlefield"
        or source.controller != plan.seat
        or source.phased_out
        or source.logical_object_id != plan.source_logical_object_id
    ):
        raise CrewAbilityError(f"{plan.kind.title()} source changed before cost commitment")
    current = {
        candidate.object_id: candidate
        for candidate in crew_candidates(host, plan.seat, source, kind=plan.kind)
    }
    for expected in plan.selected:
        actual = current.get(expected.object_id)
        if actual != expected:
            raise CrewAbilityError(
                f"A selected {plan.kind.title()} creature changed before cost commitment"
            )
    reason = f"{plan.kind.title()} {plan.threshold} activation cost"
    for candidate in plan.selected:
        set_permanent_tapped(
            host,
            candidate.object_ref,
            actor=plan.seat,
            tapped=True,
            reason=reason,
            logical_object_id=candidate.logical_object_id,
            log=False,
            semantic_events=False,
        )
    dispatch_tapped_cost_group(
        host, (host.state.cards[candidate.object_id] for candidate in plan.selected),
        reason=reason,
    )
    if plan.selected:
        host._log(
            plan.seat,
            f"cost.{plan.kind}",
            (
                f"{plan.seat} tapped {len(plan.selected)} creature(s) with "
                f"{plan.total_power} total power to {plan.kind} {source.ref}."
            ),
            {
                "source": source.ref,
                "threshold": plan.threshold,
                "total_power": plan.total_power,
                "objects": [
                    candidate.object_ref for candidate in plan.selected
                ],
            },
            importance=1,
            changed_objects=[
                candidate.object_id for candidate in plan.selected
            ],
            changed_players=[plan.seat],
        )
    return [candidate.object_id for candidate in plan.selected]


def pay_crew_cost(
    host: CrewCostHost,
    *,
    seat: str,
    source: Any,
    threshold: int,
    response: Mapping[str, Any],
    kind: str = "crew",
) -> tuple[list[str], FrozenMap]:
    plan = prepare_crew_cost(
        host,
        seat=seat,
        source=source,
        threshold=threshold,
        response=response,
        kind=kind,
    )
    paid = commit_crew_cost(host, plan)
    return paid, FrozenMap(plan.context_dict())


__all__ = [
    "CREW_CAPABILITY_ID",
    "CREW_HANDLER_ID",
    "CREW_MECHANIC_ID",
    "CrewAbilityError",
    "CrewCandidate",
    "CrewCostHost",
    "CrewCostPlan",
    "OrdinaryCrewAbilitySpec",
    "available_crew_power",
    "commit_crew_cost",
    "compile_ordinary_crew_ability",
    "crew_candidates",
    "ordinary_crew_handler_descriptor",
    "pay_crew_cost",
    "prepare_crew_cost",
]

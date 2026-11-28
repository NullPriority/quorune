from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Iterable, Mapping

from .event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from .replacement.immutable import FrozenMap
from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .object_query import ObjectQueryResult, query_objects
from .util import stable_json
from .mana_value_filter import ManaValueFilter


class AffectedPermanentSetError(ValueError):
    """A public battlefield-set descriptor or snapshot is invalid."""


class PermanentControllerRelation(str, Enum):
    ANY = "any"
    ACTOR = "actor"
    OPPONENTS = "opponents"
    TARGET_PLAYER = "target_player"


DEFENDING_PLAYER_UNTAP_SET_FIELD = "defending_player_untap_set_version"
TARGET_UNSPENT_MANA_FIELD = "target_unspent_mana_version"
TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD = "target_player_creature_untap_snapshot_version"
COMBAT_PLAYER_LAND_TAP_FIELD = "combat_player_land_tap_version"
COMBAT_DAMAGE_PLAYER_REFERENCE = "$context.target"


_SET_FIELDS = frozenset(
    {"controller_relation", "target_controller", "exclude_source", "query"}
)


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise AffectedPermanentSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class AffectedPermanentSetSpec:
    """Closed immutable description of one public battlefield object set."""

    query: ObjectQuerySpec
    controller_relation: PermanentControllerRelation = (
        PermanentControllerRelation.ANY
    )
    target_controller: str | None = None
    exclude_source: bool = False
    mana_value_filter: ManaValueFilter | None = None

    def __post_init__(self) -> None:
        if self.mana_value_filter is not None and not isinstance(self.mana_value_filter,ManaValueFilter):
            raise AffectedPermanentSetError('Affected permanent mana-value filters must be typed')
        if not isinstance(self.query, ObjectQuerySpec):
            raise AffectedPermanentSetError(
                "Affected permanent sets require a typed object query"
            )
        if not isinstance(self.controller_relation, PermanentControllerRelation):
            raise AffectedPermanentSetError(
                "Affected permanent controller relation is unsupported"
            )
        if self.query.zones != ("battlefield",):
            raise AffectedPermanentSetError(
                "Affected permanent sets are battlefield-only"
            )
        if self.query.owner is not None or self.query.controller is not None:
            raise AffectedPermanentSetError(
                "Affected permanent sets use a typed controller relation"
            )
        if self.query.include_phased_out:
            raise AffectedPermanentSetError(
                "Affected permanent sets exclude phased-out objects"
            )
        if self.query.known_to_actor is not None:
            raise AffectedPermanentSetError(
                "Public battlefield sets do not use knowledge predicates"
            )
        if self.query.exclude_ref is not None:
            raise AffectedPermanentSetError(
                "Affected permanent sets use the typed source exclusion"
            )
        if type(self.exclude_source) is not bool:
            raise AffectedPermanentSetError(
                "Affected permanent source exclusion must be boolean"
            )
        if (self.mana_value_filter is not None and self.mana_value_filter.uses_cast_spell_mana_value
                and (self.query != ObjectQuerySpec(zones=("battlefield",))
                     or self.controller_relation is not PermanentControllerRelation.ANY or self.exclude_source)):
            raise AffectedPermanentSetError("A committed spell value selects all matching battlefield permanents")
        if self.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            _nonempty(
                self.target_controller,
                field="Affected permanent target controller",
            )
        elif self.target_controller is not None:
            raise AffectedPermanentSetError(
                "Only target-player sets accept a target controller"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "controller_relation": self.controller_relation.value,
            "target_controller": self.target_controller,
            "exclude_source": self.exclude_source,
            "query": self.query.canonical_dict(),
            **({'mana_value_filter':self.mana_value_filter.to_dict()} if self.mana_value_filter is not None else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AffectedPermanentSetSpec":
        if isinstance(value, Mapping) and 'selection_kind' in value:
            from .attachment_destruction_model import AttachmentDestructionSetSpec
            return AttachmentDestructionSetSpec.from_dict(value)
        if not isinstance(value, Mapping) or frozenset(value) != _SET_FIELDS | ({'mana_value_filter'} if 'mana_value_filter' in value else set()):
            raise AffectedPermanentSetError(
                "Affected permanent set fields are incomplete or unknown"
            )
        try:
            return cls(
                query=ObjectQuerySpec.from_dict(value["query"]),
                controller_relation=PermanentControllerRelation(
                    value["controller_relation"]
                ),
                target_controller=value["target_controller"],
                exclude_source=value["exclude_source"],
                mana_value_filter=ManaValueFilter.from_dict(value['mana_value_filter']) if 'mana_value_filter' in value else None,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ObjectQueryError,
        ) as exc:
            if isinstance(exc, AffectedPermanentSetError):
                raise
            raise AffectedPermanentSetError(
                "Affected permanent set descriptor is malformed"
            ) from exc

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


KICKED_ENTRY_DESTRUCTION_FIELD = "kicked_entry_destruction"
KICKED_ENTRY_DESTRUCTION_CAPABILITIES = ("trigger.entry.fixed_kicked_result", "permanent.destroy.fixed_set")


class KickedEntryDestructionScope(str, Enum):
    LANDS = "lands"
    OTHER_CREATURES = "other creatures"


@dataclass(frozen=True, slots=True)
class KickedEntryDestructionSpec:
    scope: KickedEntryDestructionScope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, KickedEntryDestructionScope):
            raise AffectedPermanentSetError("Kicked entry requires its closed destruction subject")

    def effect(self) -> dict[str, Any]:
        source = "$source.zone_object"
        return {
            "op": "destroy_all",
            "source": source,
            "set": self.affected_set(kicked=False, source_ref=source).to_dict(),
            KICKED_ENTRY_DESTRUCTION_FIELD: 1,
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> "KickedEntryDestructionSpec":
        from .replacement.immutable import thaw_value

        if not isinstance(effect, Mapping):
            raise AffectedPermanentSetError("Kicked entry destruction requires a typed effect")
        version = effect.get(KICKED_ENTRY_DESTRUCTION_FIELD)
        if type(version) is not int or version != 1:
            raise AffectedPermanentSetError("Kicked entry destruction requires its exact descriptor version")
        baseline = AffectedPermanentSetSpec.from_dict(effect.get("set"))
        if not isinstance(baseline, AffectedPermanentSetSpec):
            raise AffectedPermanentSetError("Kicked entry destruction requires its own battlefield set")
        scope = KickedEntryDestructionScope.OTHER_CREATURES if baseline.exclude_source else KickedEntryDestructionScope.LANDS
        spec = cls(scope)
        if thaw_value(effect) != spec.effect():
            raise AffectedPermanentSetError("Kicked entry destruction requires its exact ordinary set and entering source")
        return spec

    def affected_set(self, *, kicked: bool, source_ref: str | None) -> AffectedPermanentSetSpec:
        other_creatures = self.scope is KickedEntryDestructionScope.OTHER_CREATURES
        return AffectedPermanentSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature" if other_creatures else "land",)),
            controller_relation=PermanentControllerRelation.ANY if kicked else PermanentControllerRelation.ACTOR,
            exclude_source=other_creatures and source_ref is not None,
        )


def defending_player_creature_untap_effect(
    player: str = DEFENDING_PLAYER_REFERENCE, source: str | None = "$source",
) -> dict[str, Any]:
    group = AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        controller_relation=PermanentControllerRelation.TARGET_PLAYER,
        target_controller=player,
    )
    return {
        "op": "set_public_tap_state",
        "source": source,
        "set": group.to_dict(),
        "tapped": False,
        DEFENDING_PLAYER_UNTAP_SET_FIELD: 1,
    }


def decode_defending_player_creature_untap(effect: Mapping[str, Any], *, resolved: bool = False) -> AffectedPermanentSetSpec:
    subject = effect.get("set")
    player = subject.get("target_controller") if isinstance(subject, Mapping) else None
    source = effect.get("source")
    if resolved:
        if (type(player) is not str or not player or player.startswith("$")
                or source is not None and (type(source) is not str or not source or source.startswith("$"))):
            raise AffectedPermanentSetError("Defending-player untap requires its bound attack recipient")
    elif player != DEFENDING_PLAYER_REFERENCE or source != "$source":
        raise AffectedPermanentSetError("Defending-player untap requires the symbolic defender of its attack")
    if (type(effect.get(DEFENDING_PLAYER_UNTAP_SET_FIELD)) is not int or effect.get("tapped") is not False
            or effect != defending_player_creature_untap_effect(player, source)):
        raise AffectedPermanentSetError("Defending-player untap requires the complete current creature set")
    return AffectedPermanentSetSpec.from_dict(subject)


def combat_player_land_tap_effect(
    player: str = COMBAT_DAMAGE_PLAYER_REFERENCE, source: str | None = "$source",
) -> dict[str, Any]:
    group = AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
        controller_relation=PermanentControllerRelation.TARGET_PLAYER,
        target_controller=player,
    )
    return {
        "op": "set_public_tap_state", "source": source, "set": group.to_dict(),
        "tapped": True, COMBAT_PLAYER_LAND_TAP_FIELD: 1,
    }


def fixed_controller_land_untap_effect() -> dict[str, Any]:
    group = AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
        controller_relation=PermanentControllerRelation.ACTOR,
    )
    return {"op": "set_public_tap_state", "source": "$source", "set": group.to_dict(), "tapped": False}


def decode_combat_player_land_tap(effect: Mapping[str, Any], *, resolved: bool = False) -> AffectedPermanentSetSpec:
    raw_set = effect.get("set")
    player = raw_set.get("target_controller") if isinstance(raw_set, Mapping) else None
    source = effect.get("source")
    if resolved:
        if (type(player) is not str or not player or player.startswith("$")
                or source is not None and (type(source) is not str or not source or source.startswith("$"))):
            raise AffectedPermanentSetError("Land tap requires its bound combat damage recipient")
    elif player != COMBAT_DAMAGE_PLAYER_REFERENCE or source != "$source":
        raise AffectedPermanentSetError("Land tap requires the original combat damage recipient")
    if (type(effect.get(COMBAT_PLAYER_LAND_TAP_FIELD)) is not int
            or effect.get("tapped") is not True
            or FrozenMap(effect) != FrozenMap(combat_player_land_tap_effect(player, source))):
        raise AffectedPermanentSetError("Combat damage land tap requires its complete current land set")
    return AffectedPermanentSetSpec.from_dict(raw_set)


def target_player_creature_snapshot_target_schema() -> dict[str, Any]:
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}


def target_player_creature_snapshot_spec(player: str) -> AffectedPermanentSetSpec:
    return AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        controller_relation=PermanentControllerRelation.TARGET_PLAYER,
        target_controller=player,
    )


def decode_target_player_creature_untap_snapshot(
    effect: Mapping[str, Any], *, resolved: bool = False,
) -> AffectedPermanentSetSpec:
    raw_set = effect.get("set")
    player = raw_set.get("target_controller") if isinstance(raw_set, Mapping) else None
    source = effect.get("source")
    if resolved:
        if (
            type(player) is not str or not player or player.startswith("$")
            or source is not None and (type(source) is not str or not source or source.startswith("$"))
        ):
            raise AffectedPermanentSetError("Creature-set restraint requires its resolved player target")
    elif player != "$target.0" or source != "$source":
        raise AffectedPermanentSetError("Creature-set restraint requires the originally targeted player")
    spec = target_player_creature_snapshot_spec(player)
    expected = {
        "op": "set_public_tap_state", "source": source, "set": spec.to_dict(),
        "tapped": True, TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD: 1,
    }
    if (
        FrozenMap(effect) != FrozenMap(expected)
        or type(effect[TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD]) is not int
        or effect["tapped"] is not True
    ):
        raise AffectedPermanentSetError("Creature-set restraint follows the complete original tapped set")
    return spec


def target_unspent_mana_target_schema() -> dict[str, Any]:
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}


def target_unspent_mana_tap_spec(player: str) -> AffectedPermanentSetSpec:
    return AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
        controller_relation=PermanentControllerRelation.TARGET_PLAYER,
        target_controller=player,
    )


def decode_target_unspent_mana_tap(effect: Mapping[str, Any], *, resolved: bool = False) -> AffectedPermanentSetSpec:
    raw_set = effect.get("set")
    player = raw_set.get("target_controller") if isinstance(raw_set, Mapping) else None
    source = effect.get("source")
    if resolved:
        if (type(player) is not str or not player or player.startswith("$")
                or source is not None and (type(source) is not str or not source or source.startswith("$"))):
            raise AffectedPermanentSetError("Land tap and unspent mana loss require the resolved player target")
    elif player != "$target.0" or source != "$source":
        raise AffectedPermanentSetError("Land tap and unspent mana loss require the original player target")
    spec = target_unspent_mana_tap_spec(player)
    expected = {"op": "set_public_tap_state", "source": source, "set": spec.to_dict(),
                "tapped": True, TARGET_UNSPENT_MANA_FIELD: 1}
    if (FrozenMap(effect) != FrozenMap(expected) or type(effect[TARGET_UNSPENT_MANA_FIELD]) is not int
            or effect["tapped"] is not True):
        raise AffectedPermanentSetError("The target loses unspent mana only with its exact current land tap")
    return spec


def select_affected_permanents(
    rows: Iterable[ObjectQueryResult],
    spec: AffectedPermanentSetSpec,
    *,
    actor: str,
    active_seats: Iterable[str],
    apnap_order: Iterable[str],
    source_ref: str | None = None,
) -> tuple[ObjectQueryResult, ...]:
    """Select and canonically APNAP-order one immutable public set."""

    if not isinstance(spec, AffectedPermanentSetSpec):
        raise AffectedPermanentSetError(
            "Affected permanent selection requires a typed set"
        )
    _nonempty(actor, field="Affected permanent actor")
    active = tuple(active_seats)
    order = tuple(apnap_order)
    if (
        actor not in active
        or len(active) != len(set(active))
        or len(order) != len(active)
        or set(order) != set(active)
    ):
        raise AffectedPermanentSetError(
            "Affected permanent selection requires a complete APNAP view"
        )
    if spec.exclude_source:
        _nonempty(source_ref, field="Affected permanent source")
    order_index = {seat: index for index, seat in enumerate(order)}
    if spec.mana_value_filter is not None:
        try:spec.mana_value_filter.resolved_value
        except ValueError as exc:raise AffectedPermanentSetError(str(exc)) from exc
    selected = query_objects(tuple(rows), spec.query)
    from .attachment_destruction_model import AttachmentDestructionSetSpec, select_attachment_destruction_rows
    if isinstance(spec, AttachmentDestructionSetSpec):
        selected = select_attachment_destruction_rows(selected, spec, actor=actor)
    if spec.mana_value_filter is not None:
        selected=tuple(row for row in selected if spec.mana_value_filter.matches(row.mana_value))
    if spec.controller_relation is PermanentControllerRelation.ACTOR:
        selected = tuple(row for row in selected if row.controller == actor)
    elif spec.controller_relation is PermanentControllerRelation.OPPONENTS:
        selected = tuple(row for row in selected if row.controller != actor)
    elif spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
        if spec.target_controller not in active:
            raise AffectedPermanentSetError(
                "Affected permanent target controller is no longer active"
            )
        selected = tuple(
            row for row in selected if row.controller == spec.target_controller
        )
    if spec.exclude_source:
        selected = tuple(row for row in selected if row.ref != source_ref)
    if any(
        row.controller not in order_index
        or not row.ref
        or not row.object_id
        or not row.logical_object_id
        for row in selected
    ):
        raise AffectedPermanentSetError(
            "Affected permanent query returned an invalid public identity"
        )
    deduplicated: dict[str, ObjectQueryResult] = {}
    for row in selected:
        previous = deduplicated.get(row.logical_object_id)
        if previous is not None and previous.object_id != row.object_id:
            raise AffectedPermanentSetError(
                "Affected permanent query repeated one logical object"
            )
        deduplicated[row.logical_object_id] = row
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda row: (
                order_index[row.controller],
                row.logical_object_id,
                row.object_id,
                row.ref,
            ),
        )
    )


__all__ = [
    "AffectedPermanentSetError",
    "AffectedPermanentSetSpec",
    "PermanentControllerRelation",
    "select_affected_permanents",
]

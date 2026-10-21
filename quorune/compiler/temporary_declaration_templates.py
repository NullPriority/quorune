from __future__ import annotations

"""Closed creature declaration rules lasting through the current turn."""

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from ..rules.temporary_declaration_restrictions import (
    TemporaryDeclarationRestrictionKind,
    temporary_declaration_restriction,
)
from .creature_subtypes import canonical_creature_subtype
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from .public_state_queries import fixed_battlefield_query_subject
from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..declaration_rule_effects import declaration_group_query_is_closed
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .fixed_blocker_restrictions import FixedBlockerRestrictionTemplate, fixed_blocker_restriction_template
from .source_block_pairs import SourceBlockPairTemplate, source_block_pair_template


_TARGET_TEMPORARY_DECLARATION_RESTRICTION = re.compile(
    r"target creature can't (?P<restriction>attack or block|attack|block|be blocked) "
    r"this turn\.?",
    re.IGNORECASE,
)
_TARGET_SUBTYPE_UNBLOCKABLE = re.compile(
    r"target (?P<subtype>[A-Za-z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]*) "
    r"can't be blocked this turn\.?",
    re.IGNORECASE,
)
_SOURCE_TEMPORARY_DECLARATION_RESTRICTION = re.compile(
    r"(?P<source>.+?) can't "
    r"(?P<restriction>attack or block|attack|block|be blocked) this turn\.?",
    re.IGNORECASE,
)
_RESTRICTION_KINDS: dict[str, TemporaryDeclarationRestrictionKind] = {
    "attack": "cant_attack",
    "block": "cant_block",
    "attack or block": "cant_attack_or_block",
    "be blocked": "unblockable",
}
_GROUP_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


@dataclass(frozen=True, slots=True)
class GroupTemporaryDeclarationRestrictionTemplate:
    restriction: TemporaryDeclarationRestrictionKind
    target_spec: DirectPermanentTargetSpec | None = None
    maximum_targets: int = 1
    optional_targets: bool = False
    affected_set: AffectedPermanentSetSpec | None = None

    def __post_init__(self) -> None:
        temporary_declaration_restriction(self.restriction)
        if (self.target_spec is None) == (self.affected_set is None):
            raise ValueError("Group declaration requires exactly one target set or live public set")
        if type(self.maximum_targets) is not int or not 1 <= self.maximum_targets <= 6 or type(self.optional_targets) is not bool:
            raise ValueError("Declaration target bounds are malformed")
        if self.target_spec is not None and (not isinstance(self.target_spec, DirectPermanentTargetSpec) or not (
            "creature" in self.target_spec.to_target_schema().get("types_all", ())
            or self.target_spec.to_target_schema().get("types_any") == ["creature"]
        )):
            raise ValueError("Declaration target groups require one shared creature predicate")
        if self.affected_set is not None and (
            not isinstance(self.affected_set, AffectedPermanentSetSpec)
            or not declaration_group_query_is_closed(self.affected_set.query)
            or self.affected_set.exclude_source
            or self.affected_set.controller_relation not in {
                PermanentControllerRelation.ANY,
                PermanentControllerRelation.ACTOR,
                PermanentControllerRelation.OPPONENTS,
            }
            or self.maximum_targets != 1
            or self.optional_targets
        ):
            raise ValueError("Live declaration group predicate is unsupported")

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.target_spec is None:
            return None
        schema = self.target_spec.to_target_schema()
        schema["count"] = self.maximum_targets
        if self.optional_targets:
            schema["up_to"] = self.maximum_targets
        return schema

    def compiled(self) -> tuple[
        str, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]
    ]:
        restriction = temporary_declaration_restriction(self.restriction)
        selection = {"set": self.affected_set.to_dict()} if self.affected_set is not None else {"cards": "$targets"}
        return (
            "temporary-group-declaration-" + self.restriction + "-eot-v2",
            ({"op": "grant_declaration_restriction_until_end_of_turn", "restriction": self.restriction, **selection},),
            self.target_schema,
            ("cr-611-continuous-effects", *restriction.mechanics, *(("cr-115-targets",) if self.target_spec is not None else ())),
        )


def _group_declaration_template(text: str) -> GroupTemporaryDeclarationRestrictionTemplate | None:
    multi = re.fullmatch(r"(?P<optional>up to )?(?P<count>one|two|three|four|five|six) target (?P<subject>.+?) can't (?P<restriction>attack or block|attack|block|be blocked) this turn\.?", text, re.I)
    if multi is not None:
        subject = re.sub(r"\bcreatures\b", "creature", multi["subject"], flags=re.I)
        spec = direct_permanent_target_spec("target " + subject)
        if spec is None or not ("creature" in spec.to_target_schema().get("types_all", ()) or spec.to_target_schema().get("types_any") == ["creature"]):
            return None
        return GroupTemporaryDeclarationRestrictionTemplate(_RESTRICTION_KINDS[multi["restriction"].casefold()], target_spec=spec,
            maximum_targets=_GROUP_COUNTS[multi["count"].casefold()], optional_targets=bool(multi["optional"]))
    group = re.fullmatch(r"(?P<subject>.+?) can't (?P<restriction>attack or block|attack|block|be blocked) this turn\.?", text, re.I)
    if group is None:
        return None
    subject = re.sub(r"^all ", "", group["subject"], flags=re.I)
    without = re.fullmatch(r"(?P<subject>.+?) without (?P<keyword>.+)", subject, re.I)
    if without is not None:
        if without["keyword"].title() not in FIXED_CHARACTERISTIC_KEYWORDS:
            return None
        parsed = fixed_battlefield_query_subject(without["subject"])
        if parsed is not None:
            relation, query, excluded = parsed
            parsed = relation, replace(query, keywords_none=(without["keyword"].casefold(),)), excluded
    else:
        parsed = fixed_battlefield_query_subject(subject)
    if parsed is None or parsed[2] or not declaration_group_query_is_closed(parsed[1]):
        return None
    relation = {"any": PermanentControllerRelation.ANY, "source_controller": PermanentControllerRelation.ACTOR,
                "source_opponents": PermanentControllerRelation.OPPONENTS}.get(parsed[0])
    if relation is None:
        return None
    return GroupTemporaryDeclarationRestrictionTemplate(_RESTRICTION_KINDS[group["restriction"].casefold()],
        affected_set=AffectedPermanentSetSpec(query=parsed[1], controller_relation=relation))


@dataclass(frozen=True, slots=True)
class ActivatedTemporaryDeclarationRestrictionTemplate:
    restriction: TemporaryDeclarationRestrictionKind
    card_reference: str = "$target.0"
    creature_subtype: str | None = None
    target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        temporary_declaration_restriction(self.restriction)
        if self.card_reference not in {"$source", "$target.0"}:
            raise ValueError("Temporary declaration subject is unsupported")
        if self.creature_subtype is not None and (
            self.card_reference != "$target.0"
            or canonical_creature_subtype(self.creature_subtype)
            != self.creature_subtype
        ):
            raise ValueError(
                "Temporary declaration creature subtype is unsupported"
            )
        if self.target_spec is not None and (not isinstance(self.target_spec, DirectPermanentTargetSpec)
                or self.card_reference != "$target.0" or self.creature_subtype is not None):
            raise ValueError("Qualified declaration target must be one closed direct permanent query")

    @property
    def template_id(self) -> str:
        if self.target_spec is not None:
            return f"temporary-target-{self.target_spec.slug}-{self.restriction.replace('_', '-')}-eot-v2"
        subject = (
            "temporary-source"
            if self.card_reference == "$source"
            else f"temporary-target-{self.creature_subtype}"
            if self.creature_subtype is not None
            else "activated-target"
        )
        return f"{subject}-{self.restriction.replace('_', '-')}-eot-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "grant_declaration_restriction_until_end_of_turn",
                "card": self.card_reference,
                "restriction": self.restriction,
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.card_reference == "$source":
            return None
        if self.target_spec is not None:
            return self.target_spec.to_target_schema()
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "count": 1,
        }
        if self.creature_subtype is None:
            schema["types_any"] = ["creature"]
        else:
            schema["subtypes_any"] = [self.creature_subtype]
        return schema

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        restriction = temporary_declaration_restriction(self.restriction)
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            (
                *(("cr-115-targets",) if self.target_schema else ()),
                "cr-611-continuous-effects",
                *restriction.mechanics,
            ),
        )


def activated_temporary_declaration_restriction_effect_template(
    text: str, *, card_name: str = ""
) -> ActivatedTemporaryDeclarationRestrictionTemplate | GroupTemporaryDeclarationRestrictionTemplate | FixedBlockerRestrictionTemplate | SourceBlockPairTemplate | None:
    """Parse one closed activated temporary declaration instruction."""

    return temporary_declaration_restriction_effect_template(
        text,
        card_name=card_name,
        allow_source=True,
    )


def temporary_declaration_restriction_effect_template(
    text: str,
    *,
    card_name: str,
    allow_source: bool,
) -> ActivatedTemporaryDeclarationRestrictionTemplate | GroupTemporaryDeclarationRestrictionTemplate | FixedBlockerRestrictionTemplate | SourceBlockPairTemplate | None:
    """Parse fixed targets, source creatures, or live public creature groups."""

    normalized = " ".join(text.strip().split())
    if source_pair := source_block_pair_template(normalized, allow_source=allow_source):
        return source_pair
    if fixed_blocker := fixed_blocker_restriction_template(normalized):
        return fixed_blocker
    group = _group_declaration_template(normalized)
    if group is not None:
        return group
    match = _TARGET_TEMPORARY_DECLARATION_RESTRICTION.fullmatch(normalized)
    if match is not None:
        return ActivatedTemporaryDeclarationRestrictionTemplate(
            _RESTRICTION_KINDS[match.group("restriction").casefold()]
        )
    subtype_match = _TARGET_SUBTYPE_UNBLOCKABLE.fullmatch(normalized)
    if subtype_match is not None:
        subtype = canonical_creature_subtype(subtype_match.group("subtype"))
        if subtype is not None and subtype != "creature":
            return ActivatedTemporaryDeclarationRestrictionTemplate(
                "unblockable",
                creature_subtype=subtype,
            )
    qualified = re.fullmatch(r"(?P<subject>(?:another )?target .+?) can't "
        r"(?P<restriction>attack or block|attack|block|be blocked) this turn\.?", normalized, re.I)
    if qualified is not None:
        spec = direct_permanent_target_spec(qualified["subject"])
        if spec is not None:
            return ActivatedTemporaryDeclarationRestrictionTemplate(
                _RESTRICTION_KINDS[qualified["restriction"].casefold()], target_spec=spec)
    if not allow_source:
        return None
    source_match = _SOURCE_TEMPORARY_DECLARATION_RESTRICTION.fullmatch(
        normalized
    )
    if source_match is None:
        return None
    source = source_match.group("source").casefold()
    source_names = {"this creature"}
    source_names.update(
        part.strip().casefold() for part in card_name.split("//")
    )
    source_names.discard("")
    if source not in source_names:
        return None
    return ActivatedTemporaryDeclarationRestrictionTemplate(
        _RESTRICTION_KINDS[source_match.group("restriction").casefold()],
        card_reference="$source",
    )


__all__ = [
    "ActivatedTemporaryDeclarationRestrictionTemplate",
    "GroupTemporaryDeclarationRestrictionTemplate",
    "activated_temporary_declaration_restriction_effect_template",
    "temporary_declaration_restriction_effect_template",
]

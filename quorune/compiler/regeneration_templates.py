from __future__ import annotations

"""Closed Oracle lowering for fixed regeneration effects."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..attachment_references import (
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from .direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from .fixed_source_effect_sequences import SOURCE_ZONE_OBJECT


@dataclass(frozen=True, slots=True)
class SelfRegenerationEffectTemplate:
    """One exact ``Regenerate this creature.`` instruction."""

    template_id: str = "regenerate-this-creature-v1"
    effects: tuple[Mapping[str, Any], ...] = (
        {"op": "regenerate", "card": SOURCE_ZONE_OBJECT},
    )
    target_schema: None = None
    mechanics: tuple[str, ...] = ("regenerate",)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class FixedRegenerationEffectTemplate:
    """One regeneration shield over a closed public object reference."""

    template_id: str
    _card_reference: str | Mapping[str, Any]
    _target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        if type(self.template_id) is not str or not self.template_id:
            raise ValueError("Regeneration templates require an identity")
        reference = self._card_reference
        if not (
            reference == SOURCE_ZONE_OBJECT
            or reference == "$target.0"
            or isinstance(reference, Mapping)
        ):
            raise ValueError("Regeneration object reference is unsupported")
        if (reference == "$target.0") is not (
            self._target_spec is not None
        ):
            raise ValueError(
                "Regeneration target reference requires one typed target"
            )
        if self._target_spec is not None and not isinstance(
            self._target_spec,
            DirectPermanentTargetSpec,
        ):
            raise ValueError("Regeneration target predicate is unsupported")
        object.__setattr__(self, "_card_reference", deepcopy(reference))

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "regenerate",
                "card": deepcopy(self._card_reference),
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        return (
            self._target_spec.to_target_schema()
            if self._target_spec is not None
            else None
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            ("regenerate", "cr-115-targets")
            if self._target_spec is not None
            else ("regenerate",)
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def self_regeneration_effect_template(
    text: str,
) -> SelfRegenerationEffectTemplate | None:
    """Lower only the ordinary self-creature regeneration grammar."""

    normalized = " ".join(text.strip().split())
    if re.fullmatch(
        r"regenerate this creature\.",
        normalized,
        re.IGNORECASE,
    ) is None:
        return None
    return SelfRegenerationEffectTemplate()


def fixed_regeneration_effect_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None,
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> SelfRegenerationEffectTemplate | FixedRegenerationEffectTemplate | None:
    """Lower one fixed source, attached-object, or direct-target shield."""

    normalized = " ".join(text.strip().split())
    self_template = self_regeneration_effect_template(normalized)
    if self_template is not None and source_is_permanent is True:
        return self_template

    target_match = re.fullmatch(
        r"regenerate (?P<subject>target (?:artifact creature|artifact|"
        r"creature|permanent))\.",
        normalized,
        re.IGNORECASE,
    )
    if target_match is not None:
        target_spec = direct_permanent_target_spec(target_match.group("subject"))
        if target_spec is None:
            return None
        return FixedRegenerationEffectTemplate(
            template_id=f"regenerate-target-{target_spec.slug}-v1",
            _card_reference="$target.0",
            _target_spec=target_spec,
        )

    attached_match = re.fullmatch(
        r"regenerate (?P<relation>enchanted|equipped) creature\.",
        normalized,
        re.IGNORECASE,
    )
    if attached_match is not None:
        relation = {
            "enchanted": AttachmentReferenceKind.ENCHANTED,
            "equipped": AttachmentReferenceKind.EQUIPPED,
        }[attached_match.group("relation").casefold()]
        if source_attachment_relation is not relation:
            return None
        return FixedRegenerationEffectTemplate(
            template_id=(
                "regenerate-attached-"
                f"{attached_match.group('relation').casefold()}-creature-v1"
            ),
            _card_reference=AttachmentReferenceSpec(
                relation=relation,
                required_card_type="creature",
            ).to_dict(),
        )

    if (
        source_is_permanent is True
        and card_name
        and re.fullmatch(
            rf"regenerate {re.escape(card_name)}\.",
            normalized,
            re.IGNORECASE,
        )
        is not None
    ):
        return FixedRegenerationEffectTemplate(
            template_id="regenerate-named-source-v1",
            _card_reference=SOURCE_ZONE_OBJECT,
        )
    return None


__all__ = [
    "FixedRegenerationEffectTemplate",
    "SelfRegenerationEffectTemplate",
    "fixed_regeneration_effect_template",
    "self_regeneration_effect_template",
]

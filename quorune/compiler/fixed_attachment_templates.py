from __future__ import annotations

"""Closed target-selected source attachment effects."""

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from ..rules.attachment_actions import FIXED_SOURCE_ATTACHMENT_CAPABILITY
from ..attachment_references import AttachmentReferenceKind
from .creature_subtypes import canonical_creature_subtype
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec


@dataclass(frozen=True, slots=True)
class FixedSourceAttachmentTemplate:
    template_id: str
    attachment_kind: str
    controller_only: bool
    exclude_current_attachment: bool = False
    target_spec: DirectPermanentTargetSpec | None = None

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "creature": True,
            "count": 1,
        }
        if self.controller_only:
            schema["controller"] = "you"
        if self.exclude_current_attachment:
            schema["predicate"] = "not_source_attachment"
        if self.target_spec is not None:
            schema = self.target_spec.to_target_schema()
        return (
            self.template_id,
            (
                {
                    "op": "attach",
                    "attachment_kind": self.attachment_kind,
                    "source": "$source.zone_object",
                    "target": "$target.0",
                },
            ),
            schema,
            ("cr-701-3-attach",),
        )


def fixed_source_attachment_effect_template(
    text: str,
    *,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> FixedSourceAttachmentTemplate | None:
    """Lower one source Equipment or Aura attachment instruction."""

    normalized = " ".join(text.strip().split())
    if (
        source_attachment_relation is AttachmentReferenceKind.EQUIPPED
        and re.fullmatch(
            r"Attach it to target creature you control\.?",
            normalized,
            re.IGNORECASE,
        )
    ):
        return FixedSourceAttachmentTemplate(
            template_id="fixed-source-equipment-attach-trigger-v1",
            attachment_kind="equipment",
            controller_only=True,
        )
    restricted = re.fullmatch(
        r"Attach it to (?P<target>target .+ you control)\.?", normalized, re.I
    )
    if source_attachment_relation is AttachmentReferenceKind.EQUIPPED and restricted is not None:
        spec = direct_permanent_target_spec(restricted["target"])
        if spec is not None and not (spec.types_any or spec.types_all) and spec.subtypes_any:
            if all(canonical_creature_subtype(subtype) is not None for subtype in spec.subtypes_any):
                spec = replace(spec, types_all=("creature",))
        if (
            spec is not None
            and spec.controller_relation == "you"
            and (spec.types_any == ("creature",) or spec.types_all == ("creature",))
            and (spec.subtypes_any or spec.subtypes_none)
            and spec == DirectPermanentTargetSpec(
                types_any=spec.types_any,
                types_all=spec.types_all,
                subtypes_any=spec.subtypes_any,
                subtypes_none=spec.subtypes_none,
                controller_relation="you",
            )
        ):
            return FixedSourceAttachmentTemplate(
                template_id="fixed-source-equipment-restricted-attach-trigger-v1",
                attachment_kind="equipment",
                controller_only=True,
                target_spec=spec,
            )
    aura = re.fullmatch(
        r"Attach this Aura to target creature"
        r"(?P<other> other than enchanted creature)?\.?",
        normalized,
        re.IGNORECASE,
    )
    if (
        aura is not None
        and source_attachment_relation is AttachmentReferenceKind.ENCHANTED
    ):
        return FixedSourceAttachmentTemplate(
            template_id="fixed-source-aura-attach-v1",
            attachment_kind="aura",
            controller_only=False,
            exclude_current_attachment=aura.group("other") is not None,
        )
    return None


__all__ = [
    "FixedSourceAttachmentTemplate",
    "fixed_source_attachment_effect_template",
]

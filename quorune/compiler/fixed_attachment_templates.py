from __future__ import annotations

"""Closed target-selected source attachment effects."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..rules.attachment_actions import FIXED_SOURCE_ATTACHMENT_CAPABILITY
from ..attachment_references import AttachmentReferenceKind


@dataclass(frozen=True, slots=True)
class FixedSourceAttachmentTemplate:
    template_id: str
    attachment_kind: str
    controller_only: bool
    exclude_current_attachment: bool = False

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

"""Closed Suspect actions over existing object references and targets."""

import re

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..suspect_model import SUSPECT_MECHANIC, SUSPECT_OPERATION
from .direct_target import direct_permanent_target_spec


def suspect_effect_template(text, *, source_is_permanent, source_attachment_relation):
    body = " ".join(text.casefold().split()).removesuffix(".")
    if body == "all suspected creatures are no longer suspected":
        return "clear-suspected-creatures-v1", ({"op": SUSPECT_OPERATION, "clear": "suspected_creatures"},), None, (SUSPECT_MECHANIC,)
    if not body.startswith("suspect "):
        return None
    subject = body.removeprefix("suspect ")
    if subject in {"this creature", "this permanent"} and source_is_permanent is True:
        return "suspect-source-v1", ({"op": SUSPECT_OPERATION, "card": "$source.zone_object"},), None, (SUSPECT_MECHANIC,)
    if subject == "enchanted creature" and source_attachment_relation is AttachmentReferenceKind.ENCHANTED:
        reference = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature")
        return "suspect-enchanted-creature-v1", ({"op": SUSPECT_OPERATION, "card": reference.to_dict()},), None, (SUSPECT_MECHANIC,)
    optional = subject.startswith("up to one ")
    target = direct_permanent_target_spec(subject.removeprefix("up to one "))
    if target is None or (target.types_any != ("creature",) and "creature" not in target.types_all):
        return None
    schema = target.to_target_schema()
    if optional:
        schema.pop("count")
        schema["up_to"] = 1
    return "suspect-target-v1", ({"op": SUSPECT_OPERATION, "card": "$target.0"},), schema, (SUSPECT_MECHANIC, "cr-115-targets")


def normalize_entering_source_suspect(body):
    return re.sub(r"^Suspect it(?=\.|$)", "Suspect this permanent", body, count=1, flags=re.I)

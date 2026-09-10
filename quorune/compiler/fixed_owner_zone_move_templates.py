from __future__ import annotations

"""Closed single-object and selected owner-destination zone-move grammar."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Mapping

from ..attachment_references import (
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import FIXED_OWNER_ZONE_MOVE_MECHANIC
from ..rules.source_references import (
    SourceReferenceSpec,
    source_self_permanent_type,
)
from ..targets import TargetGroup
from ..util import stable_json
from .fixed_entry_return_requirements import FixedEntryReturnRequirementSpec


class FixedOwnerZoneMoveReference(str, Enum):
    TARGET = "target"
    SOURCE = "source"
    SOURCE_ATTACHMENT = "source_attachment"
    PUBLIC_CHOICE = "public_choice"


@dataclass(frozen=True, slots=True)
class FixedOwnerZoneMoveTemplate:
    """One closed single-object or selected owner-destination zone move."""

    reference: FixedOwnerZoneMoveReference
    destination: str
    subject_id: str
    target_schema: Mapping[str, Any] | None = None
    library_position: str | int | None = None
    shuffle: bool = False
    attachment_reference: AttachmentReferenceSpec | None = None
    choice_query: ObjectQuerySpec | None = None
    choice_players: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, FixedOwnerZoneMoveReference):
            raise ValueError("Owner-zone move reference is unsupported")
        if self.destination not in {"graveyard", "hand", "library"}:
            raise ValueError("Owner-zone move destination is unsupported")
        if type(self.subject_id) is not str or not self.subject_id:
            raise ValueError("Owner-zone move subject identity is required")
        if type(self.shuffle) is not bool:
            raise ValueError("Owner-zone move shuffle flag must be boolean")
        if self.destination == "library":
            if self.shuffle is not (self.library_position is None):
                raise ValueError(
                    "Owner-library moves require one position or shuffle"
                )
            if self.library_position not in {None, "top", "bottom", 2, 3, 4}:
                raise ValueError("Owner-library position is unsupported")
        elif self.library_position is not None or self.shuffle:
            raise ValueError(
                "Only owner-library moves accept position or shuffle"
            )

        targeted = self.reference is FixedOwnerZoneMoveReference.TARGET
        attached = self.reference is FixedOwnerZoneMoveReference.SOURCE_ATTACHMENT
        selected = self.reference is FixedOwnerZoneMoveReference.PUBLIC_CHOICE
        if targeted:
            if self.target_schema is None:
                raise ValueError("Targeted owner-zone moves require a target schema")
            schema = deepcopy(dict(self.target_schema))
            group = TargetGroup.from_mapping(schema)
            if group.min_targets != 1 or group.max_targets != 1:
                raise ValueError("Owner-zone moves require exactly one target")
            object.__setattr__(self, "target_schema", schema)
        elif self.target_schema is not None:
            raise ValueError("Nontargeted owner-zone moves cannot carry targets")
        if attached is not isinstance(
            self.attachment_reference,
            AttachmentReferenceSpec,
        ):
            raise ValueError("Attachment owner-zone reference is inconsistent")
        if selected is not isinstance(self.choice_query, ObjectQuerySpec):
            raise ValueError("Selected owner-zone move query is inconsistent")
        if selected:
            if (
                self.destination != "hand"
                or self.choice_players not in {"controller", "all"}
            ):
                raise ValueError("Selected owner-zone move scope is unsupported")
        elif self.choice_query is not None or self.choice_players is not None:
            raise ValueError("Only selected owner-zone moves carry a query")

    @property
    def template_id(self) -> str:
        identity = {
            "reference": self.reference.value,
            "destination": self.destination,
            "subject": self.subject_id,
            "target_schema": dict(self.target_schema or {}),
            "library_position": self.library_position,
            "shuffle": self.shuffle,
            "attachment_reference": (
                self.attachment_reference.to_dict()
                if self.attachment_reference is not None
                else None
            ),
            "choice_query": (
                self.choice_query.to_dict()
                if self.choice_query is not None
                else None
            ),
            "choice_players": self.choice_players,
        }
        digest = hashlib.sha256(stable_json(identity).encode("utf-8")).hexdigest()
        return f"fixed-owner-zone-move-{digest[:16]}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        if self.reference is FixedOwnerZoneMoveReference.PUBLIC_CHOICE:
            assert self.choice_query is not None
            choice = deepcopy(
                FixedEntryReturnRequirementSpec(
                    count=1,
                    predicate=self.choice_query,
                    excludes_source=False,
                    sacrifice_source_unless_paid=False,
                ).effects[0]
            )
            if self.choice_players == "all":
                choice["players"] = "all"
            return (choice,)
        card: Any = {
            FixedOwnerZoneMoveReference.TARGET: "$target.0",
            FixedOwnerZoneMoveReference.SOURCE: "$source.zone_object",
            FixedOwnerZoneMoveReference.SOURCE_ATTACHMENT: (
                self.attachment_reference.to_dict()
                if self.attachment_reference is not None
                else None
            ),
        }[self.reference]
        if self.shuffle:
            return ({"op": "shuffle_into_library", "card": card},)
        effect: dict[str, Any] = {
            "op": "move",
            "card": card,
            "destination": self.destination,
        }
        if self.library_position is not None:
            effect["position"] = self.library_position
        return (effect,)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            FIXED_OWNER_ZONE_MOVE_MECHANIC,
            "fixed-public-zone-move",
            *(("return-to-owner-hand",) if self.destination == "hand" else ()),
            *(
                ("cr-115-targets",)
                if self.reference is FixedOwnerZoneMoveReference.TARGET
                else ()
            ),
        )

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


_BATTLEFIELD_TARGET_TYPES = {
    "artifact": ("artifact",),
    "artifact or creature": ("artifact", "creature"),
    "artifact or enchantment": ("artifact", "enchantment"),
    "artifact, creature, or enchantment": (
        "artifact",
        "creature",
        "enchantment",
    ),
    "creature": ("creature",),
    "creature or land": ("creature", "land"),
    "enchantment": ("enchantment",),
    "land": ("land",),
    "permanent": (),
}
_ORDINAL_POSITIONS = {"second": 2, "third": 3, "fourth": 4}


def _battlefield_owner_zone_target_schema(
    subject: str,
) -> Mapping[str, Any] | None:
    normalized = " ".join(subject.casefold().split())
    schema: dict[str, Any] = {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "count": 1,
    }
    if normalized in _BATTLEFIELD_TARGET_TYPES:
        types = _BATTLEFIELD_TARGET_TYPES[normalized]
        if types:
            schema["types_any"] = list(types)
        return schema
    if normalized == "creature or vehicle":
        schema["characteristic_forms_any"] = [
            {
                "types_all": ["creature"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": ["vehicle"],
                "supertypes_any": [],
            },
        ]
        return schema
    if normalized == "nonland historic permanent":
        schema["types_none"] = ["land"]
        schema["characteristic_forms_any"] = [
            {
                "types_all": ["artifact"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": [],
                "supertypes_any": ["legendary"],
            },
            {
                "types_all": [],
                "subtypes_any": ["saga"],
                "supertypes_any": [],
            },
        ]
        return schema
    return None


def _targeted_owner_library_move(
    text: str,
) -> FixedOwnerZoneMoveTemplate | None:
    match = re.fullmatch(
        r"Put target (?P<subject>.+?) "
        r"(?:(?P<edge>on top of|on the bottom of) its owner['’]s library|"
        r"into its owner['’]s library "
        r"(?P<ordinal>second|third|fourth) from the top)\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    subject = match.group("subject")
    schema = _battlefield_owner_zone_target_schema(subject)
    if schema is None:
        return None
    edge = (match.group("edge") or "").casefold()
    position: str | int = (
        "top"
        if edge == "on top of"
        else "bottom"
        if edge == "on the bottom of"
        else _ORDINAL_POSITIONS[match.group("ordinal").casefold()]
    )
    return FixedOwnerZoneMoveTemplate(
        reference=FixedOwnerZoneMoveReference.TARGET,
        destination="library",
        subject_id="battlefield-" + "-".join(subject.casefold().split()),
        target_schema=schema,
        library_position=position,
    )


def _targeted_public_card_owner_move(
    text: str,
) -> FixedOwnerZoneMoveTemplate | None:
    normalized = " ".join(text.strip().split())
    graveyard = re.fullmatch(
        r"Put target (?P<subject>card|artifact, instant, or sorcery card) "
        r"from a graveyard on the bottom of its owner['’]s library\.?",
        normalized,
        re.IGNORECASE,
    )
    if graveyard is not None:
        subject = graveyard.group("subject").casefold()
        schema: dict[str, Any] = {
            "zones": ["graveyard"],
            "categories": ["card"],
            "count": 1,
        }
        if subject != "card":
            schema["types_any"] = ["artifact", "instant", "sorcery"]
        return FixedOwnerZoneMoveTemplate(
            reference=FixedOwnerZoneMoveReference.TARGET,
            destination="library",
            subject_id="graveyard-" + subject.replace(" ", "-"),
            target_schema=schema,
            library_position="bottom",
        )
    if re.fullmatch(
        r"Put target face-up exiled card into its owner['’]s graveyard\.?",
        normalized,
        re.IGNORECASE,
    ):
        return FixedOwnerZoneMoveTemplate(
            reference=FixedOwnerZoneMoveReference.TARGET,
            destination="graveyard",
            subject_id="face-up-exiled-card",
            target_schema={
                "zones": ["exile"],
                "categories": ["card"],
                "face_down": False,
                "count": 1,
            },
        )
    return None


def _source_or_attachment_owner_move(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> FixedOwnerZoneMoveTemplate | None:
    normalized = " ".join(text.strip().split())
    attachment = re.fullmatch(
        r"(?P<verb>Return|Put|Shuffle) enchanted creature "
        r"(?:(?:to|on) (?:the )?top of its owner['’]s library|"
        r"to its owner['’]s hand|into its owner['’]s library "
        r"(?P<ordinal>third) from the top|into its owner['’]s library)\.?",
        normalized,
        re.IGNORECASE,
    )
    if attachment is not None:
        if source_attachment_relation is not AttachmentReferenceKind.ENCHANTED:
            return None
        verb = attachment.group("verb").casefold()
        destination = "hand" if verb == "return" else "library"
        shuffle = verb == "shuffle"
        position: str | int | None = (
            None
            if shuffle or destination == "hand"
            else _ORDINAL_POSITIONS[attachment.group("ordinal").casefold()]
            if attachment.group("ordinal")
            else "top"
        )
        return FixedOwnerZoneMoveTemplate(
            reference=FixedOwnerZoneMoveReference.SOURCE_ATTACHMENT,
            destination=destination,
            subject_id=f"enchanted-creature-{verb}",
            library_position=position,
            shuffle=shuffle,
            attachment_reference=AttachmentReferenceSpec(
                AttachmentReferenceKind.ENCHANTED,
                "creature",
            ),
        )
    if source_is_permanent is not True:
        return None
    references = SourceReferenceSpec(card_name)
    source = re.fullmatch(
        rf"(?P<verb>Return|Put) (?P<subject>this (?:Aura|creature|Equipment)|"
        rf"{references.regex_pattern}) (?:(?:to its owner['’]s hand)|"
        r"(?:on top of its owner['’]s library))\.?",
        normalized,
        re.IGNORECASE,
    )
    if source is None:
        return None
    subject = source.group("subject")
    if subject.casefold().startswith("this "):
        if source_self_permanent_type(subject) is None:
            return None
    elif not references.matches(subject):
        return None
    destination = "hand" if "hand" in normalized.casefold() else "library"
    return FixedOwnerZoneMoveTemplate(
        reference=FixedOwnerZoneMoveReference.SOURCE,
        destination=destination,
        subject_id="source-" + "-".join(subject.casefold().split()),
        library_position="top" if destination == "library" else None,
    )


def _public_choice_owner_hand_move(
    text: str,
) -> FixedOwnerZoneMoveTemplate | None:
    normalized = " ".join(text.strip().split())
    controller = re.fullmatch(
        r"Return a tapped land you control to its owner['’]s hand\.?",
        normalized,
        re.IGNORECASE,
    )
    each_player = re.fullmatch(
        r"Each player returns a creature they control to its owner['’]s hand\.?",
        normalized,
        re.IGNORECASE,
    )
    if controller is None and each_player is None:
        return None
    all_players = each_player is not None
    return FixedOwnerZoneMoveTemplate(
        reference=FixedOwnerZoneMoveReference.PUBLIC_CHOICE,
        destination="hand",
        subject_id=(
            "each-player-creature" if all_players else "controller-tapped-land"
        ),
        choice_query=ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(("creature",) if all_players else ("land",)),
            tapped=(None if all_players else True),
            controller="$actor",
            known_to_actor=True,
        ),
        choice_players="all" if all_players else "controller",
    )


def _targeted_owner_hand_move(
    text: str,
) -> FixedOwnerZoneMoveTemplate | None:
    match = re.fullmatch(
        r"Return target (?P<subject>creature or Vehicle) "
        r"to its owner['’]s hand\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    schema = _battlefield_owner_zone_target_schema(match.group("subject"))
    assert schema is not None
    return FixedOwnerZoneMoveTemplate(
        reference=FixedOwnerZoneMoveReference.TARGET,
        destination="hand",
        subject_id="battlefield-creature-or-vehicle",
        target_schema=schema,
    )


def fixed_owner_zone_move_effect_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> FixedOwnerZoneMoveTemplate | None:
    return (
        _targeted_owner_library_move(text)
        or _targeted_public_card_owner_move(text)
        or _targeted_owner_hand_move(text)
        or _public_choice_owner_hand_move(text)
        or _source_or_attachment_owner_move(
            text,
            card_name=card_name,
            source_is_permanent=source_is_permanent,
            source_attachment_relation=source_attachment_relation,
        )
    )


__all__ = [
    "fixed_owner_zone_move_effect_template",
    "FixedOwnerZoneMoveReference",
    "FixedOwnerZoneMoveTemplate",
]

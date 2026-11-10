from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from ..carddb import CardRecord
from ..characteristic_evaluation import type_parts
from ..entry_counter_model import (
    EntryCounterError,
    IntrinsicEntryCounter,
    intrinsic_entry_counters,
)
from ..intrinsic_basic_land_mana import (
    INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
    IntrinsicBasicLandManaSpec,
    intrinsic_basic_land_mana_specs,
)
from ..shared_type_doors import SHARED_TYPE_FORM, SHARED_TYPE_FORM_CAPABILITY, SharedTypeDoors
from .ir_model import SourceSpan


INTRINSIC_ENTRY_COUNTER_CAPABILITY = "counter.producer.intrinsic_entry"
SAGA_LORE_COUNTER_CAPABILITY = "counter.producer.saga_lore"
SAGA_FINAL_CHAPTER_CAPABILITY = "state_based.saga_final_chapter"
_REASON_FIELD = "reason"


@dataclass(frozen=True, slots=True)
class CardFormRuleNode:
    """One rules-derived declaration pinned to an exact card face type line."""

    face_id: str
    source_text: str
    span: SourceSpan
    entry_counter: IntrinsicEntryCounter
    capability_dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.face_id or not self.source_text:
            raise ValueError("Card-form rule nodes require face and source text")
        if self.span.start != 0 or self.span.end != len(self.source_text):
            raise ValueError("Card-form rule source span must cover the type line")
        if self.span.line != 1:
            raise ValueError("Card-form rule source span must use line one")
        expected = (
            (
                self.entry_counter.capability_id,
                SAGA_FINAL_CHAPTER_CAPABILITY,
            )
            if self.entry_counter.required_type == "saga"
            else (self.entry_counter.capability_id,)
        )
        if self.capability_dependencies != expected:
            raise ValueError(
                "Intrinsic entry nodes require their fine-grained capability"
            )

    def descriptor(self) -> dict[str, Any]:
        return {
            "kind": "intrinsic_entry_counter",
            "counter_name": self.entry_counter.counter_name,
            "amount": self.entry_counter.amount,
            "required_type": self.entry_counter.required_type,
            "rule_id": self.entry_counter.rule_id,
        }


@dataclass(frozen=True, slots=True)
class IntrinsicBasicLandManaRuleNode:
    """One CR 305.6 declaration pinned to an exact card face type line."""

    face_id: str
    source_text: str
    span: SourceSpan
    intrinsic_mana: IntrinsicBasicLandManaSpec
    capability_dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.face_id or not self.source_text:
            raise ValueError("Card-form rule nodes require face and source text")
        if self.span.start != 0 or self.span.end != len(self.source_text):
            raise ValueError("Card-form rule source span must cover the type line")
        if self.span.line != 1:
            raise ValueError("Card-form rule source span must use line one")
        if self.capability_dependencies != (
            INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
        ):
            raise ValueError(
                "Intrinsic mana nodes require their fine-grained capability"
            )

    def descriptor(self) -> dict[str, Any]:
        return self.intrinsic_mana.to_dict()


@dataclass(frozen=True, slots=True)
class SharedTypeDoorRuleNode:
    face_id: str
    source_text: str
    span: SourceSpan
    faces: tuple[str, str]
    capability_dependencies: tuple[str, ...] = (SHARED_TYPE_FORM_CAPABILITY,)

    def __post_init__(self) -> None:
        SharedTypeDoors(self.faces)
        if (self.face_id != self.faces[0] or not self.source_text
                or self.span != SourceSpan(start=0, end=len(self.source_text), line=1)
                or self.capability_dependencies != (SHARED_TYPE_FORM_CAPABILITY,)):
            raise ValueError("Shared type line requires a pinned first-face type-line declaration")

    def descriptor(self) -> dict[str, Any]:
        return {"kind": SHARED_TYPE_FORM, "schema_version": 1, "faces": list(self.faces)}


CardFormNode = CardFormRuleNode | IntrinsicBasicLandManaRuleNode | SharedTypeDoorRuleNode


@dataclass(frozen=True, slots=True)
class CardFormRuleCompilation:
    nodes: tuple[CardFormNode, ...]
    residuals: tuple[dict[str, Any], ...]


def _face_sources(
    record: CardRecord,
    *,
    compiled_face_ids: Sequence[str],
) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
    if record.faces:
        if len(compiled_face_ids) != len(record.faces):
            raise ValueError("Compiled face count does not match card faces")
        return tuple(
            (
                face_id,
                str(face.get("type_line") or record.type_line),
                {
                    **face,
                    "keywords": face.get("keywords") or record.keywords,
                },
            )
            for face_id, face in zip(
                compiled_face_ids, record.faces, strict=True
            )
        )
    if len(compiled_face_ids) != 1:
        raise ValueError("A single-faced card requires one compiled face")
    return (
        (
            compiled_face_ids[0],
            record.type_line,
            {
                "loyalty": record.loyalty,
                "defense": record.defense,
                "keywords": record.keywords,
            },
        ),
    )


def compile_intrinsic_entry_counter_forms(
    record: CardRecord,
    *,
    compiled_face_ids: Sequence[str],
) -> CardFormRuleCompilation:
    """Compile CR 306.5b/310.4b and retain unsupported forms as residuals."""

    nodes: list[CardFormRuleNode] = []
    residuals: list[dict[str, Any]] = []
    for face_id, type_line, characteristics in _face_sources(
        record,
        compiled_face_ids=compiled_face_ids,
    ):
        card_types, subtypes, _supertypes = type_parts(type_line)
        if not (
            card_types.intersection({"planeswalker", "battle"})
            or "saga" in subtypes
        ):
            continue
        normalized_keywords = {
            " ".join(str(value).casefold().split())
            for value in characteristics.get("keywords") or ()
        }
        if "saga" in subtypes and "read ahead" in normalized_keywords:
            # CR 714.3b is owned by the source-spanned Read Ahead keyword
            # program because its amount depends on that Saga's final chapter.
            continue
        try:
            counters = intrinsic_entry_counters(
                characteristics,
                card_types=tuple(sorted(card_types)),
                card_subtypes=tuple(sorted(subtypes)),
                keywords=tuple(characteristics.get("keywords") or ()),
            )
        except EntryCounterError as exc:
            span = SourceSpan(start=0, end=len(type_line), line=1)
            residuals.append(
                {
                    "face_id": face_id,
                    "residual_id": "card-form-intrinsic-entry-counter",
                    "kind": "card_form_rule",
                    "text": type_line,
                    "span": asdict(span),
                    "material": True,
                    _REASON_FIELD: str(exc),
                    "blockers": (
                        [
                            SAGA_LORE_COUNTER_CAPABILITY,
                            SAGA_FINAL_CHAPTER_CAPABILITY,
                        ]
                        if "saga" in subtypes
                        else [INTRINSIC_ENTRY_COUNTER_CAPABILITY]
                    ),
                }
            )
            continue
        for counter in counters:
            nodes.append(
                CardFormRuleNode(
                    face_id=face_id,
                    source_text=type_line,
                    span=SourceSpan(start=0, end=len(type_line), line=1),
                    entry_counter=counter,
                    capability_dependencies=(
                        (
                            counter.capability_id,
                            SAGA_FINAL_CHAPTER_CAPABILITY,
                        )
                        if counter.required_type == "saga"
                        else (counter.capability_id,)
                    ),
                )
            )
    return CardFormRuleCompilation(
        nodes=tuple(nodes),
        residuals=tuple(residuals),
    )


def compile_intrinsic_basic_land_mana_forms(
    record: CardRecord,
    *,
    compiled_face_ids: Sequence[str],
) -> CardFormRuleCompilation:
    """Compile the CR 305.6 abilities derived from printed face type lines."""

    nodes: list[IntrinsicBasicLandManaRuleNode] = []
    for face_id, type_line, _characteristics in _face_sources(
        record,
        compiled_face_ids=compiled_face_ids,
    ):
        for spec in intrinsic_basic_land_mana_specs(type_line):
            nodes.append(
                IntrinsicBasicLandManaRuleNode(
                    face_id=face_id,
                    source_text=type_line,
                    span=SourceSpan(start=0, end=len(type_line), line=1),
                    intrinsic_mana=spec,
                    capability_dependencies=(
                        INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
                    ),
                )
            )
    return CardFormRuleCompilation(nodes=tuple(nodes), residuals=())


def compile_card_form_rules(
    record: CardRecord,
    *,
    compiled_face_ids: Sequence[str],
) -> CardFormRuleCompilation:
    """Compile all rules-derived declarations for one pinned card record."""

    entry = compile_intrinsic_entry_counter_forms(
        record,
        compiled_face_ids=compiled_face_ids,
    )
    mana = compile_intrinsic_basic_land_mana_forms(
        record,
        compiled_face_ids=compiled_face_ids,
    )
    return CardFormRuleCompilation(
        nodes=(*entry.nodes, *mana.nodes, *_shared_type_forms(record, compiled_face_ids)),
        residuals=(*entry.residuals, *mana.residuals),
    )


def _shared_type_forms(record: CardRecord, compiled_face_ids: Sequence[str]) -> tuple[SharedTypeDoorRuleNode, ...]:
    from .room_bindings import printed_room_form

    faces = printed_room_form(record)
    if faces is None:
        return ()
    if tuple(compiled_face_ids) != faces:
        raise ValueError("Compiled Room face identities disagree with their printed form")
    type_line = record.faces[0]["type_line"]
    return (SharedTypeDoorRuleNode(faces[0], type_line, SourceSpan(0, len(type_line), 1), faces),)


__all__ = [
    "CardFormRuleNode",
    "CardFormNode",
    "CardFormRuleCompilation",
    "IntrinsicBasicLandManaRuleNode",
    "INTRINSIC_ENTRY_COUNTER_CAPABILITY",
    "SAGA_LORE_COUNTER_CAPABILITY",
    "SAGA_FINAL_CHAPTER_CAPABILITY",
    "compile_card_form_rules",
    "compile_intrinsic_basic_land_mana_forms",
    "compile_intrinsic_entry_counter_forms",
]

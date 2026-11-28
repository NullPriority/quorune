"""Classify exact printed face types and attachment source context."""

from ..attachment_references import AttachmentReferenceKind
from ..characteristic_evaluation import type_parts


PERMANENT_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }
)
SPELL_CARD_TYPES = frozenset({"instant", "sorcery"})


def face_type_context(
    type_line: str,
) -> tuple[
    frozenset[str],
    bool,
    bool,
    bool | None,
    AttachmentReferenceKind | None,
]:
    """Return exact card types and closed resolution-source context."""

    parsed_card_types, subtypes, _supertypes = type_parts(type_line)
    card_types = frozenset(parsed_card_types)
    permanent = bool(card_types.intersection(PERMANENT_CARD_TYPES))
    spell = bool(card_types.intersection(SPELL_CARD_TYPES))
    support_source = (
        True
        if permanent and not spell
        else False
        if spell and not permanent
        else None
    )
    attachment_relations = tuple(
        relation
        for subtype, relation in (
            ("aura", AttachmentReferenceKind.ENCHANTED),
            ("equipment", AttachmentReferenceKind.EQUIPPED),
            ("fortification", AttachmentReferenceKind.FORTIFIED),
        )
        if subtype in subtypes
    )
    attachment_relation = (
        attachment_relations[0] if len(attachment_relations) == 1 else None
    )
    return (
        card_types,
        permanent,
        spell,
        support_source,
        attachment_relation,
    )

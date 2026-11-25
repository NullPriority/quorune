from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping
from uuid import UUID


def _text(value: Any, limit: int) -> str | None:
    if type(value) not in {str, int}:
        return None
    text = str(value).strip()
    return text if 0 < len(text) <= limit and text.isprintable() else None


@dataclass(frozen=True, slots=True)
class CardPrinting:
    """Presentation metadata for a physical copy with a separate Oracle identity."""

    scryfall_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    language: str | None = None
    finish: str | None = None
    provider_id: str | None = None
    faces: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> CardPrinting | None:
        if not isinstance(value, Mapping):
            return None
        raw_id = _text(value.get("scryfall_id"), 36)
        try:
            printing_id = str(UUID(raw_id)) if raw_id else None
        except ValueError:
            printing_id = None
        faces = value.get("faces")
        printing = cls(
            scryfall_id=printing_id,
            set_code=_text(value.get("set_code"), 16),
            collector_number=_text(value.get("collector_number"), 25),
            language=_text(value.get("language"), 12),
            finish=_text(value.get("finish"), 20),
            provider_id=_text(value.get("provider_id"), 36),
            faces=tuple(
                name for face in faces[:10] if (name := _text(face, 140))
            ) if isinstance(faces, (list, tuple)) else (),
        )
        return printing if printing.to_dict() else None

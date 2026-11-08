from __future__ import annotations

"""Printed face values for current and prospective card characteristics."""

from ..util import parse_mana_symbols


def has_shared_permanent_type_line(record) -> bool:
    """Recognize a split permanent through its two printed type lines."""
    if record.layout != "split" or len(record.faces) != 2:
        return False
    left, right = record.faces
    type_line = left.get("type_line")
    return (
        isinstance(type_line, str) and bool(type_line.strip())
        and type_line == right.get("type_line")
        and record.is_permanent_spell and not (record.is_instant or record.is_sorcery)
    )


def printed_face_mana_value(face):
    if 'cmc' in face:return float(face['cmc'])
    if 'mana_value' in face:return float(face['mana_value'])
    return float(sum(int(symbol) if symbol.isdigit() else 0 if symbol=='X' else 2 if symbol.startswith('2/') else 1 for symbol in parse_mana_symbols(str(face.get('mana_cost') or ''))))


def current_face_mana_value(record, face):
    """CR 202.3b: an original transforming back face uses its front cost."""
    if (record.layout == 'transform' and len(record.faces) == 2
            and face.get('name') == record.faces[1].get('name')):
        return printed_face_mana_value(record.faces[0])
    return printed_face_mana_value(face)


def printed_face_colors(face):
    if face.get('colors'):return list(face['colors'])
    colors=set(face.get('color_indicator') or ())
    for symbol in parse_mana_symbols(str(face.get('mana_cost') or '')):colors.update(c for c in 'WUBRG' if c in symbol)
    return [c for c in 'WUBRG' if c in colors]

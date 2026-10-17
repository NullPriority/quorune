from __future__ import annotations

"""Printed face mana values for current spells and prospective casting rows."""

from ..util import parse_mana_symbols


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

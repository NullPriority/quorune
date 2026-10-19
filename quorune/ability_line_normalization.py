from __future__ import annotations

"""Pure source-line normalization before the existing activated cost parser."""

import re


def strip_inline_reminder_and_granted_text(line: str) -> str:
    """Keep only activated abilities printed on the source itself.

    Parenthetical reminder text and quoted abilities granted to other objects
    can contain colons, but neither is an activated ability of this card.
    Basic land types receive their mana abilities from the intrinsic type
    owner rather than from their parenthesized Oracle reminder.
    """

    preserve_token_declaration = bool(
        re.search(
            r":\s*Create .+? creature tokens? with [\"“]"
            r"This token can't (?:block|be blocked)\.",
            line,
            re.IGNORECASE,
        )
    )
    result: list[str] = []
    parenthetical_depth = 0
    quoted = False
    for character in line:
        if character in {'"', "“", "”"} and parenthetical_depth == 0:
            quoted = not quoted
            if preserve_token_declaration:
                result.append(character)
            continue
        if quoted:
            if preserve_token_declaration:
                result.append(character)
            continue
        if character == "(":
            parenthetical_depth += 1
            continue
        if character == ")" and parenthetical_depth:
            parenthetical_depth -= 1
            continue
        if parenthetical_depth == 0:
            result.append(character)
    return "".join(result).strip()



def normalized_ability_line(raw_line: str) -> tuple[str, str | None]:
    line = raw_line.strip()
    line = strip_inline_reminder_and_granted_text(line)
    for keyword, effect in (
        ("Cycling", "Draw a card."),
        (
            "Equip",
            "Attach this Equipment to target creature you control. "
            "Activate only as a sorcery.",
        ),
    ):
        match = re.fullmatch(
            rf"{keyword}\s+(?P<cost>(?:\{{[^{{}}]+\}})+)",
            line,
            re.IGNORECASE,
        )
        if match:
            discard = ", Discard this card" if keyword == "Cycling" else ""
            return f"{match.group('cost')}{discard}: {effect}", keyword
    return line, None

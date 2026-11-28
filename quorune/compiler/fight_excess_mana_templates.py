"""Printed paired fight-result and turn-red-retention productions."""

from . import regex_working_set as re
from ..rules.fight_excess_mana import (
    FIGHT_EXCESS_MECHANIC, FIGHT_EXCESS_TEMPLATE, RETAIN_ALL_RED_MECHANIC,
    fight_excess_effect, fight_excess_schema, red_retention_effect,
)


_FIGHT = re.compile(
    r"Target creature you control fights target creature an opponent controls\. "
    r"If the creature the opponent controls is dealt excess damage this way, add that much \{R\}\.",
    re.IGNORECASE,
)
_RETAIN = re.compile(
    r"Until end of turn, you don['’]t lose unspent red mana as steps and phases end\.",
    re.IGNORECASE,
)


def fight_excess_mana_template(body):
    material = " ".join(body.split())
    if _FIGHT.fullmatch(material):
        return (FIGHT_EXCESS_TEMPLATE, (fight_excess_effect(),), fight_excess_schema(),
                (FIGHT_EXCESS_MECHANIC, "cr-120-damage", "cr-115-targets"))
    if _RETAIN.fullmatch(material):
        return ("mana-retain-all-red-until-cleanup-v1", (red_retention_effect(),), None, (RETAIN_ALL_RED_MECHANIC,))
    return None

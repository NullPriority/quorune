"""Close the targeted untap and its additional combat/main instruction together."""

from . import regex_working_set as re
from ..extra_combat_model import ExtraCombatUntap, EXTRA_COMBAT_MAIN_MECHANIC, extra_combat_effect, main_combat_target


_PATTERN = re.compile(
    r"Untap target creature\. After this main phase, there is an additional combat phase followed by an additional main phase\.",
    re.IGNORECASE,
)


def main_phase_combat_template(text):
    if _PATTERN.fullmatch(" ".join(text.split())) is None:
        return None
    return ("target-untap-and-main-combat-v1", (extra_combat_effect(ExtraCombatUntap.TARGET_CREATURE),),
            main_combat_target(), (EXTRA_COMBAT_MAIN_MECHANIC,"cr-115-targets"))

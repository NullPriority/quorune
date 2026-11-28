"""Compile the closed same-cohort creature Addendum."""

from ..rules.main_phase_creature_cohort import COHORT_ADDENDUM_TEMPLATE, creature_cohort_addendum_effect


def creature_cohort_addendum_template(base, bonus):
    if (
        base.casefold() != "creatures you control gain indestructible until end of turn."
        or bonus.casefold() != (
            "put a +1/+1 counter on each of those creatures and they gain vigilance until end of turn."
        )
    ):
        return None
    return COHORT_ADDENDUM_TEMPLATE, (creature_cohort_addendum_effect(),), None, ()

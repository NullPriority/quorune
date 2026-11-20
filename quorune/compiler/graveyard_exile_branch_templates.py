"""Match the complete exclusive result clauses before generic sentence splitting."""

from . import regex_working_set as re
from ..graveyard_exile_branches_model import GraveyardExileBranchRecipe, GraveyardExileBranchSpec


_MANDATORY = (
    r'Exile target card from a graveyard\. If it was a creature card, you gain 3 life\. '
    r'Otherwise, you draw a card\.'
)
_OPTIONAL = (
    r'You may exile target card from a graveyard\. If a creature card is exiled this way, you gain 2 life\. '
    r'If a noncreature card is exiled this way, this creature gets \+1/\+1 until end of turn\.'
)


def parse_graveyard_exile_branch(text: str) -> GraveyardExileBranchSpec | None:
    candidate = text.strip()
    if re.fullmatch(_MANDATORY, candidate, re.IGNORECASE):
        return GraveyardExileBranchSpec(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
    if re.fullmatch(_OPTIONAL, candidate, re.IGNORECASE):
        return GraveyardExileBranchSpec(GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST)
    return None


def graveyard_exile_branch_template(text: str):
    spec = parse_graveyard_exile_branch(text)
    if spec is None:
        return None

    from ..rules.graveyard_exile_branches import graveyard_exile_branch_target_schema

    target_schema = graveyard_exile_branch_target_schema()
    mechanics = ('exile', 'fixed-public-zone-move', 'cr-115-targets')
    template = 'exclusive-graveyard-exile-outcome-v1'
    if spec.optional:
        from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

        return FixedOptionalEffectTemplate(
            template, (spec.effect(),), target_schema, (FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics),
        ).compiled()
    return template, (spec.effect(),), target_schema, mechanics

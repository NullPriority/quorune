"""Match the complete exclusive result clauses before generic sentence splitting."""

from . import regex_working_set as re
from ..graveyard_exile_branches_model import GraveyardExileBranchRecipe, GraveyardExileBranchSpec
from ..rules.source_references import SourceReferenceSpec


_MANDATORY = (
    r'Exile target card from a graveyard\. If it was a creature card, you gain 3 life\. '
    r'Otherwise, you draw a card\.'
)
_OPTIONAL = (
    r'You may exile target card from a graveyard\. If a creature card is exiled this way, you gain 2 life\. '
    r'If a noncreature card is exiled this way, this creature gets \+1/\+1 until end of turn\.'
)


_LAND_MANA = (
    r'Exile target card from a graveyard\. If it was a land card, add \{R\} or \{G\}\. '
    r'Otherwise, you gain 2 life and (?P<source>[^.]+) deals 2 damage to each opponent\.'
)


def parse_graveyard_exile_branch(text: str, *, source_name: str | None = None) -> GraveyardExileBranchSpec | None:
    candidate = text.strip()
    match = re.fullmatch(_LAND_MANA, candidate, re.IGNORECASE)
    if match is not None and source_name and SourceReferenceSpec(source_name).matches(match['source']):
        return GraveyardExileBranchSpec(GraveyardExileBranchRecipe.LAND_MANA_OR_LIFE_AND_DAMAGE, schema_version=2)
    if re.fullmatch(_MANDATORY, candidate, re.IGNORECASE):
        return GraveyardExileBranchSpec(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
    if re.fullmatch(_OPTIONAL, candidate, re.IGNORECASE):
        return GraveyardExileBranchSpec(GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST)
    return None


def graveyard_exile_branch_template(text: str, *, source_name: str | None = None):
    spec = parse_graveyard_exile_branch(text, source_name=source_name)
    if spec is None:
        return None

    from ..rules.graveyard_exile_branches import graveyard_exile_branch_target_schema

    target_schema = graveyard_exile_branch_target_schema()
    mechanics = ('exile', 'fixed-public-zone-move', 'cr-115-targets')
    template = 'graveyard-land-mana-or-life-damage-v2' if spec.classifies_land else 'exclusive-graveyard-exile-outcome-v1'
    if spec.optional:
        from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

        return FixedOptionalEffectTemplate(
            template, (spec.effect(),), target_schema, (FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics),
        ).compiled()
    return template, (spec.effect(),), target_schema, mechanics

"""Complete self-source half-life damage clauses over the fixed damage owner."""
from . import regex_working_set as re
from ..fixed_damage_set_model import PlayerDamageRelation
from ..fractional_quantity_model import HalfQuantitySpec
from ..per_player_life_damage_model import PerPlayerLifeDamageSpec, PER_PLAYER_LIFE_DAMAGE_MECHANIC
from ..rules.source_references import SourceReferenceSpec


def per_player_life_damage_template(text, *, source_name, compile_fixed):
    match = re.fullmatch(r"(?P<source>.+?) deals damage to each (?P<players>player|opponent) equal to "
        r"half that player['’]s life total, rounded (?P<rounding>up|down)\.", text.strip(), re.I)
    if match is None or not SourceReferenceSpec(source_name).matches(match['source']):
        return None
    relation = PlayerDamageRelation.ALL if match['players'].casefold() == 'player' else PlayerDamageRelation.OPPONENTS
    spec = PerPlayerLifeDamageSpec(HalfQuantitySpec(match['rounding'].casefold()), relation)
    fixed = compile_fixed(source_name + ' deals 1 damage to each player.')
    if fixed[0] is None or len(fixed[1]) != 1 or fixed[2] is not None:
        return None
    effect = dict(fixed[1][0])
    if effect.get('op') != 'damage_fixed_set' or effect.get('source') != '$source' or effect.get('amount') != 1:
        return None
    effect.update(amount=spec.to_dict(), groups=[{'kind': 'players', 'relation': relation.value}])
    return 'per-player-half-current-life-damage-v1', (effect,), None, tuple(dict.fromkeys((PER_PLAYER_LIFE_DAMAGE_MECHANIC, *fixed[3])))

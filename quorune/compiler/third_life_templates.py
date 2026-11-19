"""The complete fixed all-player third-life loss clause."""
from . import regex_working_set as re
from .fractional_player_templates import FractionalPlayerTemplate
from ..third_quantity_model import ThirdQuantitySpec,THIRD_LIFE_MECHANIC


def third_life_effect_template(text):
    normalized=' '.join(text.strip().split())
    if re.fullmatch(r'Each player loses a third of their life, rounded up\.',normalized,re.I) is None:
        return None
    effect={'op':'lose_life','scope':'all_active_players','amount':ThirdQuantitySpec('up').to_dict()}
    return FractionalPlayerTemplate('third-player-life-v1',(effect,),None,(THIRD_LIFE_MECHANIC,'cr-119-life'))

"""Pinned Enchant wording; arbitrary reminders and result riders remain residual."""
from . import regex_working_set as re
from ..enchant_public_state_model import EnchantPublicStateKind, EnchantPublicStateSpec


_MODIFIED_REMINDER = 'Equipment, Auras its controller controls, and counters are modifications.'


def parse_enchant_public_state_requirement(text):
    material = ' '.join(text.strip().split())
    if re.fullmatch(r'Enchant creature with another Aura attached to it\.?', material, re.IGNORECASE):
        return EnchantPublicStateSpec(EnchantPublicStateKind.CREATURE_WITH_OTHER_AURA)
    if re.fullmatch(r'Enchant nonbasic land\.?', material, re.IGNORECASE):
        return EnchantPublicStateSpec(EnchantPublicStateKind.NONBASIC_LAND)
    if re.fullmatch(r'Enchant modified creature(?: \(' + re.escape(_MODIFIED_REMINDER) + r'\))?\.?',
            material, re.IGNORECASE):
        return EnchantPublicStateSpec(EnchantPublicStateKind.MODIFIED_CREATURE)
    return None

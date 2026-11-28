"""Compile two ordered nontarget opponent choices with announced token sizes."""

from . import regex_working_set as re
from ..fixed_token_production import FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID
from ..rules.paired_token_creators import (
    PAIRED_TOKEN_QUANTITY, PAIRED_TOKEN_STATS, paired_token_choice_effect, paired_token_node_capabilities,
)

PAIRED_TOKEN_TEMPLATE = "paired-announced-token-creation-v1"
_CHOICE = r"Choose an opponent\. You and that player each create "
_PAIRED = re.compile(
    rf"{_CHOICE}an? X/X (?P<size>[^.]+ creature token)\. "
    rf"{_CHOICE}X (?P<count>[^.]+ creature tokens)\.", re.IGNORECASE,
)


def paired_token_spell_compiler(compile_effect):
    def compile_paired_tokens(text, **context):
        match = _PAIRED.fullmatch(" ".join(text.split()))
        if match is not None:
            from .token_templates import fixed_token_creation_effect_template

            size = fixed_token_creation_effect_template(f'Create a 2/2 {match["size"]}.')
            count = fixed_token_creation_effect_template(f'Create one {match["count"][:-1]}.')
            if (size is not None and size.target_schema is None
                    and count is not None and count.target_schema is None):
                effects = (
                    paired_token_choice_effect(size.effect, PAIRED_TOKEN_STATS),
                    paired_token_choice_effect(count.effect, PAIRED_TOKEN_QUANTITY),
                )
                if paired_token_node_capabilities(effects, None, None):
                    return (PAIRED_TOKEN_TEMPLATE, effects, None,
                            tuple(dict.fromkeys((*size.mechanics, *count.mechanics,
                                                FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID))))
        return compile_effect(text, **context)

    return compile_paired_tokens

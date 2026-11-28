"""Compile an owned creature exile paired with a sacrificed source's split return."""
from hashlib import sha256

from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_creature_settlement import MECHANIC, instruction, owned_target_schema
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re

_EXILE = re.compile(r"\{1\}, \{T\}: Exile target creature you own\.", re.I)


def linked_creature_settlement_activation_compiler(fallback, *, material_rows, card_name, card_types):
    if "land" not in card_types:
        return fallback
    source = SourceReferenceSpec(card_name).regex_pattern
    return_text = re.compile(
        rf"\{{2\}}, \{{T\}}, Sacrifice (?:this land|{source}): Return a creature card exiled with (?:this land|{source}) "
        rf"to the battlefield under your control\. Return each other card exiled with (?:this land|{source}) "
        r"to its owner['’]s hand\.", re.I,
    )
    exiles = [span.line for _, text, span in material_rows if _EXILE.fullmatch(text)]
    returns = [span.line for _, text, span in material_rows if return_text.fullmatch(text)]
    if len(exiles) != 1 or len(returns) != 1:
        return fallback
    exile, = exiles
    settlement, = returns
    pair = LINKED_ABILITY_BINDING_PREFIX + sha256(f"{card_name}:{exile}:{settlement}".encode()).hexdigest()[:24]
    templates = {
        exile: ("linked-owned-creature-exile-v17", (instruction(pair, "exile"),), owned_target_schema(),
                (MECHANIC, "exile", "cr-115-targets")),
        settlement: ("linked-creature-and-remainder-return-v17", (instruction(pair, "choose"),), None, (MECHANIC,)),
    }

    def compile_activation(**values):
        recipe = templates.get(values["span"].line)
        if recipe is not None:
            values["effect_template"] = lambda _body, **_source: recipe
        return fallback(**values)

    return compile_activation

from __future__ import annotations

"""Closed layer-safe collection values over the existing object-query parser."""

from dataclasses import replace
import re

from ..characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec
from .fixed_numbers import fixed_number
from ..object_predicate import ObjectQuerySpec


def fixed_devotion_condition(text: str):
    from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
    match = re.fullmatch(r'(?P<quantity>your devotion to (?:white|blue|black|red|green)(?: and (?:white|blue|black|red|green))?) is less than (?P<amount>one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+)', text, re.I)
    if match is None:
        return None
    quantity = collection_quantity(match['quantity'], compile_count=lambda _text: None)
    amount = fixed_number(match['amount'])
    if quantity is None or amount < 1:
        return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,
        amount=amount - 1, quantity=quantity, schema_version=2)


def collection_quantity(value: str, *, compile_count) -> CharacteristicQuantitySpec | None:
    counter_total = re.fullmatch(r'(?:the )?(?:number of )?counters (?:among|on) (?P<objects>.+)', value, re.I)
    if counter_total is not None:
        quantity = compile_count(counter_total['objects'])
        if quantity is None or quantity.schema_version != 1 or quantity.query is None or quantity.query.zones != ('battlefield',):
            return None
        return replace(quantity, schema_version=2, reduction=CharacteristicQuantityReduction.TOTAL_COUNTERS)
    devotion = re.fullmatch(r'your devotion to (?P<first>white|blue|black|red|green)(?: and (?P<second>white|blue|black|red|green))?', value, re.I)
    if devotion is not None:
        colors={'white':'W','blue':'U','black':'B','red':'R','green':'G'}
        selected=tuple(sorted(colors[color.casefold()] for color in (devotion['first'],devotion['second']) if color))
        if len(set(selected))!=len(selected):return None
        return CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',)),schema_version=2,
            reduction=CharacteristicQuantityReduction.DEVOTION,mana_colors=selected)
    symbol = re.fullmatch(
        r"(?P<color>white|blue|black|red|green) mana symbols in the mana costs of (?P<objects>.+)",
        value, re.IGNORECASE,
    )
    match = re.fullmatch(
        r"(?P<kind>basic land types?|card types?|colors?) among (?P<objects>.+)", value,
        re.IGNORECASE,
    )
    if symbol is not None:
        match = symbol
        reduction = CharacteristicQuantityReduction.COLORED_MANA_SYMBOLS
    elif match is not None:
        reduction = {
            "basic land type": CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES,
            "basic land types": CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES,
            "card type": CharacteristicQuantityReduction.DISTINCT_CARD_TYPES,
            "card types": CharacteristicQuantityReduction.DISTINCT_CARD_TYPES,
            "color": CharacteristicQuantityReduction.DISTINCT_COLORS,
            "colors": CharacteristicQuantityReduction.DISTINCT_COLORS,
        }[match.group("kind").casefold()]
    else:
        match = re.fullmatch(
            r"(?:the )?(?P<kind>greatest|highest|total) mana value (?:among|of) (?P<objects>.+)",
            value, re.IGNORECASE,
        )
        if match is None:
            return None
        reduction = (CharacteristicQuantityReduction.TOTAL_MANA_VALUE if match.group("kind").casefold() == "total"
                     else CharacteristicQuantityReduction.MAXIMUM_MANA_VALUE)
    quantity = compile_count(match.group("objects"))
    if quantity is None or quantity.schema_version != 1:
        return None
    if quantity.query is None:
        return None
    try:
        if reduction is CharacteristicQuantityReduction.DISTINCT_CARD_TYPES or (
            quantity.query.zones == ("graveyard",)
            and re.search(r"\bcards?\b", match.group("objects"), re.IGNORECASE)
        ):
            quantity = replace(quantity, query=replace(quantity.query, token=False))
        mana_colors = ()
        if symbol is not None:
            color_symbols = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
            mana_colors = (color_symbols[symbol.group("color").casefold()],)
        return replace(quantity, schema_version=2, reduction=reduction, mana_colors=mana_colors)
    except ValueError:
        return None


def additive_quantity(value: str, *, compile_value) -> CharacteristicQuantitySpec | None:
    match = re.fullmatch(r"(?P<first>(?:the )?number of .+?) plus (?P<second>(?:the )?number of .+)", value, re.IGNORECASE)
    if match is None:
        return None
    terms = tuple(public_card_quantity(compile_value(match.group(field)), match.group(field)) for field in ("first", "second"))
    if any(term is None or term.schema_version == 3 for term in terms):
        return None
    try:
        return CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE, schema_version=3,
            reduction=CharacteristicQuantityReduction.SUM_QUANTITIES, terms=terms,
        )
    except ValueError:
        return None


def fixed_quantity_arithmetic(value: str, *, compile_value) -> CharacteristicQuantitySpec | None:
    constant = r"one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+"
    prefix = re.fullmatch(rf"(?P<constant>{constant}) plus (?P<value>.+)", value, re.IGNORECASE)
    suffix = re.fullmatch(rf"(?P<value>.+) plus (?P<constant>{constant})", value, re.IGNORECASE)
    multiple = re.fullmatch(rf"(?P<multiplier>twice|(?:{constant}) times) (?P<value>.+)", value, re.IGNORECASE)
    match = prefix or suffix or multiple
    if match is None:
        return None
    quantity = public_card_quantity(compile_value(match.group("value")), match.group("value"))
    if quantity is None:
        return None
    multiplier = 1
    offset = 0
    if multiple is None:
        offset = fixed_number(match.group("constant"))
    elif multiple.group("multiplier").casefold() == "twice":
        multiplier = 2
    else:
        multiplier = fixed_number(multiple.group("multiplier").split()[0])
    try:
        return replace(quantity, schema_version=5 if quantity.schema_version==5 else 2, multiplier=quantity.multiplier * multiplier,
                       offset=quantity.offset * multiplier + offset)
    except ValueError:
        return None


def public_card_quantity(quantity, source_text: str):
    """Make the card domain explicit only for new versioned value forms."""
    if quantity is not None and quantity.query is not None and quantity.query.zones == ("graveyard",) and re.search(r"\bcards?\b", source_text, re.IGNORECASE):
        return replace(quantity, schema_version=2, query=replace(quantity.query, token=False))
    return quantity


def recipient_attachment_quantity(text: str):
    """Count one closed current attachment set belonging to an attached recipient."""
    match = re.fullmatch(r'(?P<objects>Equipment|Auras?|Aura and Equipment|Auras and Equipment) attached to it', text, re.I)
    if match is None:
        return None
    objects = match['objects'].casefold()
    if objects == 'equipment':
        predicate = ObjectQuerySpec(zones=('battlefield',), types_all=('artifact',), subtypes_all=('equipment',))
    elif objects in {'aura','auras'}:
        predicate = ObjectQuerySpec(zones=('battlefield',), types_all=('enchantment',), subtypes_all=('aura',))
    else:
        predicate = ObjectQuerySpec(zones=('battlefield',), types_any=('artifact','enchantment'), subtypes_any=('aura','equipment'))
    return CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.ATTACHED_TO_RECIPIENT,
        query=predicate, schema_version=2)

from __future__ import annotations

from copy import deepcopy
import re
from typing import Callable, Mapping, Sequence, Any

from ..query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_CAPABILITY,
    PUBLIC_QUERY_AMOUNT_KIND,
    PublicQueryAmountError,
    PublicQueryAmountSpec,
)
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..characteristic_fragments import CharacteristicQuantitySpec
from ..rules.source_references import SourceReferenceSpec
from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value
from .live_battlefield_quantities import resolution_quantity
from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
from .declared_effect_amounts import (
    DECLARED_EFFECT_AMOUNT_MECHANIC,
    declared_amount_shape_context,
    declared_amount_dependencies,
)
from .scalar_effect_amounts import scalar_amount_shape_context
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from .optional_effect_templates import OPTIONAL_EFFECT_OPERATION
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, SCALAR_AMOUNT_CAPABILITY
from ..scalar_effect_amount_model import ScalarAmountOrigin, scalar_amount_specs
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_MANA_VALUE_CAPABILITY, PAID_SACRIFICE_MANA_VALUE_MECHANIC,
    PAID_SACRIFICE_STAT_CAPABILITY, PAID_SACRIFICE_STAT_MECHANIC,
)
from ..scalar_effect_amount_model import TARGET_HAND_COUNT_MECHANIC,TARGET_HAND_COUNT_CAPABILITY
from ..multikicker_result_amounts import MULTIKICKER_AMOUNT_MECHANIC, MULTIKICKER_AMOUNT_CAPABILITY
from ..mana_spent_amounts import MANA_COLOR_AMOUNT_MECHANIC, MANA_COLOR_AMOUNT_CAPABILITY
from ..cast_event_amounts import CAST_MANA_VALUE_MECHANIC,CAST_MANA_VALUE_CAPABILITY


PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC = "public-query-effect-amount"
AMOUNT_MECHANIC_CAPABILITIES = {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC: (PUBLIC_QUERY_AMOUNT_CAPABILITY,),
                               SCALAR_AMOUNT_MECHANIC: (SCALAR_AMOUNT_CAPABILITY,),
                               PAID_SACRIFICE_STAT_MECHANIC: (PAID_SACRIFICE_STAT_CAPABILITY,),
                               PAID_SACRIFICE_MANA_VALUE_MECHANIC: (PAID_SACRIFICE_MANA_VALUE_CAPABILITY,),
                               TARGET_HAND_COUNT_MECHANIC:(TARGET_HAND_COUNT_CAPABILITY,),
                               MULTIKICKER_AMOUNT_MECHANIC:(MULTIKICKER_AMOUNT_CAPABILITY,'casting.multikicker.fixed_mana'),
                               MANA_COLOR_AMOUNT_MECHANIC:(MANA_COLOR_AMOUNT_CAPABILITY,)}
AMOUNT_MECHANIC_CAPABILITIES[CAST_MANA_VALUE_MECHANIC]=(CAST_MANA_VALUE_CAPABILITY,)

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
FixedEffectCompiler = Callable[[str], CompiledEffectTemplate]

_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_TRAILING_REMINDER = re.compile(
    r"\s+\([^()]*(?:\([^()]*\)[^()]*)*\)\.?$"
)
_LIFE_PATTERNS = (
    re.compile(
        r"^(?P<subject>(?:you|target player|target opponent|each opponent|each player) )?"
        r"(?P<verb>gain|gains|lose|loses) life equal to "
        r"(?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<subject>(?:you|target player|target opponent|each opponent|each player) )?"
        r"(?P<verb>gain|gains|lose|loses) "
        r"(?P<coefficient>[1-9]\d*|one|two|three|four|five) life for each "
        r"(?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
)
_DRAW_PATTERNS = (
    re.compile(
        r"^Draw a card for each (?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^Draw cards equal to (?P<quantity>.+?)\.?$",
        re.IGNORECASE,
    ),
)
_TOKEN_PATTERN = re.compile(
    r"^Create X (?P<definition>.+? tokens?(?: with .+?)?), where X is the number of "
    r"(?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_TOKEN_FOR_EACH = re.compile(
    r"^Create (?P<coefficient>a|an|one|two|three|four|five|[1-9]\d*) "
    r"(?P<definition>.+? tokens?(?: with .+?)?) for each (?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_TOKEN_COMPLETE_VALUE = re.compile(
    r'^Create a number of (?P<definition>.+?) equal to (?P<quantity>.+?)\.?$', re.I,
)
_COUNTER_FOR_EACH = re.compile(
    rf"^Put (?P<coefficient>a|an|one|two|three|four|five|[1-9]\d*) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) counters? on "
    r"(?P<subject>.+?) for each (?P<quantity>.+?)\.?$", re.IGNORECASE,
)
_QUERY_CHARACTERISTIC_PATTERNS = (
    (
        "per-object",
        re.compile(
            r"^Until end of turn, (?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets?) "
            r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) for each "
            r"(?P<quantity>.+?)(?P<suffix_keywords> and gains? .+?)?\.?$",
            re.IGNORECASE,
        ),
    ),
    (
        "where-x",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets?) "
            r"(?P<power>[+-](?:X|0))/(?P<toughness>[+-](?:X|0))"
            r"(?P<suffix_keywords> and gains? .+?)? until end of turn, "
            r"where X is (?:(?:equal to )?the number of )?"
            r"(?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    ),
    (
        "per-object",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets?) "
            r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
            r"(?P<suffix_keywords> and gains? .+?)? until end of turn "
            r"for each (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    ),
    (
        "per-object",
        re.compile(
            r"^(?P<subject>.+?) "
            r"(?:(?:gains? (?P<prefix_keywords>.+?) and )?gets?) "
            r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
            r"(?P<suffix_keywords> and gains? .+?)? for each "
            r"(?P<quantity>.+?) until end of turn\.?$",
            re.IGNORECASE,
        ),
    ),
)

_CHARACTERISTIC_RESULT_OPERATIONS = frozenset({
    "modify_stats_until_end_of_turn", "apply_source_characteristics_until_end_of_turn",
    "modify_all_matching_permanents_until_end_of_turn",
})


def _coefficient(value: str | None) -> int:
    if value is None:
        return 1
    normalized = value.casefold()
    return int(normalized) if normalized.isdigit() else _NUMBER_WORDS[normalized]


def _damage_patterns(source_name: str) -> tuple[re.Pattern[str], ...]:
    source = SourceReferenceSpec(source_name).regex_pattern
    subject = rf"(?:{source}|it|this (?:artifact|creature|enchantment|permanent))"
    return (
        re.compile(
            rf"^(?P<source>{subject}) deals damage (?P<recipient>.+?) "
            r"equal to (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<source>{subject}) deals damage equal to "
            r"(?P<quantity>.+?) to (?P<recipient>.+?)\.?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<source>{subject}) deals "
            r"(?P<coefficient>[1-9]\d*|one|two|three|four|five) damage "
            r"(?P<recipient>.+?) for each (?P<quantity>.+?)\.?$",
            re.IGNORECASE,
        ),
    )


def _parsed_candidate(
    text: str,
    *,
    source_name: str,
) -> tuple[str, str, int, str, bool] | None:
    energy = re.fullmatch(
        r'You get an amount of \{E\}(?: \(energy counters\))?\s+equal to the number of '
        r'(?P<quantity>.+?)\.?', text, re.I,
    )
    if energy is not None:
        return 'controller-energy', energy['quantity'], 1, 'You get {E}{E}.', False
    library = re.fullmatch(
        r"Surveil X, where X is (?:equal to )?(?:the number of )?(?P<quantity>.+?)\.?",
        text, re.I,
    )
    if library is not None:
        return (
            "surveil", library["quantity"], 1, "Surveil 2.",
            not text.casefold().startswith("surveil x, where x is the number of "),
        )
    for index, pattern in enumerate(_LIFE_PATTERNS):
        match = pattern.fullmatch(text)
        if match is not None:
            return (
                "life",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('subject') or ''}{match.group('verb')} 2 life.",
                index == 0,
            )
    for index, pattern in enumerate(_damage_patterns(source_name)):
        match = pattern.fullmatch(text)
        if match is not None:
            recipient = match.group("recipient")
            if index == 1:
                recipient = "to " + recipient
            return (
                "damage",
                match.group("quantity"),
                _coefficient(match.groupdict().get("coefficient")),
                f"{match.group('source')} deals 2 damage "
                f"{recipient}.",
                index < 2,
            )
    for index, pattern in enumerate(_DRAW_PATTERNS):
        match = pattern.fullmatch(text)
        if match is not None:
            return "draw", match.group("quantity"), 1, "Draw two cards.", index == 1
    token = _TOKEN_PATTERN.fullmatch(text) or _TOKEN_FOR_EACH.fullmatch(text) or _TOKEN_COMPLETE_VALUE.fullmatch(text)
    if token is not None:
        definition = re.sub(
            r" token$",
            " tokens",
            token.group("definition"),
            flags=re.IGNORECASE,
        )
        return (
            "token",
            token.group("quantity"),
            _coefficient(token.groupdict().get("coefficient")),
            f"Create two {definition}.",
            text.casefold().startswith('create a number of '),
        )
    counter = _COUNTER_FOR_EACH.fullmatch(text)
    if counter is not None:
        return ('counter', counter['quantity'], _coefficient(counter['coefficient']),
            f"Put two {counter['counter']} counters on {counter['subject']}.", False)
    return None


def _query_characteristic_candidate(
    text: str,
    *,
    source_name: str,
) -> tuple[str, str, int, int, str] | None:
    """Parse one query-scaled target or source layer-7c modifier."""

    for calculation, pattern in _QUERY_CHARACTERISTIC_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        prefix_keywords = match.group("prefix_keywords")
        suffix_keywords = match.group("suffix_keywords")
        if prefix_keywords is not None and suffix_keywords is not None:
            return None
        subject = match.group("subject")
        source = SourceReferenceSpec(source_name)
        source_subject = subject.casefold() in {
            "it",
            "this artifact",
            "this creature",
            "this enchantment",
            "this permanent",
        } or re.fullmatch(source.regex_pattern, subject, re.IGNORECASE)
        fixed_subject = "this permanent" if source_subject else subject

        coefficients: list[int] = []
        for field in ("power", "toughness"):
            raw = match.group(field).upper()
            if raw in {"+0", "-0"}:
                coefficient = 0
            elif calculation == "where-x":
                coefficient = 1 if raw.startswith("+") else -1
            else:
                coefficient = int(raw)
            coefficients.append(coefficient)
        if coefficients == [0, 0]:
            return None

        keyword_suffix = suffix_keywords or (
            f" and gains {prefix_keywords}" if prefix_keywords else ""
        )
        return (
            fixed_subject,
            keyword_suffix,
            coefficients[0],
            coefficients[1],
            match.group("quantity"),
        )
    return None


_AMOUNT_FIELDS = {
    "drain_opponent": "amount",
    "drain_each_opponent": "amount",
    "life_loss_then_gain_actual": "amount",
    "life": "delta",
    "lose_life": "amount",
    "lose_life_each_opponent": "amount",
    "damage": "amount",
    "damage_each_opponent": "amount",
    "draw": "count",
    "scry": "count",
    "surveil": "count",
    "create_token": "quantity",
    "place_counters": "amount",
    "place_player_counters": "amount",
}


def _source_characteristic_amount_base(
    effects: Sequence[Mapping[str, Any]],
) -> FixedSourceCharacteristicsTemplate | None:
    """Consume the entire fixed self result before adapting its scalar."""
    modifiers = [
        effect for effect in effects
        if effect.get("op") == "modify_stats_until_end_of_turn"
    ]
    if len(modifiers) != 1 or not 1 <= len(effects) <= 3:
        return None
    keywords = []
    for effect in effects:
        if effect.get("card") != "$source":
            return None
        if effect.get("op") == "modify_stats_until_end_of_turn":
            if set(effect) != {"op", "card", "power", "toughness"} or any(
                type(effect.get(field)) is not int
                for field in ("power", "toughness")
            ):
                return None
        elif effect.get("op") == "grant_keyword_until_end_of_turn":
            if set(effect) != {"op", "card", "keyword"} or type(effect["keyword"]) is not str:
                return None
            keywords.append(effect["keyword"])
        else:
            return None
    try:
        return FixedSourceCharacteristicsTemplate(
            source_kind="query-stat-modifier",
            power=modifiers[0]["power"], toughness=modifiers[0]["toughness"],
            keywords=tuple(keywords),
        )
    except ValueError:
        return None


def public_query_effect_amount_template(
    text: str,
    *,
    source_name: str,
    compile_fixed: FixedEffectCompiler,
) -> CompiledEffectTemplate | None:
    """Lower one standalone query-derived amount onto an existing fixed op."""

    normalized = _TRAILING_REMINDER.sub("", text.strip()).strip()
    candidate = _parsed_candidate(normalized, source_name=source_name)
    characteristic = _query_characteristic_candidate(
        normalized,
        source_name=source_name,
    )
    if candidate is None and characteristic is None:
        return None
    if characteristic is not None:
        subject, keyword_suffix, power, toughness, quantity_text = (
            characteristic
        )
        quantity = resolution_quantity(quantity_text, source_name=source_name)
        if quantity is None:
            return None
        if isinstance(quantity, CharacteristicQuantitySpec) and quantity.schema_version == 7 and quantity.exclude_source:
            return None
        try:
            amounts = {
                field: (
                    PublicQueryAmountSpec(
                        quantity=quantity,
                        coefficient=coefficient,
                    ).to_dict()
                    if coefficient
                    else 0
                )
                for field, coefficient in (
                    ("power", power),
                    ("toughness", toughness),
                )
            }
        except PublicQueryAmountError:
            return None
        fixed_text = (
            f"{subject} gets {power * 2:+d}/{toughness * 2:+d}"
            f"{keyword_suffix} until end of turn."
        )
        template_id, effects, target_schema, mechanics = compile_fixed(
            fixed_text
        )
        declared_global = bool(re.search(r", where X is ", normalized, re.IGNORECASE))
        if template_id is None and not declared_global:
            from .fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template
            public_text=(f"{subject} get {power * 2:+d}/{toughness * 2:+d}"
                f"{keyword_suffix.replace(' and gains ', ' and gain ', 1)} until end of turn.")
            public_set=fixed_public_characteristic_set_effect_template(public_text)
            if public_set is not None:template_id,effects,target_schema,mechanics=public_set
        if template_id is None or not mechanics:
            return None
        if declared_global and any(effect.get("op") == "modify_all_matching_permanents_until_end_of_turn" for effect in effects):
            return None  # Keep the existing instruction-scoped declaration owner.
        if target_schema is None and effects and all(effect.get("card") == "$source" for effect in effects):
            source_template = _source_characteristic_amount_base(effects)
            if source_template is None:
                return None
            template_id, effects, target_schema, mechanics = source_template.compiled()
        modified = 0
        projected: list[Mapping[str, Any]] = []
        for raw_effect in effects:
            effect = deepcopy(dict(raw_effect))
            if effect.get("op") in _CHARACTERISTIC_RESULT_OPERATIONS:
                if (
                    type(effect.get("power")) is not int
                    or type(effect.get("toughness")) is not int
                ):
                    return None
                effect.update(amounts)
                modified += 1
            projected.append(effect)
        if template_id is None or modified != 1 or not mechanics:
            return None
        return (
            "public-query-characteristic-amount-v1",
            tuple(projected),
            deepcopy(target_schema),
            tuple(
                dict.fromkeys(
                    (PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, *mechanics)
                )
            ),
        )
    assert candidate is not None
    family, quantity_text, coefficient, fixed_text, complete_value = candidate
    if family == 'controller-energy':
        from .public_state_queries import fixed_battlefield_query_subject

        parsed = fixed_battlefield_query_subject(quantity_text)
        if parsed is None or parsed[0] != 'source_controller' or parsed[2]:
            return None
        try:
            quantity = LiveBattlefieldQuantitySpec(parsed[0], parsed[1])
        except ValueError:
            return None
    else:
        quantity = resolution_quantity(quantity_text, source_name=source_name, complete_value=complete_value)
    if quantity is None:
        return None
    try:
        amount = PublicQueryAmountSpec(quantity=quantity, coefficient=coefficient)
    except PublicQueryAmountError:
        return None
    template_id, effects, target_schema, mechanics = compile_fixed(fixed_text)
    if template_id is None or len(effects) != 1 or not mechanics:
        return None
    effect = deepcopy(dict(effects[0]))
    operation = str(effect.get("op") or "")
    if amount.uses_source_relative_count and (operation != "create_token" or target_schema is not None):
        return None
    field = _AMOUNT_FIELDS.get(operation)
    if field is None or type(effect.get(field)) is not int:
        return None
    sample = int(effect[field])
    if abs(sample) != 2:
        return None
    signed_coefficient = coefficient * (-1 if sample < 0 else 1)
    if field != "delta" and signed_coefficient < 0:
        return None
    effect[field] = PublicQueryAmountSpec(
        quantity=amount.quantity,
        coefficient=signed_coefficient,
    ).to_dict()
    if family == 'controller-energy':
        if operation != 'place_player_counters' or effect.get('subjects') != 'controller' or effect.get('counter') != 'energy':
            return None
        effect['zero_is_noop'] = True
    return (
        f"public-query-{family}-amount-v1",
        (effect,),
        deepcopy(target_schema),
        tuple(dict.fromkeys((PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, *mechanics))),
    )


def contains_public_query_effect_amount(value: Any) -> bool:
    """Return whether nested semantic data contains this scalar descriptor."""

    if isinstance(value, Mapping):
        return value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND or any(
            contains_public_query_effect_amount(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(
        contains_public_query_effect_amount(child) for child in value
    )


def contains_public_query_characteristic_amount(value: Any) -> bool:
    """Return whether one temporary stat operation carries this scalar."""

    if isinstance(value, Mapping):
        if value.get("op") in _CHARACTERISTIC_RESULT_OPERATIONS:
            return any(
                isinstance(value.get(field), Mapping)
                and value[field].get("kind") == PUBLIC_QUERY_AMOUNT_KIND
                for field in ("power", "toughness")
            )
        return any(
            contains_public_query_characteristic_amount(child)
            for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(
        contains_public_query_characteristic_amount(child)
        for child in value
    )


def _fixed_shape_effects(
    effects: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...] | None:
    """Project query-derived scalars to positive fixed values for shape checks."""

    wrapper = None
    if len(effects) == 1 and effects[0].get("op") == OPTIONAL_EFFECT_OPERATION:
        wrapper = effects[0]
        children = wrapper.get("effects")
        if (
            set(wrapper) != {"op", "player", "effects"}
            or wrapper.get("player") != "$controller"
            or not isinstance(children, (list, tuple))
            or len(children) != 1
            or not isinstance(children[0], Mapping)
        ):
            return None
        effects = children

    projected = [deepcopy(dict(effect)) for effect in effects]
    if any(effect.get("op") == "modify_all_matching_permanents_until_end_of_turn"
           for effect in projected) and len(projected) != 1:
        return None
    characteristic = any(
        effect.get("op") in _CHARACTERISTIC_RESULT_OPERATIONS
        and contains_public_query_effect_amount(effect)
        for effect in projected
    )
    if characteristic:
        modifiers = tuple(
            effect
            for effect in projected
            if effect.get("op") in _CHARACTERISTIC_RESULT_OPERATIONS
        )
        references = {
            effect.get("card")
            for effect in projected
            if effect.get("op")
            in {
                "grant_keyword_until_end_of_turn",
                *_CHARACTERISTIC_RESULT_OPERATIONS,
            }
        }
        if (
            not 1 <= len(projected) <= 3
            or len(modifiers) != 1
            or len(references) != 1
            or any(
                effect.get("op")
                not in {
                    "grant_keyword_until_end_of_turn",
                    *_CHARACTERISTIC_RESULT_OPERATIONS,
                }
                for effect in projected
            )
        ):
            return None
    elif len(projected) != 1:
        return None
    descriptor_count = 0
    for effect in projected:
        operation = str(effect.get("op") or "")
        fields: tuple[str, ...]
        if operation in _CHARACTERISTIC_RESULT_OPERATIONS:
            fields = ("power", "toughness")
        else:
            field = _AMOUNT_FIELDS.get(operation)
            fields = (field,) if field is not None else ()
        for field in fields:
            value = effect.get(field)
            if (
                not isinstance(value, Mapping)
                or value.get("kind") != PUBLIC_QUERY_AMOUNT_KIND
            ):
                continue
            try:
                spec = PublicQueryAmountSpec.from_dict(value)
                attack_relative = (spec.schema_version == 3
                    and operation == "apply_source_characteristics_until_end_of_turn"
                    and effect.get("card") == "$event.permanent_object")
                if spec.schema_version != 1 and not attack_relative:
                    return None
            except PublicQueryAmountError:
                return None
            if operation == 'place_player_counters':
                if (effect.get('zero_is_noop') is not True or effect.get('subjects') != 'controller'
                        or effect.get('counter') != 'energy' or spec.coefficient != 1
                        or not isinstance(spec.quantity, LiveBattlefieldQuantitySpec)
                        or spec.quantity.controller_relation != 'source_controller'
                        or spec.quantity.exclude_source):
                    return None
                effect.pop('zero_is_noop')
            effect[field] = -1 if spec.coefficient < 0 else 1
            descriptor_count += 1
    if descriptor_count < 1 or any(
        contains_public_query_effect_amount(effect) for effect in projected
    ):
        return None
    return ({**wrapper, "effects": projected},) if wrapper is not None else tuple(projected)


def _composed_query_shape_context(effects, mechanics):
    if not {'closed-effect-program', 'bound-effect-program'}.intersection(mechanics):
        return None
    projected = []
    for effect in effects:
        if contains_public_query_effect_amount(effect):
            fixed = _fixed_shape_effects((effect,))
            if fixed is None:
                return None
            projected.extend(fixed)
        else:
            projected.append(deepcopy(dict(effect)))
    return tuple(projected), mechanics - {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC}


def public_query_amount_program_is_closed(program: Any, *, required_dependencies) -> bool:
    """Keep scalar admission with its existing fixed shape and target owners."""
    from ..rules.node_capability_shapes import (
        fixed_damage_node_capabilities, fixed_draw_node_capabilities,
        fixed_scry_node_capabilities,
        fixed_counter_placement_target_set_node_capabilities,
        fixed_player_counter_placement_node_capabilities,
        fixed_source_characteristics_node_capabilities,
        fixed_target_characteristics_node_capabilities,
    )
    from ..rules.fixed_controller_effect_shapes import fixed_life_node_capabilities
    from ..rules.actual_life_transfer import actual_life_transfer_capabilities
    from ..rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
    from ..rules.counter_placement_capability_shapes import fixed_counter_placement_node_capabilities
    from ..rules.closed_effect_program_shapes import closed_effect_program_node_capabilities
    from ..rules.surveil_capability_shapes import fixed_surveil_node_capabilities
    from ..rules.fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities
    from ..rules.fixed_controller_effect_shapes import fixed_controller_effect_sequence_node_capabilities
    from ..rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
    from ..rules.attack_event_results import attack_event_characteristic_capabilities
    from ..rules.casting_additional_costs import fixed_creature_sacrifice_price, fixed_single_sacrifice_price
    from ..rules.fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities

    if not {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, SCALAR_AMOUNT_MECHANIC}.intersection(program.coverage):
        return False
    if PAID_SACRIFICE_STAT_MECHANIC in program.coverage and not fixed_creature_sacrifice_price(program.cost_schema):
        return False
    paid_mana_value = PAID_SACRIFICE_MANA_VALUE_MECHANIC in program.coverage
    if paid_mana_value and fixed_single_sacrifice_price(program.cost_schema) is None:
        return False
    context = public_query_amount_shape_context(program.effects, set(program.coverage),target_schema=program.target_schema)
    if context is None:
        return False
    effects, mechanics = context
    required = set(required_dependencies)
    amount_owners = {
        capability
        for mechanic, capability in (
            (SCALAR_AMOUNT_MECHANIC, SCALAR_AMOUNT_CAPABILITY),
            (PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC, PUBLIC_QUERY_AMOUNT_CAPABILITY),
        )
        if mechanic in program.coverage
    }
    if (
        not amount_owners.issubset(required)
        or not required.issubset(program.capability_dependencies)
    ):
        return False
    scalar_specs = scalar_amount_specs(program.effects)
    target_mana_value_sequence = (
        len(scalar_specs) == 1
        and scalar_specs[0].origin is ScalarAmountOrigin.TARGET
        and scalar_specs[0].characteristic == "mana_value"
    )
    return any(
        bool(dependencies:=resolver(effects=effects, target_schema=program.target_schema, mechanic_ids=mechanics))
        and set(dependencies).issubset(program.capability_dependencies)
        for resolver in (
            actual_life_transfer_capabilities,
            fixed_damage_node_capabilities,
            fixed_draw_node_capabilities,
            fixed_scry_node_capabilities,
            fixed_surveil_node_capabilities,
            fixed_life_node_capabilities,
            fixed_token_creation_node_capabilities,
            fixed_target_characteristics_node_capabilities,
            fixed_source_characteristics_node_capabilities,
            attack_event_characteristic_capabilities,
            fixed_resolution_characteristic_set_node_capabilities,
            fixed_counter_placement_node_capabilities,
            fixed_counter_placement_target_set_node_capabilities,
            fixed_player_counter_placement_node_capabilities,
            closed_effect_program_node_capabilities,
            *((fixed_effect_clause_sequence_node_capabilities,)
              if target_mana_value_sequence or
              {MULTIKICKER_AMOUNT_MECHANIC, MANA_COLOR_AMOUNT_MECHANIC, PAID_SACRIFICE_STAT_MECHANIC}.intersection(program.coverage) else ()),
            *((fixed_controller_effect_sequence_node_capabilities,)
              if {MANA_COLOR_AMOUNT_MECHANIC, PAID_SACRIFICE_STAT_MECHANIC, PAID_SACRIFICE_MANA_VALUE_MECHANIC}
              .intersection(program.coverage) else ()),
            *((fixed_resolution_mana_capabilities,) if paid_mana_value else ()),
        )
    )


def public_query_amount_shape_context(
    effects: Sequence[Mapping[str, Any]], mechanics: set[str], *, target_schema=None,
) -> tuple[tuple[Mapping[str, Any], ...], set[str]] | None:
    """Return fixed-value inputs for existing capability shape owners."""

    from ..rules.target_hand_count_shapes import target_hand_count_dependencies
    from ..multikicker_result_amounts import multikicker_amount_dependencies
    from ..mana_spent_amounts import mana_color_amount_dependencies
    from ..cast_event_amounts import cast_mana_value_amount_dependencies
    from ..fixed_token_production import SEQUENTIAL_INVESTIGATE_OPERATION, sequential_investigate_count
    try:
        for effect in effects:
            if effect.get("op") == SEQUENTIAL_INVESTIGATE_OPERATION:
                sequential_investigate_count(effect)
        target_hand_count_dependencies(effects,target_schema,mechanics)
        multikicker_amount_dependencies(effects,mechanics)
        mana_color_amount_dependencies(effects,mechanics)
        cast_mana_value_amount_dependencies(effects,mechanics)
    except (TypeError,ValueError):return None

    if SCALAR_AMOUNT_MECHANIC in mechanics:
        scalar=scalar_amount_shape_context(effects, mechanics)
        if scalar is None:return None
        if PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC in scalar[1]:
            return public_query_amount_shape_context(*scalar)
        return scalar
    if DECLARED_EFFECT_AMOUNT_MECHANIC in mechanics:
        return declared_amount_shape_context(effects, mechanics, target_schema=target_schema)
    if PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC not in mechanics:
        return tuple(effects), set(mechanics)
    if len(effects) == 1 and effects[0].get("op") == "resolve_stack_counter_followup":
        # The stack owner validates the nested quantity without projecting it as a standalone amount.
        return tuple(effects), set(mechanics)
    if len(effects) > 1 and (composed := _composed_query_shape_context(effects, mechanics)) is not None:
        return composed
    projected = _fixed_shape_effects(effects)
    if projected is None:
        return None
    return projected, mechanics - {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC}


__all__ = [
    "PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC",
    "PUBLIC_QUERY_AMOUNT_CAPABILITY",
    "contains_public_query_characteristic_amount",
    "contains_public_query_effect_amount",
    "public_query_amount_shape_context",
    "public_query_effect_amount_template",
]

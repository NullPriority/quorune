from __future__ import annotations

"""Closed declared-X amounts over existing fixed result and target owners."""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import hashlib
from . import regex_working_set as re
from typing import Any, Callable, Mapping, Sequence

from ..query_effect_amount_model import (
    CAST_X_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_KIND,
    CastXAmountSpec, PublicQueryAmountError, PublicQueryAmountSpec,
    DECLARED_AMOUNT_CAPABILITY, scope_declared_amount_bindings,
    ATTACK_SHARED_TYPE_AMOUNT_VERSION, public_query_amount_runtime_dependencies,
)
from .live_battlefield_quantities import resolution_quantity
from ..util import mana_cost_to_vector
from ..resolution_characteristic_model import CHOSEN_CREATURE_TYPE_MODIFIER_FIELD
from ..entry_x_amount_model import (
    ENTRY_HALF_X_FOOD_CAPABILITY, ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_AMOUNT_KIND, ENTRY_X_AMOUNT_MECHANIC,
    EntryXAmountSpec, entry_half_x_food_effect, entry_x_amount_dependencies,
    project_entry_x_token_batch, uses_entry_x_token_batch,
)
from ..replacement.immutable import thaw_value
from ..shared_type_doors import ROOM_DOOR_AMOUNT_KIND, RoomDoorQuantity
from ..fixed_token_production import SEQUENTIAL_INVESTIGATE_OPERATION
from ..impulse_access_model import IMPULSE_ACCESS_DECLARED_COUNT_VERSION
from ..library_search_model import counted_search_bound_dependencies, counted_search_mana_value_slot


DECLARED_EFFECT_AMOUNT_MECHANIC = "declared-effect-amount"
CompiledEffectTemplate = tuple[str | None, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]]
EffectCompiler = Callable[[str], CompiledEffectTemplate]
_DEFINITION = re.compile(
    r"(?P<body>.+?), where X is (?:equal to )?(?P<quantity>.+?)\.?$",
    re.IGNORECASE,
)
_LIBRARY_DEFINITION = re.compile(
    r"(?P<body>Scry X), where X is (?:equal to )?(?P<quantity>.+?)"
    r", then (?P<suffix>draw (?:a|one|two|three|four|five|\d+) cards?)\.?$",
    re.IGNORECASE,
)
_X = re.compile(r"\bX\b")
_ANNOUNCED_PLAYER_ROUNDS = re.compile(
    r"Each player loses X life, discards X cards, sacrifices X creatures of their choice, "
    r"then sacrifices X lands of their choice\.", re.I,
)
_SCALED_X = re.compile(r"\b(?P<scale>twice|two times|three times) X\b", re.I)
_RESULT_FIELDS = {
    'grant_next_spell_cost_reduction': ('amount',),
    'drain_opponent': ('amount',),
    'drain_each_opponent': ('amount',),
    'life_loss_then_gain_actual': ('amount',),
    'discover': ('amount',),
    'variable_library_selection': ('look_count',),
    'incubate': ('amount',),
    'create_token_then_counters': ('amount',),
    'return_random_graveyard_cards': ('count',),
    'fixed_impulse_access': ('count',),
    SEQUENTIAL_INVESTIGATE_OPERATION: ('count',),
    "damage": ("amount",), "damage_each_opponent": ("amount",), "damage_fixed_set": ("amount",),
    "draw": ("count",), "draw_each_player": ("count",),
    "mill": ("count",), "scry": ("count",),
    "life": ("delta",), "lose_life": ("amount",),
    "lose_life_each_opponent": ("amount",), "create_token": ("quantity",),
    "modify_stats_until_end_of_turn": ("power", "toughness"),
    "modify_all_matching_permanents_until_end_of_turn": ("power", "toughness"),
    "apply_source_characteristics_until_end_of_turn": ("power", "toughness"),
    "place_counters": ("amount",),
}


def _result_slot(operation: str, path: tuple[str, ...], *, cast_event_counter_targets: bool = False) -> bool:
    return path in tuple((field,) for field in _RESULT_FIELDS.get(operation, ())) or (
        operation == "create_token" and path in {("characteristics", "power"), ("characteristics", "toughness")}
    ) or (
        operation == "search" and counted_search_mana_value_slot(path)
    ) or (
        operation == "destroy_all" and path == ("set", "mana_value_filter", "value")
    ) or (
        operation == "move_public_zone_set" and path == ("set", "announced_mana_value_max")
    ) or (
        operation == "choose_creature_type" and path == (CHOSEN_CREATURE_TYPE_MODIFIER_FIELD, "amount")
    ) or (
        cast_event_counter_targets and operation == "place_counters_on_targets" and path == ("amount",)
    )


def _signed_number(value: Any) -> int | None:
    if type(value) is int:
        return value
    if type(value) is str and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def _fixed_x_projection(body: str, value: int) -> str:
    """Reduce only the bounded source expression before fixed compilation."""
    scaled = _SCALED_X.sub(
        lambda match: str(value * (3 if match['scale'].lower() == 'three times' else 2)), body)
    return _X.sub(str(value), scaled)


def _announced_graveyard_set_template(text, compile_fixed):
    normalized = " ".join(text.strip().split())
    if re.fullmatch(
        r"return all creature and planeswalker cards with mana value X or less from your graveyard to the battlefield\.?",
        normalized, flags=re.I,
    ) is None:
        return None
    from ..public_zone_moves import (
        ANNOUNCED_GRAVEYARD_RETURN_VERSION, ANNOUNCED_MANA_VALUE_MAX_FIELD, PublicZoneMoveSetSpec,
    )

    old = compile_fixed(_fixed_x_projection(normalized, 2))
    if old[0] is None or old[2] is not None or len(old[1]) != 1:
        return None
    effect = old[1][0]
    if set(effect) != {"op", "source", "set"} or effect.get("op") != "move_public_zone_set":
        return None
    raw = effect["set"]
    query = dict(raw["query"])
    if query.pop("mana_value_max", None) != 2:
        return None
    try:
        projected = PublicZoneMoveSetSpec.from_dict({**raw, "query": query,
            "schema_version": ANNOUNCED_GRAVEYARD_RETURN_VERSION, ANNOUNCED_MANA_VALUE_MAX_FIELD: 2})
    except (TypeError, ValueError):
        return None
    symbolic = {**projected.to_dict(), ANNOUNCED_MANA_VALUE_MAX_FIELD: CastXAmountSpec().to_dict()}
    return ("announced-owned-graveyard-return-v1", ({**effect, "set": symbolic},), None,
            tuple(dict.fromkeys((DECLARED_EFFECT_AMOUNT_MECHANIC, *old[3]))))


def declared_effect_amount_template(
    text: str, *, source_name: str, compile_fixed: EffectCompiler,
    cast_x_available: bool = False, forbid_public_x: bool = False, cast_event_counter_targets: bool = False,
) -> CompiledEffectTemplate | None:
    """Vary only result slots; prove the entire fixed schema stays invariant."""
    if type(cast_x_available) is not bool or type(forbid_public_x) is not bool:
        return None
    normalized = text.strip()
    if cast_x_available:
        if normalized.casefold() == "populate x times.":
            from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID, announced_populate_effect

            return "announced-populate-v1", (announced_populate_effect(),), None, (
                FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-111-tokens", "cr-707-copying-objects",
            )
        from .announced_target_sets import announced_target_set_effect_template
        target_set = announced_target_set_effect_template(normalized)
        if target_set is not None:
            return target_set
        graveyard_return = _announced_graveyard_set_template(normalized, compile_fixed)
        if graveyard_return is not None:
            return graveyard_return
    mill = re.fullmatch(r'(?P<head>(?:(?:you|target player|target opponent|each player|each opponent) )?mills?) '
        r'cards equal to (?P<quantity>[^.]+?)\.?', normalized, re.I)
    if mill is not None:
        normalized = f"{mill['head']} X cards, where X is {mill['quantity']}."
    library_definition = _LIBRARY_DEFINITION.fullmatch(normalized)
    definition = library_definition or _DEFINITION.fullmatch(normalized)
    quantity = None
    if definition is not None:
        if forbid_public_x:
            return None
        body = definition["body"]
        if library_definition is not None and library_definition["suffix"]:
            body += ", then " + library_definition["suffix"]
        # This definition belongs to one instruction, not arbitrary statements.
        if "." in body or len(re.findall(r"\bwhere X\b", normalized, re.I)) != 1:
            return None
        quantity = resolution_quantity(definition['quantity'], source_name=source_name, complete_value=True)
        if quantity is None:
            return None
        try:
            PublicQueryAmountSpec(quantity=quantity)
        except PublicQueryAmountError:
            return None
    elif cast_x_available and not re.search(r"\b(?:where|if|unless)\b", normalized, re.I):
        body = normalized
    else:
        return None
    if _X.search(body) is None:
        return None
    if quantity is not None and _SCALED_X.search(body) is not None:
        return None
    first = compile_fixed(_fixed_x_projection(body, 2))
    second = compile_fixed(_fixed_x_projection(body, 3))
    if first[0] is None or second[0] is None or not first[3] or first[3] != second[3] or first[2] != second[2] or len(first[1]) != len(second[1]):
        return None
    binding_id = "x:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    replaced = 0

    def project(a: Any, b: Any, operation: str, path: tuple[str, ...]) -> Any:
        nonlocal replaced
        if a == b and type(a) is type(b):
            return deepcopy(a)
        if isinstance(a, Mapping) and isinstance(b, Mapping) and set(a) == set(b):
            return {key:project(a[key], b[key], operation, (*path, key)) for key in a}
        if not _result_slot(operation, path, cast_event_counter_targets=cast_event_counter_targets):
            raise PublicQueryAmountError("X changed something other than a result slot")
        if operation == "search" and quantity is not None:
            raise PublicQueryAmountError("Counted search bounds require announced or sealed entry X")
        n, m = _signed_number(a), _signed_number(b)
        if n not in {-6, -4, -2, 2, 4, 6} or m != n * 3 // 2 or type(a) is not type(b):
            raise PublicQueryAmountError("X result did not preserve its bounded signed coefficient")
        replaced += 1
        coefficient = n // 2
        return (
            PublicQueryAmountSpec(quantity=quantity, coefficient=coefficient, schema_version=2, binding_id=binding_id).to_dict()
            if quantity is not None else CastXAmountSpec(
                coefficient=coefficient, schema_version=1 if abs(coefficient) == 1 else 2).to_dict()
        )

    try:
        effects = tuple(project(a,b,str(a.get("op") or ""),()) for a,b in zip(first[1],second[1],strict=True))
    except PublicQueryAmountError:
        return None
    if not replaced:
        return None
    effects = tuple(
        {**effect, "schema_version": IMPULSE_ACCESS_DECLARED_COUNT_VERSION}
        if effect.get("op") == "fixed_impulse_access" and isinstance(effect.get("count"), Mapping) else effect
        for effect in effects
    )
    # A self result must already use the existing logical-object-aware owner.
    if any(effect.get("op") == "modify_stats_until_end_of_turn" and effect.get("card") == "$source" for effect in effects):
        return None
    mechanics = (DECLARED_EFFECT_AMOUNT_MECHANIC, *first[3])
    if quantity is not None:
        mechanics = (*mechanics, "public-query-effect-amount")
    return first[0], effects, deepcopy(first[2]), tuple(dict.fromkeys(mechanics))


def contains_declared_effect_amount(value: Any) -> bool:
    if isinstance(value, Mapping):
        return (value.get("kind") == "scalar_effect_amount" and value.get("binding_id") is not None) or value.get("kind") in {CAST_X_AMOUNT_KIND,ENTRY_X_AMOUNT_KIND} or (
            value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND and value.get("schema_version") == 2
        ) or any(contains_declared_effect_amount(child) for child in value.values())
    return isinstance(value, (list,tuple)) and any(contains_declared_effect_amount(child) for child in value)


def _additional_price_declares_x(oracle_text: str) -> bool:
    from .spell_additional_cost_templates import announced_x_additional_cost_template
    prices=[announced_x_additional_cost_template(line) for line in oracle_text.splitlines()]
    return sum(price is not None for price in prices)==1


def closed_additional_cost_x_result(program: Any, *, recognizers) -> bool:
    """Check the complete known price before its independent result owner."""
    schema=program.cost_schema
    if not isinstance(schema,Mapping) or set(schema)!={'additional_costs'} or not isinstance(schema['additional_costs'],list) or len(schema['additional_costs'])!=1:return False
    # Component-only views cannot establish a whole spell's cost context.
    if getattr(program,'event',None)!='resolve' or not str(getattr(program,'ability_id','')).startswith('spell:'):return False
    from ..rules.casting_additional_cost_groups import announced_additional_cost_dependencies,AnnouncedXLifeAdditionalCost
    from ..rules.casting_additional_costs import FixedZoneChangeAdditionalCost
    from .spell_additional_cost_templates import FixedZoneChangeAdditionalCostTemplate
    try:
        required=announced_additional_cost_dependencies(schema)
        if not required or not set(required)<=set(program.capability_dependencies):return False
        raw=schema['additional_costs'][0]
        if raw.get('kind')=='announced_x_life':
            AnnouncedXLifeAdditionalCost.from_descriptor(raw);prefix='spell-additional-cost-announced-x-life-v1+'
        else:
            cost=FixedZoneChangeAdditionalCost.from_descriptor(raw)
            prefix=FixedZoneChangeAdditionalCostTemplate(cost.operation,cost.predicate).template_id+'-announced-x-v3+'
    except (TypeError,ValueError):return False
    template=str(program.provenance.get('template_id') or '')
    if not template.startswith(prefix):return False
    result=replace(program,cost_schema=None,provenance={**program.provenance,'template_id':template[len(prefix):]},
        coverage=[value for value in program.coverage if value!='cr-601-casting-spells'])
    return any(recognizer(result) for recognizer in recognizers)


def _bargain_declaration_available(record, text):
    if re.search(r'^Bargain\b', text, re.I | re.M) is None:return False
    from .bargain_nodes import printed_bargain_spec
    return printed_bargain_spec(record) is not None


def _announced_player_round_spell_compiler(fallback):
    def compile_player_rounds(text, **context):
        if _ANNOUNCED_PLAYER_ROUNDS.fullmatch(" ".join(text.strip().split())) is not None:
            from ..rules.announced_x_player_rounds import announced_x_player_round_effects
            from ..rules.fixed_player_rounds import FIXED_PLAYER_ROUNDS_TEXT

            template, effects, target_schema, mechanics = fallback(FIXED_PLAYER_ROUNDS_TEXT, **context)
            if template is not None and target_schema is None:
                try:
                    announced = announced_x_player_round_effects(effects)
                except (TypeError, ValueError):
                    pass
                else:
                    return ("announced-x-player-rounds-v1", announced, None,
                            (DECLARED_EFFECT_AMOUNT_MECHANIC, *mechanics))
        return fallback(text, **context)

    return compile_player_rounds


def declared_spell_effect_compiler(record: Any, *, is_spell: bool, oracle_text: str,
                                   compile_effect: Callable[..., Any], face_name: str | None = None) -> Callable[..., Any]:
    """Compile the effect with the declarations available during casting."""
    from .public_library_pile_templates import public_library_piles_template

    if is_spell and not record.faces and public_library_piles_template(oracle_text) is not None:
        source = oracle_text.strip().casefold()

        def compile_piles(text, **context):
            if text.strip().casefold() == source:
                return public_library_piles_template(text)
            return compile_effect(text, **context)

        return compile_piles

    _, symbols = mana_cost_to_vector(record.mana_cost)
    available = bool(is_spell and not record.faces and 'X' in symbols
        and all(symbol=='X' or re.fullmatch(r'[WUBRG]/[WUBRG]',symbol) for symbol in symbols))
    if not available and 'X' not in symbols and is_spell and not record.faces and re.search(r'\bX\b',oracle_text,re.I):
        available=_additional_price_declares_x(oracle_text)
    from .cast_x_search_templates import cast_x_zone_search_template

    if available and symbols.count("X") == 1:
        repeated = re.fullmatch(
            r"repeat the following process x times\. each opponent loses (?P<loss>[1-9][0-9]*) life "
            r"unless that player sacrifices a nonland permanent(?: of their choice)? or discards a card\.",
            " ".join(oracle_text.split()), re.IGNORECASE,
        )
        if repeated is not None:
            from ..rules.repeated_apnap_payment import MECHANIC, payment_round_effect

            def compile_repeated_payment(text, **context):
                if " ".join(text.casefold().split()) == " ".join(oracle_text.casefold().split()):
                    return ("announced-x-mixed-apnap-payment-v1", (payment_round_effect(int(repeated["loss"])),),
                            None, (MECHANIC,))
                return compile_effect(text, **context)

            return compile_repeated_payment
    if available and "X" in symbols and cast_x_zone_search_template(oracle_text, cast_x_available=True):
        def compile_zone_search(text, **context):
            return cast_x_zone_search_template(text, cast_x_available=True) or compile_effect(text, **context)

        return compile_zone_search

    selected = partial(compile_effect,
        cast_x_available=available and re.search(r"\bX (?:is|can't|cannot)\b",oracle_text,re.I) is None,
        forbid_public_x=available,
        kicked_self_replacement_available=bool(is_spell and not record.faces),
        bargain_available=bool(is_spell and _bargain_declaration_available(record, oracle_text)))
    if available and symbols.count("X") == 1:
        selected = _announced_player_round_spell_compiler(selected)
    if is_spell and not record.faces and symbols.count("X") == 1:
        from .announced_mana_value_target import announced_target_spell_compiler
        selected = announced_target_spell_compiler(selected)
    elif is_spell and record.layout == "modal_dfc":
        face = next((face for face in record.faces if face.get("name") == face_name), None)
        if face is not None:
            _, face_symbols = mana_cost_to_vector(str(face.get("mana_cost") or ""))
            if face_symbols.count("X") == 1 and all(
                symbol == "X" or re.fullmatch(r"[WUBRG]/[WUBRG]", symbol) for symbol in face_symbols
            ):
                from .announced_mana_value_target import announced_target_spell_compiler

                selected = announced_target_spell_compiler(selected, distinct_graveyard_only=True)
    if is_spell and not record.faces:
        from .graveyard_cast_replacement import cast_origin_spell_compiler
        from .variable_library_selection_templates import actual_mana_spent_library_spell_compiler
        selected = cast_origin_spell_compiler(selected, source_name=face_name or record.name)
        selected = actual_mana_spent_library_spell_compiler(selected)
    if is_spell:
        from .ordered_graveyard_reanimation_templates import compile_ordered_graveyard_reanimation
        selected = partial(compile_ordered_graveyard_reanimation, fallback=selected)
    from .spell_self_exile import terminal_spell_move_effect_compiler
    matched_face = any(face.get('name') == face_name and re.search(r'\b(?:Instant|Sorcery)\b',str(face.get('type_line',''))) for face in record.faces)
    terminal = terminal_spell_move_effect_compiler(selected,source_name=face_name or record.name,oracle_text=oracle_text,
        ordinary_spell=bool(is_spell and (not record.faces or matched_face)))
    from .fame_fortune import fame_fortune_template
    if is_spell and not record.faces and (fame_fortune := fame_fortune_template(oracle_text)) is not None:
        pinned = " ".join(oracle_text.split()).casefold()

        def compile_public_opponent_choice(text, **context):
            if " ".join(text.split()).casefold() == pinned:
                return fame_fortune
            return terminal(text, **context)

        return compile_public_opponent_choice
    from .target_permanent_shuffle_reveal_templates import target_permanent_shuffle_reveal_template
    from .destruction_followup_templates import target_controller_library_reveal_template
    if is_spell and not record.faces:
        source = " ".join(oracle_text.split()).casefold()
        compiled_reveal = (target_permanent_shuffle_reveal_template(oracle_text)
                           or target_controller_library_reveal_template(oracle_text))
        if compiled_reveal is not None:
            def compile_target_library_reveal(text, **context):
                if " ".join(text.split()).casefold() == source:
                    return compiled_reveal
                return terminal(text, **context)

            return compile_target_library_reveal
    if not is_spell or " damage divided as you choose among " not in oracle_text.casefold():
        return terminal

    from .announced_damage_templates import fixed_announced_damage_template, variable_announced_damage_template

    def compile_spell_effect(text: str, **context):
        name = context.get("card_name") or record.name
        face = next((face for face in record.faces if face.get("name") == name), None)
        face_x = face is not None and "X" in mana_cost_to_vector(str(face.get("mana_cost") or ""))[1]
        allocation = (fixed_announced_damage_template(text, card_name=name)
                      or variable_announced_damage_template(text, card_name=name, has_cast_x=available or face_x))
        return allocation or terminal(text, **context)

    return compile_spell_effect


def scope_declared_card_faces(faces: Sequence[Any]) -> tuple[Any, ...]:
    """Scope repeated declarations only at their canonical Oracle-node owner."""
    return tuple(replace(face,nodes=tuple(
        replace(node,effects=tuple(scope_declared_amount_bindings(
            node.effects,f"{face.face_id}:{node.node_id}")))
        if contains_declared_effect_amount(node.effects) else node
        for node in face.nodes)) for face in faces)


def declared_amount_dependencies(
    mechanics: set[str], *, effects: Sequence[Mapping[str, Any]] = (),
) -> set[str]:
    from ..object_predicate import object_query_capabilities
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_MECHANIC
    from .public_query_effect_amounts import PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC

    dependencies = {
        capability
        for mechanic, capability in (
            (DECLARED_EFFECT_AMOUNT_MECHANIC, DECLARED_AMOUNT_CAPABILITY),
            (SCALAR_AMOUNT_MECHANIC, SCALAR_AMOUNT_CAPABILITY),
            (ENTRY_X_AMOUNT_MECHANIC, ENTRY_X_AMOUNT_CAPABILITY),
        )
        if mechanic in mechanics
    }
    if PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC in mechanics:
        dependencies.update(object_query_capabilities(effects))
    from ..rules.impulse_access_capability_shapes import declared_impulse_access_dependencies
    from ..rules.announced_x_player_rounds import (
        ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY, uses_announced_x_player_round_choice,
    )
    if any(uses_announced_x_player_round_choice(effect) for effect in effects):
        dependencies.add(ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY)
    for effect in effects:
        dependencies.update(declared_impulse_access_dependencies(effect))
        dependencies.update(entry_x_amount_dependencies(effect))
        room_amount = _room_token_amount(effect)
        if room_amount is not None:
            dependencies.update(public_query_amount_runtime_dependencies(room_amount))
        power = effect.get("power")
        if (isinstance(power, Mapping) and power.get("kind") == PUBLIC_QUERY_AMOUNT_KIND
                and power.get("schema_version") == ATTACK_SHARED_TYPE_AMOUNT_VERSION):
            dependencies.update(public_query_amount_runtime_dependencies(power))
    return dependencies


def _room_token_amount(effect: Mapping[str, Any]) -> Mapping[str, Any] | None:
    characteristics = effect.get("characteristics")
    if not isinstance(characteristics, Mapping):
        return None
    power, toughness = characteristics.get("power"), characteristics.get("toughness")
    room_amount = next((value for value in (power, toughness)
                        if isinstance(value, Mapping) and isinstance(value.get("quantity"), Mapping)
                        and value["quantity"].get("kind") == ROOM_DOOR_AMOUNT_KIND), None)
    if room_amount is None:
        return None
    spec = PublicQueryAmountSpec.from_dict(room_amount)
    if (effect.get("op") != "create_token" or effect.get("controller") != "$controller"
            or type(effect.get("quantity")) is not int or effect["quantity"] != 1
            or power != toughness or spec.schema_version != 2 or spec.coefficient != 1):
        raise PublicQueryAmountError("A Room door amount requires one token with two equal fixed stats")
    return room_amount


def is_closed_declared_amount_program(program: Any, *, required_dependencies: Sequence[str],
                                     is_fixed_program: Callable[[Any], bool]) -> bool:
    """Project only validated scalar slots before existing result admission."""
    from ..zone_devour import is_closed_devour_entry_result_program

    if is_closed_devour_entry_result_program(program):
        return True

    from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
    from ..rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities

    if DECLARED_EFFECT_AMOUNT_MECHANIC not in program.coverage:
        return False
    context = declared_amount_shape_context(
        program.effects, set(program.coverage), target_schema=program.target_schema,
    )
    required = set(required_dependencies)
    if context is None or DECLARED_AMOUNT_CAPABILITY not in required or not required.issubset(program.capability_dependencies):
        return False
    effects, mechanics = context
    characteristic_required = set(fixed_resolution_characteristic_set_node_capabilities(
        effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics))
    return bool(characteristic_required and characteristic_required.issubset(program.capability_dependencies)) or is_fixed_program(
        replace(program,effects=effects,coverage=tuple(sorted(mechanics))))


def declared_amount_shape_context(
    effects: Sequence[Mapping[str, Any]], mechanics: set[str], *,
    target_schema: Mapping[str, Any] | None = None,
) -> tuple[tuple[Mapping[str, Any], ...], set[str]] | None:
    """Validate every symbolic path before asking existing fixed shape owners."""
    from .public_query_effect_amounts import (
        PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC,
        contains_public_query_effect_amount,
        public_query_amount_shape_context,
    )
    from ..rules.closed_effect_program_shapes import CLOSED_EFFECT_PROGRAM_MECHANIC
    from .announced_target_sets import ANNOUNCED_TARGET_SET_MECHANIC
    from ..rules.announced_x_player_rounds import (
        project_announced_x_player_round_effects, uses_announced_x_player_round_choice,
    )

    if any(uses_announced_x_player_round_choice(effect) for effect in effects):
        try:
            fixed = project_announced_x_player_round_effects(effects, target_schema)
        except (TypeError, ValueError, KeyError):
            return None
        return fixed, mechanics - {DECLARED_EFFECT_AMOUNT_MECHANIC}

    count = 0
    def project(value: Any, operation: str, path: tuple[str,...]) -> Any:
        nonlocal count
        if isinstance(value, Mapping) and value.get("kind") in {CAST_X_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_KIND,ENTRY_X_AMOUNT_KIND}:
            if not _result_slot(operation,path):
                raise PublicQueryAmountError("Declared amount is outside a result slot")
            if value['kind']==ENTRY_X_AMOUNT_KIND:
                spec=EntryXAmountSpec.from_dict(value)
            elif value["kind"] == CAST_X_AMOUNT_KIND:
                spec = CastXAmountSpec.from_dict(value)
            else:
                if "public-query-effect-amount" not in mechanics:
                    raise PublicQueryAmountError("Declared public amounts require their query owner")
                spec = PublicQueryAmountSpec.from_dict(value)
                if spec.schema_version != 2:
                    raise PublicQueryAmountError("Declared instruction requires its binding identity")
                if isinstance(spec.quantity, RoomDoorQuantity) and (
                    operation != "create_token" or path not in {
                        ("characteristics", "power"), ("characteristics", "toughness"),
                    }
                ):
                    raise PublicQueryAmountError("A Room door amount requires its fixed token stats")
            if operation == "move_public_zone_set" and spec != CastXAmountSpec():
                raise PublicQueryAmountError("The graveyard return ceiling requires the spell's announced X")
            count += 1
            result = 2 * spec.coefficient
            return str(result) if path in {("characteristics","power"),("characteristics","toughness")} else result
        if isinstance(value, Mapping):
            return {key:project(child,operation,(*path,key)) for key,child in value.items()}
        if isinstance(value,(list,tuple)):
            if contains_declared_effect_amount(value):
                raise PublicQueryAmountError("Nested effect programs require their own declaration scopes")
            return deepcopy(value)
        return value
    def project_instruction(effect: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal count
        if uses_entry_x_token_batch(effect):
            if ENTRY_X_AMOUNT_MECHANIC not in mechanics or target_schema is not None:
                raise PublicQueryAmountError("A token batch requires its untargeted entry-X owner")
            count += 2
            return project_entry_x_token_batch(effect)
        _room_token_amount(effect)
        if ANNOUNCED_TARGET_SET_MECHANIC in mechanics and effect.get("announced_target_count") is True:
            from ..rules.fixed_homogeneous_target_set_capability_shapes import announced_target_set_node_capabilities

            if not announced_target_set_node_capabilities(
                effects=(effect,), target_schema=target_schema, mechanic_ids=mechanics,
            ):
                raise PublicQueryAmountError("Announced target bound lacks its target-count owner")
            return deepcopy(effect)
        if effect.get("op") == "search":
            counted_search_bound_dependencies(effect)
            from ..library_search_model import QUERY_BOUND_SEARCH_VERSION, QUERY_BOUND_SEARCH_MECHANIC
            if effect.get("schema_version") == QUERY_BOUND_SEARCH_VERSION:
                if QUERY_BOUND_SEARCH_MECHANIC not in mechanics:
                    raise PublicQueryAmountError("Search quantity lacks its typed search owner")
                return deepcopy(effect)
        if effect.get("op") == "fixed_impulse_access":
            from ..rules.impulse_access_capability_shapes import declared_impulse_access_dependencies
            if declared_impulse_access_dependencies(effect):
                effect = {key: value for key, value in effect.items() if key != "schema_version"}
        if effect.get("op") == "offer_optional_effect":
            nested = effect.get("effects")
            if (isinstance(nested, (list, tuple)) and len(nested) == 1
                    and isinstance(nested[0], Mapping) and nested[0].get("op") == "search"):
                return {**effect, "effects": [project_instruction(nested[0])]}
        if (contains_public_query_effect_amount(effect) and not contains_declared_effect_amount(effect)):
            if not {CLOSED_EFFECT_PROGRAM_MECHANIC, PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC} <= mechanics:
                raise PublicQueryAmountError("Independent amounts require a closed ordered instruction")
            independent = public_query_amount_shape_context((effect,), {PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC})
            if independent is None:
                raise PublicQueryAmountError("Independent instruction amount is malformed")
            return independent[0][0]
        return project(effect, str(effect.get("op") or ""), ())

    try:
        if ENTRY_HALF_X_FOOD_CAPABILITY in entry_x_amount_dependencies(effects) and (
            target_schema is not None or thaw_value(effects) != [entry_half_x_food_effect()]
        ):
            return None
        fixed = tuple(project_instruction(effect) for effect in effects)
    except (ValueError, TypeError, KeyError):
        return None
    return (fixed, mechanics - {DECLARED_EFFECT_AMOUNT_MECHANIC,"public-query-effect-amount",ENTRY_X_AMOUNT_MECHANIC}) if count else None

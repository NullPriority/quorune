"""Compile CR721 striations around the ordinary source-spanned ability owners."""
from dataclasses import dataclass, replace
import re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID, StaticComponentScopeSpec, ability_fragment_to_dict
from ..station_symbol_model import STATION_SYMBOL_CAPABILITY, STATION_SYMBOL_MECHANIC, StationSymbolSpec, StationSymbolsSpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .dependency_gate import explicit_capabilities_gate
from .generated_program_identity import generated_ability_id
from .ir_model import OracleNode, SourceSpan, append_residual

_SYMBOL = re.compile(r'^(?P<minimum>[1-9]\d*)\+\s*\|\s*(?P<body>.+)$')
_CREATURE_BOX = re.compile(r"It's an artifact creature at (?P<minimum>[1-9]\d*)\+\.", re.I)


@dataclass(frozen=True, slots=True)
class ParsedStationSymbol:
    minimum: int
    row: int
    member_lines: frozenset[int]
    power: int | None
    toughness: int | None


@dataclass(frozen=True, slots=True)
class ParsedStationContext:
    symbols: tuple[ParsedStationSymbol, ...]


def station_source_context(record, material_rows):
    """Validate the pinned export's explicit P/T-striation annotation."""
    if record.layout != 'normal' or record.faces:
        return material_rows, None
    headers = [(index, _SYMBOL.fullmatch(material)) for index, (_text, material, _span) in enumerate(material_rows)
               if _SYMBOL.fullmatch(material)]
    if not 1 <= len(headers) <= 2:
        return material_rows, None
    station = [text for text, material, _span in material_rows[:headers[0][0]] if material.casefold() == 'station']
    if len(station) != 1:
        return material_rows, None
    minimums = [int(match['minimum']) for _, match in headers]
    if minimums != sorted(set(minimums)):
        return material_rows, None
    box = _CREATURE_BOX.findall(station[0])
    power = toughness = None
    if record.power is not None or record.toughness is not None:
        if (not isinstance(record.power, str) or not record.power.isdigit()
                or not isinstance(record.toughness, str) or not record.toughness.isdigit()
                or box != [str(minimums[-1])]):
            return material_rows, None
        power, toughness = int(record.power), int(record.toughness)
    elif box:
        return material_rows, None
    rows = list(material_rows)
    symbols = []
    for ordinal, (index, match) in enumerate(headers):
        end = headers[ordinal + 1][0] if ordinal + 1 < len(headers) else len(rows)
        symbols.append(ParsedStationSymbol(int(match['minimum']), index,
            frozenset(span.line for _text, _material, span in material_rows[index:end]),
            power if ordinal == len(headers) - 1 else None,
            toughness if ordinal == len(headers) - 1 else None))
        text, _material, span = rows[index]
        rows[index] = text, match['body'], span
    return tuple(rows), ParsedStationContext(tuple(symbols))


def _semantic_key(oracle_id, face_id, node):
    identity = generated_ability_id(kind=node.kind, face_id=face_id, line=node.span.line,
        static_declaration=bool(node.handlers or node.kind == 'keyword_ability' and node.capability_dependencies), node_id=node.node_id)
    return f'{oracle_id}:{identity}' if identity else None


def apply_station_context(*, context, oracle_id, face_id, oracle_text, material_rows, nodes,
                          residuals, capability_registry, capability_profile):
    if context is None:
        return tuple(nodes)
    keys = [[] for _ in context.symbols]
    keywords = [[] for _ in context.symbols]
    annotated = []
    for node in nodes:
        slot = next((index for index, symbol in enumerate(context.symbols) if node.span.line in symbol.member_lines), None)
        if slot is None or not node.exact:
            annotated.append(node)
            continue
        key = _semantic_key(oracle_id, face_id, node)
        if key is None:
            annotated.append(node)
            continue
        gate = explicit_capabilities_gate((*node.capability_dependencies, STATION_SYMBOL_CAPABILITY),
            capability_registry=capability_registry, capability_profile=capability_profile)
        extra = ()
        if gate.blockers:
            extra = (append_residual(residuals, kind='dependency_contract', text=node.text, span=node.span,
                reason='Station-symbol child depends on trusted threshold semantics', blockers=gate.blockers),)
        annotated.append(replace(node, exact=node.exact and not gate.blockers,
            mechanics=tuple(dict.fromkeys((*node.mechanics, STATION_SYMBOL_MECHANIC))),
            runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))),
            residual_ids=(*node.residual_ids, *extra), capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (), capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
        if node.exact:
            keys[slot].append(key)
            if node.kind == 'keyword_ability':
                keywords[slot].extend(str(mechanic).casefold() for mechanic in node.mechanics if str(mechanic).casefold() in {value.casefold() for value in FIXED_CHARACTERISTIC_KEYWORDS})
    spec = StationSymbolsSpec(tuple(StationSymbolSpec(symbol.minimum, symbol.power, symbol.toughness,
        tuple(sorted(set(keywords[index]), key=str.casefold)), tuple(sorted(set(keys[index]), key=str.casefold)))
        for index, symbol in enumerate(context.symbols)))
    first = material_rows[context.symbols[0].row][2]
    last = material_rows[-1][2]
    span = SourceSpan(first.start, last.end, first.line)
    parent_id = f'{face_id}:station-symbol-scope'
    parent = OracleNode(node_id=parent_id, kind='static_ability', text=oracle_text[span.start:span.end],
        span=span, active_zone='battlefield', event='characteristics.evaluate', lowerable=True, exact=True,
        template_id='station-symbol-scopes-v1', mechanics=(STATION_SYMBOL_MECHANIC,))
    gate = explicit_capabilities_gate((STATION_SYMBOL_CAPABILITY,), capability_registry=capability_registry, capability_profile=capability_profile)
    extra = ()
    if gate.blockers:
        extra = (append_residual(residuals, kind='dependency_contract', text=parent.text, span=span,
            reason='Station symbols depend on trusted threshold semantics', blockers=gate.blockers),)
    scope = StaticComponentScopeSpec(parent_semantic_key=_semantic_key(oracle_id, face_id, replace(parent, handlers=(spec.descriptor(),))),
        child_semantic_keys=tuple(sorted(key for group in keys for key in group)),
        keywords=tuple(sorted({keyword for group in keywords for keyword in group}, key=str.casefold)))
    parent = replace(parent, exact=not gate.blockers, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (), capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None, residual_ids=extra,
        handlers=(spec.descriptor(), {'handler_id': STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID, 'schema_version': 1,
            'event': 'characteristics.evaluate', 'fragment': ability_fragment_to_dict(scope)}))
    return tuple(sorted((*annotated, parent), key=lambda node: (node.span.line, node.node_id)))

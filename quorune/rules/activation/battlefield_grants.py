"""Live Layer 6 grants over current controlled, differently named creatures."""

from dataclasses import dataclass

from ...ability_fragments import canonical_ability_fragments, StaticComponentSpec
from ...carddb_characteristics import public_object_rules_names
from ...characteristic_read_frames import memoized_characteristic_read
from ...continuous_effect_model import Layer
from ...continuous_type_dependencies import live_dependency_effect_order
from ...replacement.face_status_copy import current_copy_history


BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY = 'continuous.ability.battlefield_creature_activation_grant'
BATTLEFIELD_CREATURE_ACTIVATION_GRANT_HANDLER = 'ability.static.battlefield-creature-activation-grant.v1'


def battlefield_activation_grant_descriptor():
    return {'handler_id': BATTLEFIELD_CREATURE_ACTIVATION_GRANT_HANDLER,
            'schema_version': 1, 'event': 'characteristics.evaluate'}


@dataclass(frozen=True, slots=True)
class BattlefieldCreatureActivationGrantHandler:
    handler_id: str = BATTLEFIELD_CREATURE_ACTIVATION_GRANT_HANDLER
    schema_version: int = 1
    family: str = BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('201.2a', '201.5b', '602.2', '611.3b', '613.1f', '613.7a', '613.8a', '613.8b', '613.8c')
    capability_dependencies: tuple[str, ...] = (BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY,)

    def validate(self, descriptor):
        from ...semantic_runtime.context import SemanticNodeError

        if (descriptor != battlefield_activation_grant_descriptor()
                or type(descriptor.get('schema_version')) is not int):
            raise SemanticNodeError('A battlefield creature activation grant requires its closed static descriptor')

    def lower(self, descriptor, _context):
        # The canonical compiled source already contributes its StaticComponentSpec.
        # Materialization reads that component only after current Layer 6 applicability.
        self.validate(descriptor)
        return ()


def battlefield_grant_runtime_dependencies(program):
    declared = BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY in program.capability_dependencies
    marked = any(row.get('handler_id') == BATTLEFIELD_CREATURE_ACTIVATION_GRANT_HANDLER for row in program.handlers)
    if not declared and not marked:
        return ()
    if (program.event != 'characteristics.evaluate' or program.active_zone != 'battlefield'
            or program.effects or program.target_schema is not None or program.event_condition is not None
            or program.cost_schema is not None or tuple(program.handlers) != (battlefield_activation_grant_descriptor(),)
            or type(program.handlers[0].get('schema_version')) is not int
            or not program.ability_id.startswith('static:')):
        raise ValueError('A battlefield creature activation grant requires its exact battlefield static program')
    return (BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY,)


def _has_current_battlefield_grant(host, data):
    return any(
        isinstance(fragment, StaticComponentSpec)
        and (program := host.semantics.get(fragment.semantic_key)) is not None
        and BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY in program.capability_dependencies
        and tuple(program.handlers) == (battlefield_activation_grant_descriptor(),)
        and host.semantic_program_is_current_trusted(program)
        for fragment in canonical_ability_fragments(data.get('ability_fragments', ()))
    )


@dataclass(frozen=True, slots=True)
class BattlefieldActivationGrant:
    effect_id: str
    timestamp: int
    donor_ids: tuple[str, ...]
    characteristic_defining: bool = False


def _evaluate_battlefield_activation_grants(host, controller):
    from .query import bind_donor_activation_catalog, current_activation_catalog, represented_donor_activation_catalog

    cards, names, concrete, grants = {}, {}, {}, []
    for object_id in host.state.players[controller].zones['battlefield']:
        card = host.state.cards[object_id]
        if card.zone != 'battlefield' or card.controller != controller or card.phased_out:
            continue
        data = host._effective_card_data(card, maximum_layer=Layer.ABILITY)
        cards[object_id] = card
        names[object_id] = frozenset(public_object_rules_names(card, data))
        types, subtypes, _ = host._type_parts(str(data.get('type_line') or ''))
        if 'creature' in types:
            catalog = current_activation_catalog(host, card, data)
            concrete[object_id] = tuple(represented_donor_activation_catalog(
                host, card, catalog, types=types, subtypes=subtypes,
            ))
        if _has_current_battlefield_grant(host, data):
            timestamp = max(card.zone_timestamp, current_copy_history(card).effective.timestamp)
            grants.append((object_id, timestamp))

    effects = tuple(
        BattlefieldActivationGrant(receiver, timestamp, tuple(donor for donor in concrete
            if donor != receiver and names[receiver].isdisjoint(names[donor])))
        for receiver, timestamp in grants
    )
    added = {}

    def dependencies_for(remaining):
        producing = {receiver for receiver, effect in remaining.items()
                     if any(concrete[donor] for donor in effect.donor_ids)}
        return {receiver: set(effect.donor_ids) & producing for receiver, effect in remaining.items()}

    for effect in live_dependency_effect_order(effects, dependencies_for, []):
        recipient = cards[effect.effect_id]
        abilities = []
        for donor in effect.donor_ids:
            abilities.extend(bind_donor_activation_catalog(
                cards[donor], concrete[donor], binding_identity=recipient.logical_object_id,
                ability_prefix='battlefield-creature', first_line_index=35_000 + len(abilities),
            ))
        added[effect.effect_id] = tuple(abilities)
        if effect.effect_id in concrete:
            concrete[effect.effect_id] += tuple((ability, ability.builtin_semantic_key) for ability in abilities)
    return added


def battlefield_creature_activated_abilities(host, recipient, data):
    if (recipient.zone != 'battlefield' or recipient.phased_out
            or not _has_current_battlefield_grant(host, data)):
        return ()
    group = memoized_characteristic_read(host, ('battlefield-creature-activations', recipient.controller),
        lambda: _evaluate_battlefield_activation_grants(host, recipient.controller))
    return group.get(recipient.object_id, ())

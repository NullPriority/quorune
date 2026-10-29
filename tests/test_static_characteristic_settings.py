from __future__ import annotations

"""Pinned CR205/611/613: constant live characteristic settings."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_characteristic_settings import fixed_characteristic_setting_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler, ConditionalCharacteristicSettingsHandler
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class ConditionalCreatureTypeRemovalTests(unittest.TestCase):
    def test_conditional_type_component_validates_information_layers_and_incarnation(self):
        from quorune.continuous_conditions import FixedPublicStateConditionSnapshot
        from quorune.continuous_effect_model import Layer
        from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.semantic_runtime.fixed_characteristic_settings import ConditionalCreatureTypeRemovalHandler
        from quorune.card_programs.runtime import _component_public_state_snapshot

        text = "As long as your devotion to blue is less than five, this permanent isn't a creature."
        descriptor = fixed_characteristic_setting_handler(text, source_name='Generic God')[1]
        handler = ConditionalCreatureTypeRemovalHandler()
        public = FixedPublicStateConditionSnapshot(source_controller='A', active_player='B',
            controller_hand_count=2, controller_graveyard_card_count=0, controller_life=40,
            opponent_life_totals=(40, 40, 40), turn_sequence=1,
            source_entered_battlefield_turn_sequence=0, source_counters=(), condition_quantity=4)
        context = ContinuousEffectSourceContext(source_object_id='source', source_ref='SOURCE', source_controller='A',
            source_timestamp=1, source_logical_object_id='source@0', component_id='conditional-type', public_state=public)
        effects = handler.lower(descriptor, context)
        self.assertEqual(1, len(effects))
        self.assertEqual(Layer.TYPE, effects[0].layer)
        base = CharacteristicState(name='Source', card_types={'Enchantment', 'Creature'}, subtypes={'God'}, power=3, toughness=4)
        active = evaluate_continuous_effects(deepcopy(base), effects,
            context={'object_id': 'source', 'logical_object_id': 'source@0', 'zone': 'battlefield'}).characteristics
        stale = evaluate_continuous_effects(deepcopy(base), effects,
            context={'object_id': 'source', 'logical_object_id': 'source@1', 'zone': 'battlefield'}).characteristics
        self.assertEqual(['Enchantment'], active['card_types'])
        self.assertEqual([], active['subtypes'])
        self.assertEqual(['Creature', 'Enchantment'], stale['card_types'])
        self.assertEqual((), handler.lower(descriptor, replace(context,
            public_state=replace(public, condition_quantity=5))))
        for invalid in (replace(context, public_state=None), replace(context, source_logical_object_id=None),
                        replace(context, public_state=replace(public, condition_quantity=None))):
            with self.subTest(context=invalid), self.assertRaises(ValueError):
                handler.lower(descriptor, invalid)
        self.assertEqual((True, None), _component_public_state_snapshot(None, descriptor, maximum_layer=Layer.TEXT,
            public_state_resolver=None, setting_state_resolver=None))
        from quorune.semantic_runtime.continuous_components import ContinuousEffectComponentRegistry
        registry = ContinuousEffectComponentRegistry((handler,)).freeze()
        self.assertEqual(Layer.TEXT, registry.source_component_layer(descriptor))
        self.assertEqual(Layer.ABILITY, registry.source_component_layer({'handler_id': 'unclassified'}))
        malformed = {**descriptor, 'unrepresented': True}
        with self.assertRaises(ValueError):
            registry.source_component_layer(malformed)
        with self.assertRaises(ValueError):
            _component_public_state_snapshot(None, malformed, maximum_layer=Layer.TEXT,
                public_state_resolver=None, setting_state_resolver=None)
        for raw in ({**descriptor, 'schema_version': True}, {**descriptor, 'event': 'anything'}):
            with self.assertRaises(ValueError):
                handler.validate(raw)

    def test_type_removal_operation_round_trips_and_rejects_payloads(self):
        from quorune.continuous_effect_model import ContinuousEffect, ContinuousEffectError, ContinuousOperation, Layer

        operation = ContinuousOperation('remove_creature_card_type')
        self.assertEqual(operation, ContinuousOperation.from_dict(json.loads(json.dumps(operation.to_dict()))))
        with self.assertRaises(ContinuousEffectError):
            ContinuousEffect(effect_id='wrong-layer', source_id='source', layer=Layer.COLOR,
                sublayer='5', timestamp=0, operations=(operation,))
        for value in (True, False, 0, '', 'Creature', [], {}):
            with self.subTest(value=value), self.assertRaises(ContinuousEffectError):
                ContinuousOperation('remove_creature_card_type', value)
        with self.assertRaises(ContinuousEffectError):
            ContinuousOperation('remove_creature_card_type', field='subtypes')
        with self.assertRaises(ContinuousEffectError):
            ContinuousOperation.from_dict({**operation.to_dict(), 'extra': True})

    def test_type_removal_retains_only_subtypes_correlated_with_remaining_types(self):
        # Pinned CR 205.1a: Kindred shares the creature subtype set.
        from quorune.continuous_effect_model import ContinuousEffect, ContinuousOperation, ContinuousEffectOrigin, Layer
        from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects

        operation = ContinuousOperation('remove_creature_card_type', None)
        effect = ContinuousEffect(effect_id='remove-creature-type', source_id='source',
            layer=Layer.TYPE, sublayer='4', timestamp=1, operations=(operation,), origin=ContinuousEffectOrigin.OBJECT)
        cases = (
            ({'Enchantment', 'Creature'}, {'God'}, {'Enchantment'}, set()),
            ({'Kindred', 'Enchantment', 'Creature'}, {'God'}, {'Kindred', 'Enchantment'}, {'God'}),
            ({'Artifact', 'Creature'}, {'Construct', 'Equipment'}, {'Artifact'}, {'Equipment'}),
            ({'Land', 'Creature'}, {'Dryad', 'Forest'}, {'Land'}, {'Forest'}),
            ({'Kindred', 'Enchantment'}, {'God'}, {'Kindred', 'Enchantment'}, {'God'}),
        )
        for types, subtypes, expected_types, expected_subtypes in cases:
            with self.subTest(types=types, subtypes=subtypes):
                base = CharacteristicState(name='Generic Conditional Source', controller='A',
                    card_types=set(types), subtypes=set(subtypes), power=3, toughness=4)
                result = evaluate_continuous_effects(base, (effect,)).characteristics
                self.assertEqual(expected_types, set(result['card_types']))
                self.assertEqual(expected_subtypes, set(result['subtypes']))
                self.assertEqual((3, 4), (result['power'], result['toughness']))
                repeated = evaluate_continuous_effects(base, (effect,
                    replace(effect, effect_id='remove-creature-type-again', timestamp=2))).characteristics
                self.assertEqual(expected_types, set(repeated['card_types']))
                self.assertEqual(expected_subtypes, set(repeated['subtypes']))

    def test_devotion_type_removal_compiles_the_complete_generic_condition(self):
        # CR 700.5/700.5a and 613.1d; runtime admission has separate witnesses.
        registry = load_default_capability_registry()
        for colors, threshold in (('blue', 'five'), ('blue and black', 'seven')):
            text = f"As long as your devotion to {colors} is less than {threshold}, this permanent isn't a creature."
            with self.subTest(text=text):
                record = replace(query_record(text), type_line='Enchantment Creature — God', mana_cost='{3}{U}')
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertEqual(1, len(ir.faces[0].nodes))
                self.assertEqual(text, ir.faces[0].nodes[0].text)
                self.assertTrue(ir.faces[0].nodes[0].lowerable)
                self.assertIn('continuous.characteristics.conditional_creature_type_removal', ir.faces[0].nodes[0].capability_dependencies)
                self.assertIn('state_query.devotion.current', ir.faces[0].nodes[0].capability_dependencies)

    def test_devotion_type_subject_domain_and_withdrawn_dependencies_fail_closed(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        for condition in ('your devotion to blue and blue is less than five', 'your devotion to blue is less than zero',
                          'your devotion to blue is less than 0', 'your devotion to blue and black and green is less than seven'):
            self.assertIsNone(fixed_public_state_condition(condition, source_name='Generic God'))
        registry = load_default_capability_registry()
        for body in ("that creature isn't a creature", "target creature isn't a creature", "this permanent isn't a creature and draws a card"):
            text = 'As long as your devotion to blue is less than five, ' + body + '.'
            ir = compile_oracle_card(replace(query_record(text), type_line='Enchantment Creature — God'),
                capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
        text = "As long as your devotion to blue is less than five, this permanent isn't a creature."
        record = replace(query_record(text), type_line='Enchantment Creature — God')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('continuous.characteristics.conditional_creature_type_removal', 'state_query.devotion.current'):
            withdrawn = deepcopy(raw)
            next(row for row in withdrawn['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['withdrawn'])
            ir = compile_oracle_card(record, capability_registry=CapabilityRegistry(withdrawn), capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            self.assertTrue(ir.material_residuals)


class StaticSettingCompilerTests(unittest.TestCase):
    def test_conditional_source_setting_preserves_public_condition_and_layer_contract(self):
        from test_qualified_zone_event_queries import query_record
        text = "During your turn, this creature has base power and toughness 5/2."
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(text, node.text)
        descriptor = node.handlers[0]
        self.assertEqual('source', descriptor['target']['kind'])
        self.assertEqual('controller_turn', descriptor['source_condition']['kind'])
        self.assertEqual((5, 2), (descriptor['modifier']['base_power'], descriptor['modifier']['base_toughness']))

    def test_conditional_source_settings_keep_layers_live_and_source_incarnation_exact(self):
        from quorune.continuous_conditions import FixedPublicStateConditionSnapshot
        from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects
        from quorune.continuous_effect_model import ContinuousEffect, ContinuousOperation, ContinuousEffectOrigin, Layer
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext

        cases = (
            ('During your turn, this creature is a Bear with base power and toughness 4/2.',
             {'Bear', 'Equipment'}, {'Artifact', 'Creature'}, 4, 2),
            ('During turns other than yours, this artifact is a 2/3 Gargoyle artifact creature with flying.',
             {'Gargoyle', 'Equipment'}, {'Artifact', 'Creature'}, 2, 3),
        )
        for index, (text, subtypes, types, power, toughness) in enumerate(cases):
            with self.subTest(text=text):
                descriptor = fixed_characteristic_setting_handler(text, source_name='Generic Setting Source')[1]
                public = FixedPublicStateConditionSnapshot(source_controller='A', active_player='A' if index == 0 else 'B',
                    controller_hand_count=2, controller_graveyard_card_count=0, controller_life=40,
                    opponent_life_totals=(40, 40, 40), turn_sequence=1,
                    source_entered_battlefield_turn_sequence=0, source_counters=())
                context = ContinuousEffectSourceContext(source_object_id='source', source_ref='SOURCE',
                    source_controller='A', source_timestamp=1, source_logical_object_id='source@0',
                    component_id='conditional-setting', public_state=public)
                handler = ConditionalCharacteristicSettingsHandler()
                effects = handler.lower(descriptor, context) + (ContinuousEffect(
                    effect_id='counter', source_id='source', layer=Layer.POWER_TOUGHNESS, sublayer='7c',
                    timestamp=0, operations=(ContinuousOperation('modify_power_toughness', [1, 1]),),
                    origin=ContinuousEffectOrigin.OBJECT),)
                base = CharacteristicState(name='Source', controller='A', card_types={'Artifact', 'Creature'},
                    subtypes={'Human', 'Equipment'}, power=1, toughness=1)
                evaluation = {'object_id': 'source', 'logical_object_id': 'source@0', 'zone': 'battlefield', 'owner': 'A'}
                result = evaluate_continuous_effects(deepcopy(base), effects, context=evaluation).characteristics
                self.assertEqual({s.casefold() for s in subtypes}, {s.casefold() for s in result['subtypes']})
                self.assertEqual(types, set(result['card_types']))
                self.assertEqual((power + 1, toughness + 1), (result['power'], result['toughness']))
                self.assertEqual((), handler.lower(descriptor, replace(context, public_state=replace(
                    public, active_player='B' if index == 0 else 'A'))))
                stale = evaluate_continuous_effects(deepcopy(base), effects,
                    context={**evaluation, 'logical_object_id': 'source@2'}).characteristics
                self.assertEqual((2, 2), (stale['power'], stale['toughness']))
                malformed = deepcopy(descriptor); malformed['target']['exclude_source'] = True
                with self.assertRaises(ValueError):handler.validate(malformed)
                with self.assertRaises(ValueError):handler.lower(descriptor, replace(context, public_state=None))
        for text in (
            'As long as you control three or more artifacts, this artifact is a 5/5 creature.',
            'During your turn, this creature has base power and toughness X/X.',
            'During your turn, this creature is a creature with base power and toughness equal to your life total.',
        ):
            self.assertIsNone(fixed_characteristic_setting_handler(text, source_name='Generic Setting Source'))

    def test_conditional_setting_grammar_schema_and_dependency_mutants_fail_closed(self):
        text = 'During your turn, this creature has base power and toughness 5/2.'
        compiled = fixed_characteristic_setting_handler(text, source_name='Generic Setting Source')
        descriptor = compiled[1]
        handler = ConditionalCharacteristicSettingsHandler()
        for field, value in (('source_condition', {'kind': 'unknown'}), ('schema_version', True),
                             ('schema_version', 1)):
            malformed = deepcopy(descriptor); malformed[field] = value
            with self.assertRaises(ValueError):handler.validate(malformed)
        registry = load_default_capability_registry()
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('continuous.characteristics.fixed_public_setting', 'continuous.characteristics.fixed_public_state'):
            blocked = deepcopy(raw)
            next(row for row in blocked['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['setting boundary'])
            ir = compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(blocked))
            self.assertNotEqual('exact', ir.status)
        with patch('quorune.compiler.runtime_templates.fixed_characteristic_setting_handler', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status)
    def test_global_permanent_subject_has_no_fictitious_card_type(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        for text in ('All permanents','Other permanents','permanents you control'):
            relation,query,excluded=fixed_characteristic_battlefield_query_subject(text)
            self.assertEqual((),query.types_all)
            self.assertEqual(('battlefield',),query.zones)
        self.assertEqual(('artifact',),fixed_characteristic_battlefield_query_subject('All artifacts')[1].types_all)

    def test_static_settings_preserve_exact_types_colors_and_source_spans(self):
        cases=(
            'Enchanted artifact is a creature with base power and toughness 5/5 in addition to its other types.',
            'Other creatures have base power and toughness 2/2 and are Bears in addition to their other types.',
            'All permanents are colorless.',
            'All creatures are black.',
            'Enchanted creature has base power and toughness 0/4, has defender, loses all other abilities, and is a blue Wall in addition to its other colors and types.',
            'Creatures you control with +1/+1 counters on them have base power and toughness 4/4, have flying, and are Angels in addition to their other types.')
        for text in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Enchantment'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0]
                self.assertEqual('static-fixed-characteristic-setting-v1',node.template_id)
                FixedCharacteristicSettingsHandler().validate(node.handlers[0])
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        compiled=fixed_characteristic_setting_handler(cases[4])[1]
        modifier=compiled['modifier']
        self.assertTrue(modifier['remove_all_abilities']);self.assertEqual(['Defender'],modifier['add_abilities'])
        self.assertEqual([{'op':'add_colors','values':['U']}],modifier['color_operations'])
        self.assertEqual(0,modifier['base_power']);self.assertEqual(4,modifier['base_toughness'])

    def test_static_setting_unsupported_grammar_and_malformed_descriptors_fail_closed(self):
        for text in ('All lands are Islands.', 'All creatures are named Example.',
                     'All creatures are every creature type.', 'All creatures have base power and toughness X/X.',
                     'As long as you control a creature, all creatures are black.',
                     'Enchanted creature is a Goblin and has "{T}: Draw a card."',
                     'Enchanted creature is a Goblin. You win the game.',
                     'All creatures are enchantments.',
                     'All creatures have base power and toughness 1/1 and have base power and toughness 2/2.',
                     'All creatures have flying and lose all abilities.'):
            self.assertIsNone(fixed_characteristic_setting_handler(text),text)
        base=fixed_characteristic_setting_handler('All creatures have base power and toughness 1/1.')[1]
        for field,value in [('schema_version',True),('event','resolve'),('extra',0)]:
            with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate({**base,field:value})
        missing=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in missing['capabilities'] if r['id']=='continuous.characteristics.fixed_public_setting').update(
            status='blocked',blockers=['Fixed setting owner withdrawn'])
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        registry=CapabilityRegistry(missing)
        index=build_index(registry_value=missing,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(replace(query_record('All creatures have base power and toughness 1/1.'),type_line='Enchantment'),
            capability_registry=registry)
        self.assertNotEqual('exact',ir.status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)

    def test_static_setting_creature_subtype_replacement_keeps_artifact_subtypes(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        descriptor=fixed_characteristic_setting_handler('Enchanted creature is a Demon Spirit.')[1]
        context=ContinuousEffectSourceContext(source_object_id='aura',source_ref='aura',source_controller='A',
            source_timestamp=1,component_id='subtype-witness',attached_object=ContinuousObjectIdentity('body','body@0'))
        effects=FixedCharacteristicSettingsHandler().lower(descriptor,context)
        base=CharacteristicState(name='Subtype witness',controller='B',card_types={'Artifact','Creature'},
            subtypes={'Human','Equipment'},power=2,toughness=3)
        result=evaluate_continuous_effects(base,effects,context={'object_id':'body','logical_object_id':'body@0','zone':'battlefield','owner':'B'}).characteristics
        self.assertEqual({'demon','spirit','equipment'},{s.lower() for s in result['subtypes']})

    def test_static_setting_attachment_incarnation_and_query_codec_remain_closed(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        descriptor=fixed_characteristic_setting_handler('Enchanted artifact is a creature with base power and toughness 5/5 in addition to its other types.')[1]
        context=ContinuousEffectSourceContext(source_object_id='aura',source_ref='aura',source_controller='A',source_timestamp=3,
            component_id='incarnation-test',attached_object=ContinuousObjectIdentity('body','body@0'))
        effects=FixedCharacteristicSettingsHandler().lower(descriptor,context)
        base=CharacteristicState(name='Artifact witness',controller='B',card_types={'Artifact'})
        result=evaluate_continuous_effects(base,effects,context={'object_id':'body','logical_object_id':'body@1','zone':'battlefield','owner':'B'}).characteristics
        self.assertEqual(['Artifact'],result['card_types']);self.assertIsNone(result['power'])
        malformed=deepcopy(descriptor);malformed['target']['predicate']['types_all']=['permanent']
        with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate(malformed)
        malformed=deepcopy(descriptor);malformed['modifier']['quantity']={'unexpected':0}
        with self.assertRaises(ValueError):FixedCharacteristicSettingsHandler().validate(malformed)


class StaticSettingRuntimeTests(unittest.TestCase):
    def cast_at_priority(self, session, seat, card, targets=()):
        for _ in range(16):
            session.engine.pump()
            decision = session.state.pending_decision
            self.assertEqual('priority', decision.kind)
            if session.state.priority_player == seat:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Casting principal did not receive priority')
        actions = session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
        current = session.state.cards[card.object_id]
        matching = [row for row in actions if row['id'] == 'cast:' + current.ref]
        self.assertTrue(matching, {'card': current.printed_name, 'zone': current.zone,
            'reference_changed': current.ref != card.ref, 'phase': session.state.phase, 'step': session.state.step,
            'mana': dict(session.state.players[seat].mana_pool),
            'offered_cast_cards': [row.get('card') for row in actions if row['id'].startswith('cast:')]})
        offered = matching[0]
        accepted = session.act('pilot:' + seat, {'action_id': offered['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)

    def assert_closed_card(self, session, card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding)
        self.assertEqual('capability_closed', binding['trust_basis'])

    def current_types(self, session, card):
        return session.engine._type_parts(session.engine._effective_card_data(session.state.cards[card.object_id])['type_line'])

    def test_actual_original_thassa_devotion_threshold_retains_abilities_counters_and_replays(self):
        session = self.session(394001); engine = session.engine
        god = self.add(engine, 'Thassa, Deep-Dwelling', zone='hand', ref='devotion-original-thassa')
        blue = self.add(engine, 'Generic Devotion Blue Artifact', zone='hand', ref='devotion-blue-four')
        bounce = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='devotion-blue-bounce')
        counter = self.add(engine, 'Generic Setting Counter Spell', zone='hand', ref='devotion-counter')
        body = self.add(engine, 'Generic Bound Body', seat='B', ref='devotion-foreign-body')
        self.add(engine, 'Forest', ref='devotion-counter-forest')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='devotion-private-hand')
        offered = self.ready(session, god, {'U': 20, 'C': 6}); self.assert_closed_card(session, god)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertIn('creature', self.current_types(session, god)[0])  # Creature spell, even below devotion.
        self.resolve(session)
        self.assertEqual({'enchantment'}, self.current_types(session, god)[0])
        self.assertNotIn('god', self.current_types(session, god)[1])
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        ability = next(row for row in actions if row['id'].startswith('activate:') and row.get('source') == god.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': ability['id'], 'targets': [body.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertEqual({'enchantment'}, self.current_types(session, god)[0])
        self.cast_at_priority(session, 'A', blue)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual({'enchantment', 'creature'}, self.current_types(session, god)[0])
        self.assertIn('god', self.current_types(session, god)[1])
        self.cast_at_priority(session, 'A', counter, [god.ref]); self.resolve(session)
        self.assertEqual(1, session.state.cards[god.object_id].counters.get('+1/+1'))
        self.cast_at_priority(session, 'A', bounce, [blue.ref])
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual({'enchantment'}, self.current_types(session, god)[0])
        self.assertEqual(1, session.state.cards[god.object_id].counters.get('+1/+1'))
        self.cast_at_priority(session, 'A', blue); self.resolve(session)
        self.assertEqual({'enchantment', 'creature'}, self.current_types(session, god)[0])
        self.assertEqual((7, 6), tuple(session.engine._numeric_stat(god.object_id, stat) for stat in ('power', 'toughness')))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        self.replay(session, load=True)

    def test_actual_hybrid_devotion_control_change_and_reentry_keep_new_incarnation(self):
        session = self.session(394002); engine = session.engine
        god = self.add(engine, 'Generic Devotion Twin God', zone='hand', ref='devotion-twin-god')
        hybrid = self.add(engine, 'Generic Devotion Hybrid Artifact', zone='hand', ref='devotion-two-hybrid')
        blue = self.add(engine, 'Generic Devotion Single Blue Artifact', zone='hand', ref='devotion-one-blue')
        self.add(engine, 'Generic Devotion Single Blue Artifact', seat='D', ref='devotion-foreign-blue')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='devotion-control')
        self.add(engine, 'Generic Bound Plains', seat='D', ref='devotion-control-payment')
        bounce = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='devotion-god-return')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='devotion-private-twin')
        offered = self.ready(session, god, {'U': 5}); self.assert_closed_card(session, god)
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok); self.resolve(session)
        self.assertNotIn('creature', self.current_types(session, god)[0])
        self.cast_at_priority(session, 'A', hybrid); self.resolve(session)
        self.assertNotIn('creature', self.current_types(session, god)[0])  # Three symbols, not six; opponent mana is excluded.
        self.cast_at_priority(session, 'A', blue); self.resolve(session)
        self.assertIn('creature', self.current_types(session, god)[0])  # Exactly four symbols.
        old_identity = session.state.cards[god.object_id].logical_object_id
        self.cast_at_priority(session, 'D', control, [god.ref])
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual('D', session.state.cards[god.object_id].controller)
        self.assertNotIn('creature', self.current_types(session, god)[0])
        self.cast_at_priority(session, 'A', bounce, [god.ref]); self.resolve(session)
        self.assertEqual('hand', session.state.cards[god.object_id].zone)
        self.assertIn('creature', self.current_types(session, god)[0])  # Card outside the battlefield.
        self.cast_at_priority(session, 'A', god); self.resolve(session)
        current = session.state.cards[god.object_id]
        self.assertEqual('A', current.controller)
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertIn('creature', self.current_types(session, god)[0])
        self.assertNotIn('haste', session.engine._combat_keywords(current))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_devotion_type_removal_precedes_persistent_ability_loss(self):
        session = self.session(394003); engine = session.engine
        god = self.add(engine, 'Thassa, Deep-Dwelling', zone='hand', ref='ability-loss-god')
        blue = self.add(engine, 'Generic Devotion Blue Artifact', ref='ability-loss-devotion')
        aura = self.add(engine, 'Generic Devotion Ability Seal', zone='hand', ref='ability-loss-aura')
        lower = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='ability-loss-lower-devotion')
        restore = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='ability-loss-restore')
        self.add(engine, 'Generic Bound Body', seat='B', ref='ability-loss-legal-activation-target')
        offered = self.ready(session, god, {'U': 8, 'C': 6})
        for card in (god, aura):
            self.assert_closed_card(session, card)
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok); self.resolve(session)
        self.assertIn('creature', self.current_types(session, god)[0])
        self.cast_at_priority(session, 'A', aura, [god.ref]); self.resolve(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:') and row.get('source') == god.ref for row in actions))
        self.cast_at_priority(session, 'A', lower, [blue.ref])
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[aura.object_id].zone)
        self.assertEqual(god.object_id, session.state.cards[aura.object_id].attached_to)
        self.assertEqual({'enchantment'}, self.current_types(session, god)[0])
        self.cast_at_priority(session, 'A', restore, [aura.ref]); self.resolve(session)
        self.assertEqual({'enchantment'}, self.current_types(session, god)[0])
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(row['id'].startswith('activate:') and row.get('source') == god.ref for row in actions))
        self.replay(session, load=True)

    def test_actual_attacking_god_leaves_combat_and_does_not_rejoin_after_devotion_returns(self):
        from quorune.model import CombatState
        session = self.session(394004); engine = session.engine
        god = self.add(engine, 'Thassa, Deep-Dwelling', ref='combat-devotion-god')
        god.acquired_control_turn_count = -1
        blue = self.add(engine, 'Generic Devotion Blue Artifact', ref='combat-devotion-four')
        bounce = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='combat-devotion-lower')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='combat-devotion-private')
        self.ready(session, bounce, {'U': 4}); self.assert_closed_card(session, god)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.phase = 'combat'; engine.state.step = 'declare_attackers'; engine.state.phase_index = 5
        engine.state.combat = CombatState(); engine._issue_attackers()
        god_identity = god.logical_object_id; blue_identity = blue.logical_object_id
        life = {seat: player.life for seat, player in session.state.players.items()}
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:D', {'action_id': 'attack', 'attackers': {god.ref: 'B'}})
        self.assertFalse(denied.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id': 'attack', 'attackers': {god.ref: 'B'}}).ok)
        self.assertIn(god.object_id, session.state.combat.attackers)
        self.cast_at_priority(session, 'A', bounce, [blue.ref]); self.resolve(session)
        self.assertNotIn('creature', self.current_types(session, god)[0])
        self.assertNotIn(god.object_id, session.state.combat.attackers)
        session = self.replay(session, load=True)
        self.cast_at_priority(session, 'A', blue); self.resolve(session)
        self.assertIn('creature', self.current_types(session, god)[0])
        self.assertNotIn(god.object_id, session.state.combat.attackers)
        for _ in range(48):
            session.engine.pump()
            if session.state.phase != 'combat':
                break
            decision = session.state.pending_decision
            response = {'action_id': 'block', 'blocks': {}} if decision.kind == 'combat.blockers' else {'action_id': 'pass'}
            self.assertTrue(session.act(session.pending_principals()[0], response).ok)
        else:
            self.fail('Combat did not finish')
        self.assertEqual(life, {seat: player.life for seat, player in session.state.players.items()})
        self.assertEqual(god_identity, session.state.cards[god.object_id].logical_object_id)
        self.assertNotEqual(blue_identity, session.state.cards[blue.object_id].logical_object_id)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_original_thassa_entry_replacement_and_post_entry_event_use_distinct_devotion(self):
        from quorune.semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot
        for devotion, expected_entry_creature, expected_draw in ((0, False, 0), (4, False, 1), (5, True, 1)):
            with self.subTest(pre_entry_devotion=devotion):
                session = self.session(394010 + devotion); engine = session.engine
                god = self.add(engine, 'Thassa, Deep-Dwelling', zone='hand', ref='entry-devotion-god')
                observer = self.add(engine, 'Generic Devotion Entry Observer', ref='entry-devotion-observer')
                if devotion >= 4:
                    self.add(engine, 'Generic Devotion Blue Artifact', ref='entry-devotion-four')
                if devotion == 5:
                    self.add(engine, 'Generic Devotion Single Blue Artifact', ref='entry-devotion-one')
                offered = self.ready(session, god, {'U': 1, 'C': 3})
                for card in (god, observer):
                    self.assert_closed_card(session, card)
                hands = {seat: set(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
                self.checkpoint(session)
                self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
                before_snapshot = authoritative_state_hash(session.state)
                snapshot = capture_zone_change_replacement_snapshot(session.engine, [(god.object_id, 'battlefield')],
                    destination_controllers={god.object_id: 'A'})
                self.assertEqual(before_snapshot, authoritative_state_hash(session.state))
                self.assertEqual(expected_entry_creature, 'creature' in snapshot.subjects[0].object_types)
                session = self.replay(session, load=True); self.resolve(session)
                current = session.state.cards[god.object_id]
                self.assertEqual(bool(expected_draw), 'creature' in self.current_types(session, god)[0])
                self.assertEqual(len(hands['A']) - 1 + expected_draw, len(session.state.players['A'].zones['hand']))
                for seat in 'BCD':
                    self.assertEqual(hands[seat], set(session.state.players[seat].zones['hand']))
                added = set(session.state.players['A'].zones['hand']) - hands['A']
                self.assertEqual(expected_draw, len(added))
                for object_id in added:
                    reference = session.state.cards[object_id].ref
                    for viewer in 'ABCD':
                        rendered = json.dumps(session.projector._snapshot('pilot:' + viewer), sort_keys=True)
                        if viewer == 'A':
                            self.assertIn(reference, rendered)
                        else:
                            self.assertNotIn(reference, rendered)
                self.replay(session, load=True)

    def test_actual_graveyard_wonder_tracks_owner_land_source_departure_and_replay(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        session = self.session(286101); engine = session.engine
        source = self.add(engine, 'Wonder', seat='B', ref='borrowed-wonder'); source.owner = 'A'
        own = self.add(engine, 'Generic Bound Body', ref='graveyard-grant-recipient')
        foreign = self.add(engine, 'Generic Bound Body', seat='C', ref='foreign-graveyard-grant')
        land = self.add(engine, 'Island', ref='owner-grant-island')
        self.add(engine, 'Island', seat='B', ref='opponent-grant-island')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='graveyard-grant-private')
        kill = self.add(engine, 'Murder', zone='hand', ref='graveyard-grant-death')
        return_source = self.add(engine, 'Raise Dead', zone='hand', ref='graveyard-grant-return')
        return_land = self.add(engine, 'Generic Conditional Land Return', zone='hand', ref='graveyard-grant-land-return')
        for number in range(4):self.add(engine, 'Swamp', ref='graveyard-action-mana-' + str(number))
        program = compile_best_available_card_program(self.db, self.db.lookup('Wonder'), semantic_registry=engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        self.assertNotIn('flying', engine._combat_keywords(own))
        action = self.ready(session, kill, {'B': 2, 'C': 1}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.resolve(session); session = self.replay(session, load=True); engine = session.engine
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertIn('flying', engine._combat_keywords(session.state.cards[own.object_id]))
        self.assertNotIn('flying', engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:A', full=True)))
        timestamp = session.state.cards[source.object_id].zone_timestamp
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row.get('card') == return_land.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [land.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertNotIn('flying', engine._combat_keywords(session.state.cards[own.object_id]))
        self.assertEqual(timestamp, session.state.cards[source.object_id].zone_timestamp)
        self.replay(session, load=True)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        current_land = session.state.cards[land.object_id]
        action = next(row for row in actions if row.get('card') == current_land.ref and row['id'].startswith('play-land:'))
        accepted = session.act('pilot:A', {'action_id': action['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIn('flying', engine._combat_keywords(session.state.cards[own.object_id]))
        self.assertEqual(timestamp, session.state.cards[source.object_id].zone_timestamp)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row.get('card') == return_source.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertNotIn('flying', engine._combat_keywords(session.state.cards[own.object_id]))
        self.replay(session, load=True)
    def test_original_doc_ock_graveyard_count_gates_setting_at_earlier_layers(self):
        from quorune.continuous_effect_model import Layer
        session = self.session(285110); engine = session.engine
        source = self.add(engine, 'Doc Ock, Sinister Scientist', ref='graveyard-setting-source')
        other = self.add(engine, 'Doc Ock, Sinister Scientist', seat='B', ref='same-name-other-source')
        for number in range(8):self.add(engine, 'Generic Bound Plains', zone='graveyard', ref='setting-grave-' + str(number))
        engine.state.active_player = 'A'
        full = engine._effective_card_data(source)
        self.assertEqual((8, 8), tuple(engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        self.assertNotEqual((8, 8), tuple(engine._numeric_stat(other.object_id, f) for f in ('power', 'toughness')))
        # The same setting condition can be collected at a type-only boundary
        # without recursively evaluating battlefield characteristic predicates.
        data = engine._effective_card_data(source, maximum_layer=Layer.TYPE)
        self.assertEqual(full['type_line'], data['type_line'])
        source.phased_out = True
        self.assertNotEqual((8, 8), tuple(engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        source.phased_out = False
        self.assertEqual((8, 8), tuple(engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))

    def test_actual_original_conditional_settings_cast_turn_control_counters_and_replay(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program

        cases = (
            ('Snowmelt Stag', {'U': 1, 'C': 3}, (5, 2), (2, 5)),
            ('Circle of the Moon Druid', {'G': 1, 'C': 2}, (4, 2), (2, 4)),
            ('Warden of the Wall', {'C': 3}, (None, None), (2, 3)),
        )
        for index, (name, mana, own_stats, other_stats) in enumerate(cases):
            with self.subTest(name=name):
                session = self.session(285100 + index); engine = session.engine
                source = self.add(engine, name, zone='hand', ref='original-conditional-setting')
                counter = self.add(engine, 'Generic Setting Counter Spell', zone='hand', ref='conditional-setting-counter')
                snake = self.add(engine, 'Snakeform', zone='hand', ref='conditional-setting-removal')
                for number in range(5):self.add(engine, 'Forest', ref='conditional-counter-mana-' + str(number))
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='private-setting-hand')
                program = compile_best_available_card_program(self.db, self.db.lookup(name), semantic_registry=engine.semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])
                action = self.ready(session, source, mana); self.checkpoint(session)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:C', {'action_id': action['id'], 'pay': 'auto'}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
                session = self.replay(session, load=True); engine = session.engine
                body = session.state.cards[source.object_id]
                def stats():
                    data = engine._effective_card_data(body)
                    if 'creature' not in engine._type_parts(data['type_line'])[0]:
                        return (None, None)
                    return tuple(engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness'))
                self.assertEqual(own_stats, stats())
                for seat in 'ABD':self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
                # A read-only condition contrast; restore before recording actions.
                engine.state.active_player = 'B'
                self.assertEqual(other_stats, stats())
                engine.state.active_player = 'A'
                self.assertEqual(own_stats, stats())
                if index < 2:
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    action = next(row for row in actions if row.get('card') == counter.ref)
                    accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [body.ref], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
                    self.assertEqual(tuple(v + 1 for v in own_stats), stats())
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    action = next(row for row in actions if row.get('card') == snake.ref)
                    accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [body.ref], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary); self.resolve(session)
                    self.assertEqual((2, 2), stats())
                    self.assertEqual({'snake'}, engine._type_parts(engine._effective_card_data(body)['type_line'])[1])
                self.replay(session, load=True)
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'settings.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/static-characteristic-setting-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json',ROOT/'tests/fixtures/scryfall-exact-lists.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static setting witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_ensoul_offer_animation_counters_source_departure_and_replay(self):
        session=self.session(284001);engine=session.engine
        body=self.add(engine,'Generic Setting Artifact',seat='B');body.counters['+1/+1']=2
        aura=self.add(engine,'Ensoul Artifact',zone='hand')
        spell=self.add(engine,'Generic Setting Bounce',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-setting-c')
        nonartifact=self.add(engine,'Generic Bound Body',seat='B',ref='nonartifact-body')
        action=self.ready(session,aura,{'U':2,'C':1})
        self.assertIn(body.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(nonartifact.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(private.ref,str(session.packet('pilot:B',full=True)))
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        rejected=session.act('pilot:A',{'action_id':action['id'],'targets':[nonartifact.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        body=session.state.cards[body.object_id];aura=session.state.cards[aura.object_id]
        data=engine._effective_card_data(body)
        self.assertEqual({'artifact','creature'},engine._type_parts(data['type_line'])[0])
        self.assertEqual((7,7),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual(aura.object_id,body.attachments[0])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        bounce=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':bounce['id'],'targets':[aura.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual({'artifact'},engine._type_parts(engine._effective_card_data(body)['type_line'])[0])
        self.replay(session,load=True)

    def test_actual_kudo_changes_other_creatures_across_controllers_with_replay(self):
        session=self.session(284002);engine=session.engine
        source=self.add(engine,'Kudo, King Among Bears',zone='hand')
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        action=self.ready(session,source,{'G':1,'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((4,4),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual((2,2),(engine._numeric_stat(source.object_id,'power'),engine._numeric_stat(source.object_id,'toughness')))
        self.assertIn('bear',engine._type_parts(engine._effective_card_data(body)['type_line'])[1])
        self.replay(session,load=True)

    def test_static_setting_source_removal_and_ability_order_use_current_layers(self):
        session=self.session(284003);engine=session.engine
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        source=self.add(engine,'Humility',zone='hand')
        spell=self.add(engine,'Generic Setting Bounce',zone='hand')
        action=self.ready(session,source,{'W':2,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((3,3),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('flying',tuple(k.lower() for k in data['keywords']))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        bounce=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':bounce['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((8,9),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertIn('flying',tuple(k.lower() for k in data['keywords']))
        self.replay(session,load=True)

    def test_actual_deep_freeze_keeps_original_types_and_own_defender(self):
        session=self.session(284004);engine=session.engine
        body=self.add(engine,'Generic Setting Large Body',seat='B');body.counters['+1/+1']=2
        aura=self.add(engine,'Deep Freeze',zone='hand')
        action=self.ready(session,aura,{'U':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(body)
        self.assertEqual((2,6),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        types,subtypes,_=engine._type_parts(data['type_line'])
        self.assertEqual({'artifact','creature'},types)
        self.assertEqual({'construct','wall'},subtypes)
        self.assertIn('U',data['colors'])
        self.assertEqual({'defender'},{k.lower() for k in data['keywords']})
        self.replay(session,load=True)

    def test_static_setting_started_effect_continues_when_source_loses_ability(self):
        session=self.session(284005);engine=session.engine
        source=self.add(engine,'Humility')
        body=self.add(engine,'Generic Setting Large Body',seat='B')
        spell=self.add(engine,'Generic Setting Animation',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertIn('creature',engine._type_parts(engine._effective_card_data(source)['type_line'])[0])
        self.assertEqual((1,1),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
        self.replay(session,load=True)

    def test_static_setting_color_timestamps_and_source_departure_replay(self):
        for first,second,expected in (('Darkest Hour','Thran Lens',set()),('Thran Lens','Darkest Hour',{'B'})):
            with self.subTest(first=first):
                session=self.session(284006 if first=='Darkest Hour' else 284007);engine=session.engine
                body=self.add(engine,'Generic Setting Large Body',seat='B')
                earlier=self.add(engine,first)
                earlier.zone_timestamp=0
                later=self.add(engine,second,zone='hand')
                bounce=self.add(engine,'Generic Setting Bounce',zone='hand')
                action=self.ready(session,later,{'B':1,'C':2});self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual(expected,set(engine._effective_card_data(body)['colors']))
                self.assertEqual(set(),set(engine._copyable_characteristics(body)['colors']))
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                action=next(row for row in actions if row.get('card')==bounce.ref)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[later.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual({'B'} if first=='Darkest Hour' else set(),set(engine._effective_card_data(body)['colors']))
                self.replay(session,load=True)

    def test_static_setting_older_removal_and_newer_grant_follow_timestamp_order(self):
        for removal_first in (True,False):
            with self.subTest(removal_first=removal_first):
                session=self.session(284008 if removal_first else 284009);engine=session.engine
                body=self.add(engine,'Generic Setting Large Body',seat='B')
                humility=self.add(engine,'Humility',zone='battlefield' if removal_first else 'hand')
                if removal_first:humility.zone_timestamp=0
                flight=self.add(engine,'Generic Setting Flight',zone='hand')
                action=self.ready(session,flight,{'W':2,'C':2});self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
                if not removal_first:
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==humility.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                    self.assertNotIn('flying',{k.lower() for k in engine._effective_card_data(body)['keywords']})
                self.replay(session,load=True)

    def test_actual_equipment_setting_tracks_reciprocal_recipient_and_reequip(self):
        session=self.session(284010);engine=session.engine
        first=self.add(engine,'Generic Setting Large Body',ref='first')
        second=self.add(engine,'Generic Setting Large Body',ref='second')
        source=self.add(engine,'Angelic Armaments')
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program=compile_best_available_card_program(self.db,self.db.lookup('Angelic Armaments'),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        engine.state.started=True;engine.state.active_player='A';engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['A'].mana_pool.update({'C':8});engine._grant_priority('A');engine.pump()
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('source')==source.ref)
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((8,9),(engine._numeric_stat(first.object_id,'power'),engine._numeric_stat(first.object_id,'toughness')))
        self.assertEqual({'construct','angel'},engine._type_parts(engine._effective_card_data(first)['type_line'])[1])
        self.assertIn('W',engine._effective_card_data(first)['colors'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('source')==source.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[second.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((6,7),(engine._numeric_stat(first.object_id,'power'),engine._numeric_stat(first.object_id,'toughness')))
        self.assertEqual((8,9),(engine._numeric_stat(second.object_id,'power'),engine._numeric_stat(second.object_id,'toughness')))
        self.assertEqual(second.object_id,source.attached_to)
        self.replay(session,load=True)

    def test_actual_counter_qualified_static_set_distinguishes_controller_and_enemy(self):
        session=self.session(284011);engine=session.engine
        marked=self.add(engine,'Generic Setting Large Body',ref='own-marked');marked.counters['+1/+1']=1
        unmarked=self.add(engine,'Generic Setting Large Body',ref='own-unmarked')
        enemy=self.add(engine,'Generic Setting Large Body',seat='B',ref='enemy-marked');enemy.counters['+1/+1']=1
        source=self.add(engine,"Sigarda's Summons",zone='hand')
        action=self.ready(session,source,{'W':2,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((5,5),(engine._numeric_stat(marked.object_id,'power'),engine._numeric_stat(marked.object_id,'toughness')))
        self.assertEqual((6,7),(engine._numeric_stat(unmarked.object_id,'power'),engine._numeric_stat(unmarked.object_id,'toughness')))
        self.assertEqual((7,8),(engine._numeric_stat(enemy.object_id,'power'),engine._numeric_stat(enemy.object_id,'toughness')))
        self.assertIn('angel',engine._type_parts(engine._effective_card_data(marked)['type_line'])[1])
        self.assertNotIn('angel',engine._type_parts(engine._effective_card_data(enemy)['type_line'])[1])
        self.replay(session,load=True)

    def test_static_setting_mutant_is_killed(self):
        from quorune.semantic_runtime import fixed_characteristic_settings as owner
        original=owner.fixed_characteristic_effects
        def omitted(node,context,*,common):
            return tuple(effect for effect in original(node,context,common=common) if effect.sublayer!='7b')
        with patch.object(owner,'fixed_characteristic_effects',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_actual_kudo_changes_other_creatures_across_controllers_with_replay()


if __name__=='__main__':unittest.main()

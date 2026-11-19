"""Counters belong to the sealed source entry, before entry observation and SBAs."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from test_granted_skulk import record
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.self_zone_move import SelfZoneMoveError, SelfZoneMoveSpec, compile_self_zone_move
from quorune.self_zone_move_followups import compiled_self_entry
from quorune.source_self_entry_counter_model import (
    CAPABILITY, FIELD, FixedSelfEntryCounter, compile_source_self_entry_counter,
    source_self_entry_counter_from_effect,
)
from scripts.build_test_database import build_fixture_database
from quorune.activation_cost_modifiers import (
    OPPONENT_CONTROLLED_ACTIVATION_CAPABILITY, OpponentControlledActivationReduction,
    ability_local_mana_reduction_from_dict, ability_local_reduction_origin_is_supported,
)
from quorune.compiler.activated_costs import parse_compiled_activation_line


class SourceSelfEntryCounterModelTests(unittest.TestCase):
    def ability(self, text):
        ability, = parse_activated_abilities(card_name='Generic Source Entry', oracle_text=text)
        return ability

    def test_fixed_counter_spec_roundtrips_and_preserves_complete_ability(self):
        for tapped, count, text in [(True, 2, 'tapped with two +1/+1 counters'),
                                   (False, 1, 'with a +1/+1 counter')]:
            ability = self.ability('{3}{B}: Return this card from your graveyard to the battlefield '+text+' on it.')
            spec = compile_self_zone_move(ability)
            self.assertIsNotNone(spec)
            self.assertEqual((3, tapped, count), (spec.schema_version, spec.tapped, spec.entry_counter.amount))
            self.assertEqual(ability.effect_text, spec.ability.effect_text)
            self.assertEqual(('graveyard',), spec.ability.zones)
            self.assertEqual(spec, SelfZoneMoveSpec.from_dict(spec.to_dict()))
            self.assertEqual((spec.effect(),), compiled_self_entry(spec)[1])

    def test_unknown_variable_linked_and_wrong_grammatical_shapes_reject(self):
        for text in ('with X +1/+1 counters', 'with two +1/+1 counter', 'with a +1/+1 counters',
                     'with two shield counters', 'with two -1/-1 counters', 'with eleven +1/+1 counters'):
            body = 'Return this card from your graveyard to the battlefield '+text+' on it.'
            self.assertIsNone(compile_source_self_entry_counter(body))
            self.assertIsNone(compile_self_zone_move(self.ability('{3}{B}: '+body)))
        self.assertIsNone(compile_self_zone_move(self.ability(
            '{3}{B}: Return this card from your graveyard to the battlefield with two +1/+1 counters on it. Draw a card.')))

    def test_new_counter_data_cannot_change_legacy_or_followup_versions(self):
        for suffix, version in [('.', 1), ('. You skip your next turn.', 2)]:
            spec = compile_self_zone_move(self.ability(
                '{3}{B}: Return this card from your graveyard to the battlefield tapped'+suffix))
            self.assertEqual(version, spec.schema_version)
            self.assertNotIn('entry_counter', spec.to_dict())
            self.assertNotIn(FIELD, spec.effect())
            self.assertEqual(spec, SelfZoneMoveSpec.from_dict(spec.to_dict()))
            with self.assertRaises(SelfZoneMoveError):replace(spec, entry_counter=FixedSelfEntryCounter(2))

    def test_counter_descriptor_and_spec_reject_boolean_amount_version_and_body_drift(self):
        spec = compile_self_zone_move(self.ability(
            '{3}{B}: Return this card from your graveyard to the battlefield tapped with two +1/+1 counters on it.'))
        for change in ({'version': True}, {'version': 1.0}, {'amount': True}, {'amount': 0},
                       {'counter_name': 'shield'}, {'amount': 11}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                FixedSelfEntryCounter.from_dict({**spec.entry_counter.to_dict(), **change})
        for change in ({'schema_version': True}, {'schema_version': 3.0}, {'schema_version': 1},
                       {'tapped': False}, {'entry_counter': FixedSelfEntryCounter(1).to_dict()}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                SelfZoneMoveSpec.from_dict({**spec.to_dict(), **change})

    def test_effect_shape_rejects_new_origin_other_recipient_and_ordinary_counter_instruction(self):
        spec = compile_self_zone_move(self.ability(
            '{3}{B}: Return this card from your graveyard to the battlefield tapped with two +1/+1 counters on it.'))
        for change in ({'origin': 'exile'}, {'destination': 'hand'}, {'source_form': 'aura'},
                       {'controller': 'B'}, {'tapped': 1}, {FIELD: {}}, {'op': 'add_counter'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                source_self_entry_counter_from_effect({**spec.effect(), **change})

    def test_compiler_uses_counter_and_movement_authority_with_nearest_negative(self):
        registry = load_default_capability_registry()
        body = '{3}{B}: Return this card from your graveyard to the battlefield tapped with two +1/+1 counters on it.'
        ir = compile_oracle_card(record(body, 'Artifact Creature — Zombie'), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertTrue({CAPABILITY, 'counter.producer.effect_entry', 'zone.self_move.activated'}
                        <= set(node.capability_dependencies))
        self.assertEqual('graveyard', node.active_zone)
        self.assertEqual(1, len(node.effects))
        self.assertEqual(2, node.effects[0][FIELD]['amount'])
        self.assertNotEqual('exact', compile_oracle_card(record(body.replace('two +1/+1', 'X +1/+1'),
            'Artifact Creature — Zombie'), capability_registry=registry).status)


class OpponentControlledActivationPriceModelTests(unittest.TestCase):
    body = ('{4}{B}{B}: Return this card from your graveyard to the battlefield with a +1/+1 counter on it. '
            'This ability costs {4} less to activate if an opponent controls four or more nonbasic lands.')

    def test_versioned_opponent_cost_preserves_counter_body_and_seals_graveyard_origin(self):
        ability, = parse_compiled_activation_line(self.body, card_name='Generic Price', keywords=())[0]
        reduction = ability.ability_local_mana_reduction
        self.assertIsInstance(reduction, OpponentControlledActivationReduction)
        self.assertEqual((4, 4), (reduction.generic, reduction.minimum))
        self.assertEqual(reduction, ability_local_mana_reduction_from_dict(reduction.to_dict()))
        self.assertEqual(('graveyard',), ability.zones)
        self.assertNotIn('This ability costs', ability.effect_text)
        spec = compile_self_zone_move(ability)
        self.assertEqual((3, False, 1), (spec.schema_version, spec.tapped, spec.entry_counter.amount))
        self.assertEqual(ability, type(ability).from_dict(ability.to_dict()))
        self.assertTrue(ability_local_reduction_origin_is_supported(('graveyard',), False, reduction))
        for zone in ('hand', 'battlefield', 'exile'):
            self.assertFalse(ability_local_reduction_origin_is_supported((zone,), False, reduction))

    def test_opponent_price_rejects_wrong_query_version_boolean_and_hidden_threshold(self):
        from quorune.object_predicate import ObjectQuerySpec
        reduction = OpponentControlledActivationReduction(4, 4)
        for change in ({'schema_version': True}, {'schema_version': 4.0}, {'generic': True},
                {'minimum': True}, {'minimum': 0}, {'generic': 0}, {'extra': 1},
                {'opponent_controlled_query': ObjectQuerySpec(zones=('hand',)).to_dict()},
                {'opponent_controlled_query': ObjectQuerySpec(zones=('battlefield',), types_all=('land',)).to_dict()}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ability_local_mana_reduction_from_dict({**reduction.to_dict(), **change})
        for text in (self.body.replace('an opponent controls', 'your opponents control'),
                self.body.replace('four or more nonbasic lands', 'four or more cards in hand'),
                self.body.replace('{4} less', '{B} less'),
                self.body.replace('with a +1/+1 counter on it.', 'with X +1/+1 counters on it.')):
            ability, = parse_compiled_activation_line(text, card_name='Generic Price', keywords=())[0]
            self.assertIsNone(ability.ability_local_mana_reduction)

    def test_typed_public_rows_are_counted_per_current_active_opponent(self):
        from types import SimpleNamespace
        from quorune.rules.activation.cost_modifiers import opponent_controlled_activation_threshold
        from quorune.object_query import object_query_result
        from quorune.engine import CommanderEngine
        def card(seat, ordinal, *, basic=False, phased=False, land=True):
            return SimpleNamespace(object_id=seat+str(ordinal), ref=seat+str(ordinal), zone='battlefield',
                owner=seat, controller=seat, phased_out=phased, face_down=False, counters={}, tapped=False,
                logical_object_id=seat+str(ordinal), printed_name='Generic Public Land', annotations={},
                is_commander=False, is_token=False, object_kind='card', monstrous_value=None, renowned=False,
                attacking=None, blocking=None, types=('land',) if land else ('artifact',), basic=basic)
        cards = [card('A', i) for i in range(4)] + [card('B', i) for i in range(2)] + [card('C', i) for i in range(2)]
        def row(card, **kwargs):
            data = {'type_line': ('Basic ' if card.basic else '')+('Land — Swamp' if card.types == ('land',) else 'Artifact')}
            return object_query_result(card, data, type_parts=CommanderEngine._type_parts(data['type_line']),
                                       known_to_actor=True, attached_to_ref=None)
        host = SimpleNamespace(active_seats=('A','B','C'), state=SimpleNamespace(cards={c.object_id:c for c in cards}),
                               _public_object_query_result=row)
        reduction = OpponentControlledActivationReduction(4, 4)
        self.assertFalse(opponent_controlled_activation_threshold(host, 'A', reduction))
        for i in (2,3):host.state.cards['B'+str(i)] = card('B', i)
        self.assertTrue(opponent_controlled_activation_threshold(host, 'A', reduction))
        for field in ('basic', 'phased_out'):
            setattr(host.state.cards['B3'], field, True)
            self.assertFalse(opponent_controlled_activation_threshold(host, 'A', reduction))
            setattr(host.state.cards['B3'], field, False)
        host.active_seats=('A','C')
        self.assertFalse(opponent_controlled_activation_threshold(host, 'A', reduction))

    def test_complete_source_program_requires_both_counter_and_cost_authority(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record("This creature can't block.\n"+self.body,
            'Artifact Creature — Zombie'), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = [node for node in ir.faces[0].nodes if node.event == 'activate']
        self.assertTrue({CAPABILITY, OPPONENT_CONTROLLED_ACTIVATION_CAPABILITY} <= set(node.capability_dependencies))


class SourceSelfEntryCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'source-entry-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-self-entry-fixed-counter-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source entry counters', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def activate(self, session, source):
        self.priority(session, 'A')
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'].startswith('activate:'+source.ref+':')]
        command = {'action_id': offered['id'], 'targets': [], 'pay': 'auto'}
        self.reject(session, 'B', command)
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_retrofitted_enters_tapped_with_counters_before_lethal_static_toughness(self):
        session = self.session(12260301)
        source = self.add(session.engine, 'Retrofitted Transmogrant', zone='graveyard')
        norn = self.add(session.engine, 'Elesh Norn, Grand Cenobite', seat='B')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, norn):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 3, 'B': 1})
        self.checkpoint(session)
        self.activate(session, source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertTrue(current.tapped)
        self.assertEqual(2, current.counters.get('+1/+1', 0))
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(current.object_id, key)
                                     for key in ('power', 'toughness')))
        self.finish(session, private)

    def offered(self, session, source):
        self.priority(session, 'A')
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        return next((row for row in rows if row['id'].startswith('activate:'+source.ref+':')), None)

    def test_actual_razorlash_prices_one_opponent_and_keeps_both_black_mana(self):
        session = self.session(12260401)
        source = self.add(session.engine, 'Razorlash Transmogrant', zone='graveyard')
        donate = self.add(session.engine, 'Donate', zone='hand')
        own = [self.add(session.engine, 'Underground Sea', ref='own-land-'+str(i)) for i in range(4)]
        for land in own:land.tapped = True
        for i in range(3):self.add(session.engine, 'Darksteel Citadel', seat='B', ref='b-land-'+str(i))
        self.add(session.engine, 'Darksteel Citadel', seat='C', ref='c-land')
        self.add(session.engine, 'Generic Bound Plains', seat='B')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, donate, own[0]):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 2, 'U': 1, 'B': 2})
        self.checkpoint(session)
        self.assertIsNone(self.offered(session, source))
        self.cast_for(session, 'A', donate, ['B', own[0].ref], reserve={'B': 2})
        self.assertIsNone(self.resolve(session))
        self.assertEqual('B', session.state.cards[own[0].object_id].controller)
        self.assertIsNotNone(self.offered(session, source))
        self.activate(session, source)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertFalse(current.tapped)
        self.assertEqual(1, current.counters.get('+1/+1', 0))
        self.finish(session, private)

    def test_actual_razorlash_locks_paid_reduction_before_a_land_leaves(self):
        session = self.session(12260402)
        source = self.add(session.engine, 'Razorlash Transmogrant', zone='graveyard')
        lands = [self.add(session.engine, 'Underground Sea', seat='B', ref='opposing-land-'+str(i)) for i in range(4)]
        response = self.add(session.engine, 'Boomerang', seat='B', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, lands[0], response):self.assert_closed(session, card)
        self.prepare(session, mana={'B': 2})
        session.state.players['B'].mana_pool['U'] = 2
        self.checkpoint(session)
        self.activate(session, source)
        self.cast(session, 'B', response, [lands[0].ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[lands[0].object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        self.finish(session, private)

    def test_razorlash_does_not_discount_colored_mana_or_phased_opposing_lands(self):
        for mana, phased in [({'B': 1}, False), ({'B': 2}, True)]:
            session = self.session(12260403+int(phased))
            source = self.add(session.engine, 'Razorlash Transmogrant', zone='graveyard')
            lands = [self.add(session.engine, 'Underground Sea', seat='B', ref='opposing-land-'+str(i)) for i in range(4)]
            lands[-1].phased_out = phased
            self.assert_closed(session, source)
            self.prepare(session, mana=mana)
            self.checkpoint(session)
            self.assertIsNone(self.offered(session, source))

    def test_new_cost_capability_is_required_without_coverage_annotations(self):
        session = self.session(12260410)
        source = self.add(session.engine, 'Razorlash Transmogrant', zone='graveyard')
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event == 'activate']
        deps = tuple(cap for cap in program.capability_dependencies if cap != OPPONENT_CONTROLLED_ACTIVATION_CAPABILITY)
        stripped = replace(program, coverage=(), capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+OPPONENT_CONTROLLED_ACTIVATION_CAPABILITY, bound['blockers'])

    def test_fresh_missing_opponent_reduction_is_killed_by_actual_payment(self):
        from quorune.rules.activation import cost_modifiers
        method = 'test_actual_razorlash_prices_one_opponent_and_keeps_both_black_mana'
        with patch.object(cost_modifiers, 'opponent_controlled_activation_threshold', return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()

    def test_actual_scales_modifies_entry_and_solemnity_prevents_entry_counters(self):
        for control, expected in [('Hardened Scales', 3), ('Solemnity', 0)]:
            with self.subTest(control=control):
                session = self.session(12260302+expected)
                source = self.add(session.engine, 'Retrofitted Transmogrant', zone='graveyard')
                owner = self.add(session.engine, control)
                private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
                for card in (source, owner):self.assert_closed(session, card)
                self.prepare(session, mana={'C': 3, 'B': 1})
                self.checkpoint(session)
                self.activate(session, source)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.assertEqual(expected, session.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.finish(session, private)

    def test_actual_crypt_response_exiles_original_without_return_or_entry_counters(self):
        session = self.session(12260310)
        source = self.add(session.engine, 'Retrofitted Transmogrant', zone='graveyard')
        crypt = self.add(session.engine, "Tormod's Crypt", seat='B')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, crypt):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 3, 'B': 1})
        self.checkpoint(session)
        self.activate(session, source)
        self.priority(session, 'B')
        rows = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in rows if row['id'].startswith('activate:'+crypt.ref+':')]
        result = session.act('pilot:B', {'action_id': offered['id'], 'targets': ['A'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual('exile', current.zone)
        self.assertFalse(current.counters)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        self.finish(session, private)

    def test_payload_minimum_survives_coverage_removal_and_rejects_stripped_marker(self):
        session = self.session(12260311)
        source = self.add(session.engine, 'Retrofitted Transmogrant', zone='graveyard')
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event == 'activate']
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        stripped = replace(program, coverage=(), capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])
        effect = deepcopy(dict(program.effects[0]))
        effect.pop(FIELD)
        broken = replace(program, effects=(effect,), coverage=())
        self.assertFalse(bind_semantic_program_runtime(broken, capability_registry=self.registry,
                                                     profile='commander_review')['strict'])

    def test_fresh_entry_counter_omission_is_killed_by_original_native_action(self):
        from quorune.engine import CommanderEngine
        original = CommanderEngine.move_object_intent
        def omit(engine, intent):
            return original(engine, replace(intent, effect_entry_counters=()))
        method = 'test_actual_retrofitted_enters_tapped_with_counters_before_lethal_static_toughness'
        with patch.object(CommanderEngine, 'move_object_intent', omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()

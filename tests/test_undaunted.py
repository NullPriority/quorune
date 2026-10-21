from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.compiler.cast_cost_modifier_templates import self_spell_cost_reduction_handler
from quorune.self_cast_reductions import CastReductionMetric, CastReductionMetricKind, cast_reduction_multiplier
from quorune.semantic_runtime.cast_costs import SelfSpellCostReductionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as actions
import test_fixed_self_cost_surfaces as costs


class UndauntedCompilerTests(unittest.TestCase):
    def compile(self, text):
        return compile_oracle_card(replace(record(text, 'Sorcery'), keywords=('Undaunted',)),
            capability_registry=load_default_capability_registry())

    def test_keyword_instances_and_equivalent_text_keep_exact_spans_and_independent_terms(self):
        for text, instances in (('Undaunted', 1), ('Undaunted, undaunted', 2),
                ('Undaunted\nUndaunted', 2),
                ('Undaunted (This spell costs {1} less to cast for each opponent.)', 1),
                ('This spell costs {1} less to cast for each opponent you have.', 1)):
            ir = self.compile(text)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            terms = []
            for face in ir.faces:
                for node in face.nodes:
                    self.assertEqual(node.text, text[node.span.start:node.span.end])
                    for descriptor in node.handlers:
                        terms.extend(SelfSpellCostReductionHandler().validate(descriptor).terms)
            self.assertEqual(instances, len(terms))
            for term in terms:
                self.assertEqual(CastReductionMetricKind.OPPONENT_COUNT, term.metric.kind)
                self.assertEqual((('GENERIC', 1),), term.reduction)

    def test_closed_opponent_metric_excludes_caster_and_eliminated_seats_without_mutation(self):
        from itertools import product
        metric = CastReductionMetric(kind=CastReductionMetricKind.OPPONENT_COUNT)
        self.assertEqual(metric, CastReductionMetric.from_dict(metric.to_dict()))
        for queries in ([], {}, ''):
            with self.assertRaises(ValueError):
                replace(metric, queries=queries)
        for mask in product((False, True), repeat=4):
            players = {seat:SimpleNamespace(in_game=live) for seat, live in zip('ABCD', mask)}
            host = SimpleNamespace(state=SimpleNamespace(players=players))
            for caster in 'ABCD':
                expected = sum(live for seat, live in zip('ABCD', mask) if seat != caster)
                self.assertEqual(expected, cast_reduction_multiplier(host, caster, metric))
            self.assertEqual(mask, tuple(player.in_game for player in players.values()))
        descriptor = self_spell_cost_reduction_handler('This spell costs {1} less to cast for each opponent.')[1]
        for field, value in (('minimum', 1), ('maximum', 2), ('require_all', True),
                ('color', 'B'), ('turn_fact', 'creature_died'), ('counter_name', 'charge'), ('unknown', 1)):
            changed = json.loads(json.dumps(descriptor))
            changed['reduction']['terms'][0]['metric'][field] = value
            with self.subTest(field=field), self.assertRaises(SemanticNodeError):
                SelfSpellCostReductionHandler().validate(changed)
        for text, kind in (('creature in your party', 'party_size'),
                ('color among permanents you control', 'permanent_color_count'),
                ('land your opponents control that could produce {C}', 'opponent_colorless_land_count')):
            descriptor = self_spell_cost_reduction_handler('This spell costs {1} less to cast for each ' + text + '.')[1]
            legacy = SelfSpellCostReductionHandler().validate(descriptor).terms[0].metric
            self.assertEqual((kind, 2), (legacy.kind.value, legacy.schema_version))

    def test_modified_keyword_and_withdrawn_cost_owner_remain_residual(self):
        for text in ('Undaunted {2}', 'Undaunted, unless an opponent pays {1}.',
                'Undaunted for each creature.', 'Undaunted and draw a card.'):
            self.assertNotEqual('exact', self.compile(text).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('casting.cost.undaunted', 'casting.cost.self_public_reduction'):
            changed = json.loads(json.dumps(raw))
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['Undaunted dependency omitted'])
            self.assertNotEqual('exact', compile_oracle_card(
                replace(record('Undaunted', 'Sorcery'), keywords=('Undaunted',)),
                capability_registry=CapabilityRegistry(changed)).status)

    def test_original_keyword_preserves_complete_and_unrepresented_result_bodies(self):
        data = json.loads((ROOT / 'tests/fixtures/undaunted-cards.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cards.sqlite3'
            build_fixture_database([ROOT / 'tests/fixtures/undaunted-cards.json'], path)
            with CardDatabase(path) as db:
                for row in data['cards']:
                    card = db.by_oracle_id(row['oracle_id'])
                    ir = compile_oracle_card(card, capability_registry=load_default_capability_registry())
                    expected = 'partial' if card.name == 'Divergent Transformations' else 'exact'
                    with self.subTest(name=card.name):
                        self.assertEqual(expected, ir.status, ir.material_residuals)
                        if card.name == 'Seeds of Renewal':
                            self.assertTrue(any('zone.exile.resolving_spell' in node.capability_dependencies for face in ir.faces for node in face.nodes))


class UndauntedRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    replay = actions.SpreeModeCostRuntimeTests.replay
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    reject = actions.SpreeModeCostRuntimeTests.reject
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    cost = costs.FixedSelfCostSurfaceRuntimeTests.cost

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'undaunted.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/undaunted-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Undaunted witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def offered(self, session, card, actor='A'):
        return next((row for row in session.packet('pilot:' + actor, full=True)['decision']['ctx']['legal']['actions']
                     if row['id'] == 'cast:' + session.state.cards[card.object_id].ref), None)

    def test_actual_curtains_call_advertises_and_accepts_reduced_cost_with_two_targets(self):
        session = self.session(70212501)
        spell = self.add(session.engine, "Curtains' Call", zone='hand')
        first = self.add(session.engine, 'Generic Bound Body', seat='B', ref='curtains-first')
        second = self.add(session.engine, 'Generic Bound Body', seat='C', ref='curtains-second')
        spared = self.add(session.engine, 'Generic Bound Body', seat='D', ref='curtains-spared')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='curtains-private')
        self.prepare(session, mana={'C': 2, 'B': 1})
        self.checkpoint(session)
        offer = self.offered(session, spell)
        self.assertIsNotNone(offer, 'Undaunted must expose the affordable native cast')
        self.assertEqual({'GENERIC': 2, 'B': 1}, {k:v for k,v in self.cost(session, spell).items() if v})
        self.assert_closed(session, spell)
        for actor, targets, payment in (('B', [first.ref, second.ref], {'C':2,'B':1}),
                ('A', [first.ref], {'C':2,'B':1}), ('A', [first.ref, first.ref], {'C':2,'B':1}),
                ('A', [first.ref, second.ref], {'B':1})):
            self.reject(session, offer['id'], actor, targets=targets, pay='manual', payment=payment)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'targets':[first.ref, second.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(6, session.engine._effective_card_data(session.state.cards[spell.object_id])['mana_value'])
        session = self.replay(session, load=True)
        self.resolve(session)
        for card in (spell, first, second):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[spared.object_id].zone)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_elimination_updates_next_cast_price_and_never_reprices_paid_stack_spell(self):
        session = self.session(70212502)
        spell = self.add(session.engine, 'Sublime Exhalation', zone='hand')
        first = self.add(session.engine, 'Generic Bound Body', seat='B', ref='sublime-first')
        second = self.add(session.engine, 'Generic Bound Body', seat='C', ref='sublime-second')
        land = self.add(session.engine, 'Generic Bound Plains', ref='sublime-land')
        session.state.players['A'].mana_pool.update({'C':4, 'W':1})
        self.prepare(session, actor='D')
        self.checkpoint(session)
        accepted = session.act('pilot:D', {'action_id':'concede', 'confirm_concede':True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.players['D'].in_game)
        self.assertIsNotNone(self.offered(session, spell))
        self.assertEqual({'GENERIC':4, 'W':1}, {k:v for k,v in self.cost(session, spell).items() if v})
        self.assert_closed(session, spell)
        accepted = session.act('pilot:A', {'action_id':'cast:' + spell.ref, 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        for _ in range(8):
            if session.state.priority_player == 'B':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        else:
            self.fail('The next active opponent must receive response priority')
        accepted = session.act('pilot:B', {'action_id':'concede', 'confirm_concede':True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.players['B'].in_game)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[second.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session, load=True)

    def test_actual_coastal_breach_preserves_lands_returns_to_owners_and_private_views(self):
        session = self.session(70212503)
        spell = self.add(session.engine, 'Coastal Breach', zone='hand')
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B', ref='breach-foreign-control')
        foreign.owner = 'A'
        other = self.add(session.engine, 'Generic Bound Body', seat='C', ref='breach-other')
        lands = [self.add(session.engine, 'Generic Bound Plains', seat=seat, ref='breach-land-' + seat) for seat in 'ABCD']
        private = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='breach-private')
        self.prepare(session, mana={'C':3, 'U':1})
        self.checkpoint(session)
        self.assert_closed(session, spell)
        self.assertEqual({'GENERIC':3, 'U':1}, {k:v for k,v in self.cost(session, spell).items() if v})
        accepted = session.act('pilot:A', {'action_id':'cast:' + spell.ref, 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        for card, owner in ((foreign, 'A'), (other, 'C')):
            self.assertEqual('hand', session.state.cards[card.object_id].zone)
            self.assertIn(card.object_id, session.state.players[owner].zones['hand'])
        for card in lands:
            self.assertEqual('battlefield', session.state.cards[card.object_id].zone)
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_native_self_reduction_omission_is_killed(self):
        with patch('quorune.rules.casting.static_modifiers.compiled_self_spell_cost_reduction_specs', return_value=()):
            with self.assertRaises(AssertionError):
                type(self)('test_actual_curtains_call_advertises_and_accepts_reduced_cost_with_two_targets').test_actual_curtains_call_advertises_and_accepts_reduced_cost_with_two_targets()

    def test_native_generic_floor_repeated_instances_and_kicker_total_preserve_private_draws(self):
        session = self.session(70212504)
        kicked = self.add(session.engine, 'Generic Undaunted Kicker', zone='hand')
        repeated = self.add(session.engine, 'Generic Repeated Undaunted', zone='hand')
        self.prepare(session, mana={'C':4, 'U':2})
        self.checkpoint(session)
        for card in (kicked, repeated):
            self.assert_closed(session, card)
        initial = {seat:len(player.zones['hand']) for seat, player in session.state.players.items()}
        options = {row['id']:row['requirements'] for row in self.offered(session, kicked)['cost_options']}
        self.assertEqual({'U':1}, {k:v for k,v in options['normal'].items() if v})
        self.assertEqual({'GENERIC':2, 'U':1}, {k:v for k,v in options['kicked'].items() if v})
        self.assertEqual({'GENERIC':1, 'U':1}, {k:v for k,v in self.cost(session, repeated).items() if v})
        for card, option in ((kicked, 'kicked'), (repeated, 'normal')):
            accepted = session.act('pilot:A', {'action_id':'cast:' + session.state.cards[card.object_id].ref,
                'cost_option':option, 'pay':'auto'})
            self.assertTrue(accepted.ok, accepted.summary)
            session = self.replay(session, load=True)
            before = set(session.state.players['A'].zones['hand'])
            self.resolve(session)
            drawn, = set(session.state.players['A'].zones['hand']) - before
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
            for viewer in 'BCD':
                self.assertNotIn(session.state.cards[drawn].ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.assertEqual(initial, {seat:len(player.zones['hand']) for seat, player in session.state.players.items()})
        self.assertEqual(1, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session, load=True)

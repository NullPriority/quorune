from __future__ import annotations

"""CR 702.172/601.2: selected Spree mode costs join the total casting cost."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node, load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


OWNER = 'casting.additional_cost.spree_fixed_modes'
TEXT = 'Spree\n+ {1} \u2014 Draw a card.\n+ {2}{B} \u2014 You gain 3 life.'


class SpreeModeCostCompilerTests(unittest.TestCase):
    def compile(self, text=TEXT):
        return compile_oracle_card(replace(record(text, 'Sorcery'), keywords=('Spree',), mana_cost='{W}', mana_value=1),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')

    def test_fixed_mode_costs_close_with_original_spans_and_independent_modal_bodies(self):
        for text in (TEXT, TEXT.replace('{2}{B}', '{0}'), TEXT.replace('Draw a card.', 'Destroy target artifact.')):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertIn(OWNER, node.capability_dependencies)
                self.assertEqual(['mode_1', 'mode_2'], list(node.target_schema['modes']))
                self.assertEqual(1, node.target_schema['min_modes'])
                self.assertEqual(2, node.target_schema['max_modes'])
                self.assertEqual(1, node.cost['spree']['schema_version'])
                self.assertEqual(mana_cost_to_vector('{1}')[0], node.cost['spree']['mode_costs']['mode_1'])

    def test_selected_costs_targets_and_free_base_options_remain_correlated(self):
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        from quorune.rules.casting.spree_costs import with_spree_mode_cost_options
        node = ir.faces[0].nodes[0]
        program = SimpleNamespace(cost_schema=node.cost, target_schema=node.target_schema, coverage=node.mechanics)
        for base_cost, expected in (('{W}', '{3}{W}{B}'), ('{0}', '{3}{B}')):
            base = {'id': 'normal', 'kind': 'normal', 'requirements': mana_cost_to_vector(base_cost)[0]}
            options = with_spree_mode_cost_options(program, node.cost, [base], {'modes': ['mode_2', 'mode_1']}, hint=False)
            self.assertEqual(1, len(options))
            self.assertEqual(mana_cost_to_vector(expected)[0], options[0]['requirements'])
            self.assertEqual(['mode_1', 'mode_2'], options[0]['spree_modes'])
            self.assertEqual(2, options[0]['target_schema']['min_modes'])
            self.assertEqual(['mode_1', 'mode_2'], list(options[0]['target_schema']['modes']))
            self.assertEqual(mana_cost_to_vector(base_cost)[0], base['requirements'])
        base = {'id': 'normal', 'kind': 'normal', 'requirements': mana_cost_to_vector('{W}')[0]}
        self.assertEqual(3, len(with_spree_mode_cost_options(program, node.cost, [base], {}, hint=True)))
        for modes in ([], ['mode_1', 'mode_1'], ['unknown']):
            self.assertEqual([], with_spree_mode_cost_options(program, node.cost, [base], {'modes': modes}, hint=False))
        single, = with_spree_mode_cost_options(program, node.cost, [base], {'modes': ['mode_2']}, hint=False)
        self.assertEqual(['mode_2'], list(single['target_schema']['modes']))

    def test_unknown_prices_bodies_and_malformed_mode_cost_ownership_fail_closed(self):
        for text in (TEXT.replace('{1}', '{X}'), TEXT.replace('{1}', '{W/U}'),
                     TEXT.replace('Draw a card.', 'Rewrite history.'), TEXT + '\nDraw a card.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        for change in ({'mode_1': {'GENERIC': True}}, {'unknown': mana_cost_to_vector('{1}')[0]}):
            cost = deepcopy(node.cost)
            cost['spree']['mode_costs'].update(change)
            self.assertFalse(capability_dependencies_for_node(effects=node.effects, target_schema=node.target_schema,
                mechanic_ids=node.mechanics, cost_schema=cost))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'choice.modal.fixed_nonrepeating'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Spree owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(replace(record(TEXT, 'Sorcery'), keywords=('Spree',)),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)


class SpreeModeCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'spree-mode-costs.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/spree-mode-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Spree witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def reject(self, session, action, actor='A', **response):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, {'action_id': action, **response}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_raid_selected_modes_charge_sum_and_bind_only_selected_targets(self):
        session = self.session(416000)
        source = self.add(session.engine, 'Requisition Raid', zone='hand', ref='spree-original-raid')
        artifact = self.add(session.engine, 'Generic Spree Artifact', seat='B', ref='spree-artifact-target')
        enchantment = self.add(session.engine, 'Generic Spree Enchantment', seat='C', ref='spree-enchantment-target')
        untouched = self.add(session.engine, 'Generic Bound Body', seat='D', ref='spree-unselected-recipient')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='spree-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'W': 1})
        self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                       if row['id'] == 'cast:' + source.ref)
        targets = [artifact.ref, enchantment.ref]
        for modes, selected in (([], []), (['mode_1', 'mode_1'], targets), (['mode_1'], targets), (['unknown'], [])):
            self.reject(session, offered['id'], modes=modes, targets=selected, pay='auto')
        self.reject(session, offered['id'], 'D', modes=['mode_1', 'mode_2'], targets=targets, pay='auto')
        cheap = next(row['id'] for row in offered['cost_options'] if row['target_schema']['legal_modes'] == ['mode_1'])
        self.reject(session, offered['id'], modes=['mode_1', 'mode_2'], targets=targets, cost_option=cheap, pay='auto')
        self.reject(session, offered['id'], modes=['mode_1', 'mode_2'], targets=targets, pay='manual', payment={'W': 1})
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'modes': ['mode_2', 'mode_1'],
            'targets': targets, 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(['mode_1', 'mode_2'], session.state.stack[-1].modes)
        self.assertEqual('{W}', session.engine._effective_card_data(session.state.cards[source.object_id])['mana_cost'])
        session = self.replay(session, load=True)
        self.resolve(session)
        for card in (artifact, enchantment, source):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.assertEqual({}, session.state.cards[untouched.object_id].counters)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_electromancer_reduces_the_total_after_spree_additional_costs(self):
        session = self.session(416010)
        source = self.add(session.engine, 'Requisition Raid', zone='hand', ref='spree-reduced-raid')
        reducer = self.add(session.engine, 'Goblin Electromancer', ref='spree-actual-reducer')
        artifact = self.add(session.engine, 'Generic Spree Artifact', seat='B', ref='spree-reduced-artifact')
        enchantment = self.add(session.engine, 'Generic Spree Enchantment', seat='C', ref='spree-reduced-enchantment')
        for card in (source, reducer):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                       if row['id'] == 'cast:' + source.ref)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'modes': ['mode_1', 'mode_2'],
            'targets': [artifact.ref, enchantment.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.resolve(session)
        for card in (artifact, enchantment):
            self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        self.replay(session, load=True)

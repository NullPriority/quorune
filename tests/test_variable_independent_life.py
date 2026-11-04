from __future__ import annotations

"""CR 107.3i/119/608.2h: a printed gain of X stays independent of losses."""

from dataclasses import replace
import unittest

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_declared_effect_amounts import generic_spell


class VariableIndependentLifeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_announced_public_and_source_scalar_amounts_reuse_independent_gain(self):
        cards = (
            generic_spell('Each opponent loses X life and you gain X life.'),
            generic_spell('Target opponent loses X life and you gain X life.'),
            generic_spell('Each opponent loses X life and you gain X life, where X is the number of artifacts you control.', mana_cost='{2}{B}'),
            replace(generic_spell('When this creature dies, each opponent loses X life and you gain X life, where X is its power.', mana_cost='{2}{B}'),
                type_line='Creature — Faerie', power='2', toughness='2'),
            replace(generic_spell('When this creature enters, target opponent loses X life and you gain X life, where X is the number of +1/+1 counters on it.', mana_cost='{2}{B}'),
                type_line='Creature — Beast', power='2', toughness='2'),
        )
        for card in cards:
            with self.subTest(text=card.oracle_text):
                ir = compile_oracle_card(card, capability_registry=self.registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effect = ir.faces[0].nodes[0].effects[0]
                self.assertIn(effect['op'], {'drain_each_opponent', 'drain_opponent'})
                self.assertIsInstance(effect['amount'], dict)
                self.assertIn('quantity_expression.declared_effect_amount', ir.faces[0].nodes[0].capability_dependencies)

    def test_unknown_correlations_and_quantities_do_not_borrow_a_valid_amount(self):
        for text in (
            'Each opponent loses X life and you gain X life, where X is the number of creatures in your party.',
            'Each opponent loses X life and you gain X life, where X is that player\'s life total.',
            'Target opponent loses X life and you gain X life. Rewrite history.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(generic_spell(text, mana_cost='{2}{B}'),
                    capability_registry=self.registry).status)

    def test_fixed_independent_drain_encoding_is_unchanged(self):
        for text, effect in (
            ('Each opponent loses 2 life and you gain 2 life.', {'op': 'drain_each_opponent', 'amount': 2}),
            ('Target opponent loses 2 life and you gain 2 life.', {'op': 'drain_opponent', 'target': '$target.0', 'amount': 2}),
        ):
            ir = compile_oracle_card(generic_spell(text), capability_registry=self.registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertEqual(effect, ir.faces[0].nodes[0].effects[0])

    def test_independent_amount_slot_omission_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler.declared_effect_amounts import _RESULT_FIELDS
        fields = {k: v for k, v in _RESULT_FIELDS.items() if k not in {'drain_opponent', 'drain_each_opponent'}}
        with patch.dict('quorune.compiler.declared_effect_amounts._RESULT_FIELDS', fields, clear=True):
            result = unittest.TestResult()
            VariableIndependentLifeCompilerTests(
                'test_announced_public_and_source_scalar_amounts_reuse_independent_gain').run(result)
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(result.failures)

    def test_verified_quantity_withdrawals_preserve_fixed_independent_drain(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        original = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for owner in ('quantity_expression.declared_effect_amount', 'quantity_expression.public_query_effect_amount'):
            raw = json.loads(json.dumps(original))
            next(c for c in raw['capabilities'] if c['id'] == owner).update(
                status='blocked', blockers=['Independent result quantity owner withdrawn'])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            variable = generic_spell('Each opponent loses X life and you gain X life, where X is the number of artifacts you control.', mana_cost='{2}{B}')
            self.assertNotEqual('exact', compile_oracle_card(variable, capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(generic_spell('Each opponent loses 2 life and you gain 2 life.'),
                capability_registry=registry).status)


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class VariableIndependentLifeNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'independent-life.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/variable-independent-life-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Independent life', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 14)], ['Isamaru, Hound of Konda'])

    def reject(self, s, seat, command):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def test_actual_bladecrafter_uses_death_power_and_locked_controller_for_one_gain(self):
        s = self.session(119700710)
        source = self.add(s.engine, 'Faerie Bladecrafter')
        theft = self.add(s.engine, 'Act of Treason', seat='B', zone='hand')
        orrery = self.add(s.engine, 'Vedalken Orrery', seat='B')
        growth = self.add(s.engine, 'Giant Growth', zone='hand')
        kill = self.add(s.engine, 'Murder', seat='B', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, theft, orrery, growth, kill):
            self.assert_closed(s, card)
        s.state.players['B'].mana_pool.update({'R': 1, 'B': 2, 'C': 3})
        self.prepare(s, mana={'G': 1})
        self.checkpoint(s)
        self.cast(s, 'B', theft, [source.ref])
        self.resolve_top(s)
        self.cast(s, 'A', growth, [source.ref])
        self.resolve_top(s)
        self.assertEqual('B', s.state.cards[source.object_id].controller)
        self.cast(s, 'B', kill, [source.ref])
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((35, 45, 35, 35), tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual('A', s.state.cards[source.object_id].owner)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:'+viewer, full=True)))
        self.replay(s, load=True)

    def test_actual_shimmercreep_reads_current_distinct_own_public_colors(self):
        s = self.session(119700720)
        spell = self.add(s.engine, 'Shimmercreep', zone='hand')
        green = self.add(s.engine, 'Grizzly Bears')
        white = self.add(s.engine, 'White Knight')
        other_green = self.add(s.engine, 'Grizzly Bears', seat='B', ref='other-green')
        private_blue = self.add(s.engine, 'Counterspell', zone='hand')
        kill = self.add(s.engine, 'Murder', seat='B', zone='hand')
        for card in (spell, green, white, other_green, kill):
            self.assert_closed(s, card)
        s.state.players['B'].mana_pool.update({'B': 2, 'C': 1})
        self.prepare(s, mana={'B': 1, 'C': 4})
        self.checkpoint(s)
        self.cast(s, 'A', spell, [])
        self.resolve_top(s)
        self.cast(s, 'B', kill, [green.ref])
        self.resolve_top(s)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((42, 38, 38, 38), tuple(s.state.players[p].life for p in 'ABCD'))
        for viewer in 'BCD':
            self.assertNotIn(private_blue.ref, json.dumps(s.packet('pilot:'+viewer, full=True)))
        self.replay(s, load=True)

    def test_actual_fell_beast_devours_before_targeting_and_uses_its_counter_amount(self):
        s = self.session(119700725)
        spell = self.add(s.engine, 'Fell Beast of Mordor', zone='hand')
        bodies = [self.add(s.engine, 'Memnite', ref='devoured-'+str(i)) for i in range(2)]
        pride = self.add(s.engine, "Ajani's Pridemate")
        self.assert_closed(s, spell)
        self.prepare(s, mana={'B': 2, 'C': 2})
        self.checkpoint(s)
        self.cast(s, 'A', spell, [])
        decision = self.resolve(s)
        self.assertEqual('replacement.order', decision.kind)
        s = self.replay(s, load=True)
        ctx = s.packet('pilot:A', full=True)['decision']['ctx']
        option = next(row for row in ctx['options']
            if 'replacement.zone.devour-entry.v1:' in row['id'] and not row['decline'])
        command = {'action_id': 'choose', 'replacement': option['id'],
            'replacement_objects': [body.ref for body in bodies]}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.resolve(s)
        self.target(s, 'A', ['B'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((42, 38, 40, 40), tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual(2, s.state.cards[spell.object_id].counters.get('+1/+1', 0))
        self.assertEqual(1, s.state.cards[pride.object_id].counters.get('+1/+1', 0))
        self.assertTrue(all(s.state.cards[body.object_id].zone == 'graveyard' for body in bodies))
        self.replay(s, load=True)

    def test_actual_rotcaller_counts_a_union_once_and_a_returned_source_is_another_object(self):
        s = self.session(119700730)
        source = self.add(s.engine, 'Valley Rotcaller')
        own = [self.add(s.engine, name) for name in ('Changeling Outcast', 'Squirrel Sovereign', 'Typhoid Rats')]
        other = self.add(s.engine, 'Typhoid Rats', seat='B', ref='other-rat')
        private = self.add(s.engine, 'Faerie Bladecrafter', zone='hand', ref='private-faerie')
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        land = self.add(s.engine, 'Generic Bound Plains', ref='combat-white-source')
        for card in (source, *own, other, blink):
            self.assert_closed(s, card)
        self.prepare(s, mana={'W': 1})
        self.checkpoint(s)
        original = source.logical_object_id
        self.attack(s, [(source, 'B')])
        self.priority(s, 'A')
        offered = [row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if land.ref in row['id']]
        self.assertEqual(1, len(offered), offered)
        payment = {'action_id': offered[0]['id'], 'pay': 'auto'}
        self.reject(s, 'B', payment)
        accepted = s.act('pilot:A', payment)
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(s, 'A', blink, [source.ref])
        self.resolve_top(s)
        self.assertNotEqual(original, s.state.cards[source.object_id].logical_object_id)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((44, 36, 36, 36), tuple(s.state.players[p].life for p in 'ABCD'))
        for viewer in 'BCD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:'+viewer, full=True)))
        self.replay(s, load=True)

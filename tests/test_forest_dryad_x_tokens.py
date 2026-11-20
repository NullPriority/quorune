"""CR107.3/111/302.6/305.6: announced X makes real Forest land creatures."""
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_commander_cast_quantities as counts
import test_target_recipient_results as recipients


class ForestDryadXTests(InheritedNativeFixture, unittest.TestCase):
    tokens = counts.CommanderCastQuantityNativeTests.tokens
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'forest-dryad-x.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/commander-cast-quantity-cards.json',
            ROOT/'tests/fixtures/forest-dryad-x-token-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Forest Dryad X', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def cast_x(self, s, source, x, *, reserve=None):
        self.priority(s, 'A'); offer = self.action(s, 'A', s.state.cards[source.object_id])
        command = {'action_id': offer['id'], 'x': x, 'pay': 'auto', 'targets': [], 'reserve': reserve or {}}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def test_original_has_complete_normal_whole_card_admission(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        card = self.db.lookup('Awaken the Woods', fuzzy=False); semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, [card], trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        spell = next(p for p in semantics.programs_for_oracle(card.oracle_id)
            if p.active_zone == 'stack' and p.event == 'resolve')
        for missing in ('token.creation.fixed_definition', 'mana.intrinsic.basic_land_type',
                'quantity_expression.declared_effect_amount'):
            self.assertIn(missing, spell.capability_dependencies)
            dependencies = tuple(c for c in spell.capability_dependencies if c != missing)
            changed = replace(spell, capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            check = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(check['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + missing, check['blockers'])

    def test_actual_zero_and_two_land_creatures_keep_costs_sickness_haste_and_intrinsic_mana(self):
        for x in (0, 2):
            with self.subTest(x=x):
                s = self.session(691000+x); source = self.add(s.engine, 'Awaken the Woods', zone='hand')
                haste = self.add(s.engine, 'Fervor', zone='hand')
                private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='forest-x-private')
                self.prepare(s, mana={'C': x+2, 'G': 2, 'R': 1}); self.checkpoint(s)
                self.priority(s, 'A'); offer = self.action(s, 'A', source)
                for bad in (-1, True, 30):
                    self.reject(s, 'A', {'action_id': offer['id'], 'x': bad, 'targets': [], 'pay': 'auto'})
                self.cast_x(s, source, x, reserve={'C': 2, 'R': 1}); self.assertEqual(x, s.state.stack[-1].x_value)
                self.assertIsNone(self.resolve(s)); tokens = self.tokens(s, subtype='dryad'); self.assertEqual(x, len(tokens))
                for token in tokens:
                    data = s.engine._effective_card_data(token); types, subtypes, supers = s.engine._type_parts(data['type_line'])
                    self.assertTrue({'land', 'creature'} <= types); self.assertIn('forest', subtypes); self.assertNotIn('basic', supers)
                    self.assertEqual(['G'], data['colors']); self.assertEqual('Forest Dryad Token', data['name'])
                    self.assertEqual((1, 1), tuple(s.engine._numeric_stat(token.object_id, k) for k in ('power', 'toughness')))
                    self.assertIn('intrinsic_forest', [a.ability_id for a in s.engine._activated_abilities(token)])
                    self.reject(s, 'A', {'action_id': 'activate:'+token.ref+':intrinsic_forest'})
                if tokens:
                    self.cast(s, 'A', haste); self.assertIsNone(self.resolve(s))
                    action = 'activate:'+s.state.cards[tokens[0].object_id].ref+':intrinsic_forest'
                    self.assertTrue(any(a['id'] == action for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
                    self.reject(s, 'B', {'action_id': action}); green = s.state.players['A'].mana_pool.get('G', 0)
                    result = s.act('pilot:A', {'action_id': action}); self.assertTrue(result.ok, result.summary)
                    self.assertEqual(green+1, s.state.players['A'].mana_pool.get('G', 0))
                self.finish(s, private)

    def test_actual_countered_x_spell_creates_no_tokens_or_mana_refund(self):
        s = self.session(691010); source = self.add(s.engine, 'Awaken the Woods', zone='hand')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        for i in range(2): self.add(s.engine, 'Island', seat='B', ref='forest-counter-blue-'+str(i))
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='forest-counter-private')
        self.prepare(s, mana={'C': 2, 'G': 2}); self.checkpoint(s); self.cast_x(s, source, 2)
        original = s.state.stack[-1]; self.cast(s, 'B', counter, [original.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual([], self.tokens(s)); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.finish(s, private)

    def test_actual_copy_retains_announced_x_without_another_cast(self):
        s = self.session(691020); source = self.add(s.engine, 'Awaken the Woods', zone='hand')
        copier = self.add(s.engine, 'Fury Storm', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='forest-copy-private')
        self.prepare(s, mana={'C': 4, 'G': 2, 'R': 2}); self.checkpoint(s)
        self.cast_x(s, source, 2, reserve={'C': 2, 'R': 2}); original = s.state.stack[-1]
        self.cast(s, 'A', copier, [original.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(4, len(self.tokens(s, subtype='dryad')))
        self.assertEqual(2, len(s.engine._current_turn_history('spell_cast'))); self.finish(s, private)

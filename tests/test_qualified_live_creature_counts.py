"""Typed public creature counts at instruction resolution."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_public_history_queries as history_controls
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.granted_program_bounds import granted_effect_values_are_closed
from quorune.compiler.live_battlefield_quantities import resolution_quantity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.live_battlefield_quantities import current_live_battlefield_count
from quorune.live_battlefield_quantity_model import LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY, LiveBattlefieldQuantitySpec
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class QualifiedLiveCreatureCountTests(unittest.TestCase):
    def test_qualified_live_count_uses_effective_creatures_and_kills_membership_mutant(self):
        def candidate(ref, power, *, controller='A', phased_out=False):
            row = ObjectQueryResult(object_id=ref, ref=ref, printed_name='Creature', owner=controller,
                                    controller=controller, zone='battlefield', types=('creature',),
                                    effective_power=power, effective_toughness=3, phased_out=phased_out)
            return SimpleNamespace(row=row, zone='battlefield', controller=controller, phased_out=phased_out)

        high = candidate('high', 4)
        rows = (high, candidate('low', 3), candidate('opponent', 8, controller='B'),
                candidate('phased', 8, phased_out=True))

        def current(card, *, _maximum_layer):
            self.assertIsNone(_maximum_layer)
            return card.row

        host = SimpleNamespace(state=SimpleNamespace(players={'A': None, 'B': None},
                                                     cards={card.row.ref: card for card in rows}),
                               _public_object_query_result=current)
        spec = resolution_quantity('creature you control with power 4 or greater', source_name='Source')
        self.assertEqual(2, spec.schema_version)
        self.assertEqual(1, current_live_battlefield_count(host, 'A', spec))
        with patch('quorune.live_battlefield_quantities.object_matches_query', return_value=True):
            with self.assertRaises(AssertionError):
                self.assertEqual(1, current_live_battlefield_count(host, 'A', spec))
        high.row = replace(high.row, effective_power=1)
        self.assertEqual(0, current_live_battlefield_count(host, 'A', spec))
        high.row = replace(high.row, effective_power=None)
        with self.assertRaisesRegex(ValueError, 'unavailable effective characteristics'):
            current_live_battlefield_count(host, 'A', spec)

    def test_qualified_live_count_codec_and_granted_binding_are_closed(self):
        for phrase in ('creature you control with power 4 or greater', 'modified creature you control',
                       'creature you control with a counter on it'):
            with self.subTest(phrase=phrase):
                spec = resolution_quantity(phrase, source_name='Source')
                self.assertEqual(2, spec.schema_version)
                raw = spec.to_dict()
                self.assertEqual(spec, LiveBattlefieldQuantitySpec.from_dict(raw))
                for change in ({'schema_version': 1}, {'schema_version': True},
                               {'controller_relation': 'source_opponents'}, {'hidden_ref': 'anything'}):
                    with self.assertRaises(ValueError):
                        LiveBattlefieldQuantitySpec.from_dict({**raw, **change})
                amount = PublicQueryAmountSpec(spec).to_dict()
                effect = {'op': 'draw', 'player': '$controller', 'count': amount, 'private': True}
                self.assertTrue(granted_effect_values_are_closed((effect,)))
                self.assertFalse(granted_effect_values_are_closed(({**effect, 'count': {
                    **amount, 'schema_version': 2, 'binding_id': 'declared-instruction',
                }},)))
                self.assertFalse(granted_effect_values_are_closed(({**effect, 'count': '$x'},)))
        leading = resolution_quantity('the number of other modified creatures you control',
                                      source_name='Source', complete_value=True)
        suffix = resolution_quantity('the number of modified creatures you control other than this creature',
                                     source_name='Source', complete_value=True)
        self.assertEqual(leading, suffix)
        self.assertTrue(suffix.exclude_source)
        self.assertIsNone(resolution_quantity('the number of modified creatures you control other than that creature',
                                             source_name='Source', complete_value=True))

    def test_qualified_draw_keeps_source_spans_and_requires_its_capability(self):
        registry = load_default_capability_registry()
        text = 'Draw a card for each creature you control with power 4 or greater.'
        ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                                 capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn(LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY, node.capability_dependencies)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(item for item in raw['capabilities'] if item['id'] == LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY)
        row.update(status='blocked', blockers=['Qualified live count capability mutation'])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        blocked = compile_oracle_card(record(text, 'Sorcery'), capability_registry=altered,
                                      capability_profile='commander_review')
        self.assertNotEqual('exact', blocked.status)
        self.assertTrue(any(LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))

    def test_mixed_ordered_amounts_require_both_the_declared_and_independent_instructions(self):
        from quorune.compiler.declared_effect_amounts import declared_amount_shape_context

        text = ('Draw a card for each creature you control with power 4 or greater. Then creatures you control '
                'get +X/+X until end of turn, where X is the number of cards in your hand.')
        ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=load_default_capability_registry(),
                                 capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        mechanics = set(node.mechanics)
        self.assertEqual([1, 2], [node.effects[0]['count']['schema_version'],
                                  node.effects[1]['power']['schema_version']])
        self.assertIsNotNone(declared_amount_shape_context(node.effects, mechanics))
        self.assertIsNone(declared_amount_shape_context(node.effects[:1], mechanics))
        self.assertIsNone(declared_amount_shape_context(node.effects, mechanics - {'closed-effect-program'}))
        self.assertIsNone(declared_amount_shape_context(node.effects, mechanics - {'public-query-effect-amount'}))


class QualifiedLiveCreatureCountActionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / 'qualified-live-creatures.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/conditioned-multi-event-cards.json',
            ROOT / 'tests/fixtures/creature-power-multiple-cards.json',
            ROOT / 'tests/fixtures/qualified-live-creature-count-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified live creature counts', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def test_original_goliath_draw_counts_response_and_earlier_counters(self):
        session = self.session(608_208_221)
        source = self.add(session.engine, 'Goliath, Mass Manipulator')
        bear = self.add(session.engine, 'Grizzly Bears', ref='grown-bear')
        self.add(session.engine, 'Grizzly Bears', ref='ungrown-bear')
        self.add(session.engine, 'Serra Angel', seat='C')
        response = self.add(session.engine, 'Giant Growth', seat='B', zone='hand')
        self.add(session.engine, 'Forest', seat='B')
        self.assert_closed(session, source)
        self.assert_closed(session, response)
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                       if LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY in program.capability_dependencies)
        dependencies = tuple(value for value in program.capability_dependencies
                             if value != LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY)
        altered = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY,
                      binding['blockers'])
        self.prepare(session, mana={'C': 4, 'G': 1})
        self.checkpoint(session)
        before = set(session.state.players['A'].zones['hand'])
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if row['id'].startswith(f'activate:{source.ref}:'))
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.cast_for(session, 'B', response, [bear.ref])
        self.resolve_top(session)
        self.assertEqual(2, session.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(5, session.engine._numeric_stat(bear.object_id, 'power'))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[source.object_id].counters['+1/+1'])
        drawn = set(session.state.players['A'].zones['hand']) - before
        self.assertEqual(2, len(drawn))
        for viewer in 'BCD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            for oid in drawn:
                self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session)

    def test_original_gamma_draw_counts_any_counter_after_its_power_up(self):
        session = self.session(608_208_228)
        source = self.add(session.engine, 'Gamma Grotesque')
        countered = self.add(session.engine, 'Grizzly Bears', ref='charge-bearing-creature')
        countered.counters['charge'] = 1
        self.add(session.engine, 'Grizzly Bears', ref='creature-without-counters')
        opponent = self.add(session.engine, 'Grizzly Bears', seat='C', ref='opposing-countered-creature')
        opponent.counters['+1/+1'] = 1
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'G': 2})
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if row['id'].startswith(f'activate:{source.ref}:'))
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(before + 2, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_original_carapace_draw_rechecks_modification_after_equipment_departs(self):
        session = self.session(700_009_221)
        equip = self.add(session.engine, 'Biorganic Carapace')
        attacker = self.add(session.engine, 'Generic Bound Body', ref='equipped-attacker')
        attacker.acquired_control_turn_count = -1
        countered = self.add(session.engine, 'Generic Bound Body', ref='counter-modified')
        countered.counters['charge'] = 1
        aura_recipient = self.add(session.engine, 'Generic Bound Body', ref='opposing-aura-only')
        aura = self.add(session.engine, 'Pacifism', seat='B')
        response = self.add(session.engine, 'Disenchant', seat='B', zone='hand')
        self.add(session.engine, 'Generic Bound Plains', seat='B', ref='disenchant-white')
        self.add(session.engine, 'Generic Bound Plains', seat='B', ref='disenchant-generic')
        for source, recipient in ((equip, attacker), (aura, aura_recipient)):
            attach_objects(session.state.cards, source, recipient, source_timestamp=source.zone_timestamp,
                           players=session.state.players)
            self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])

        self.attack(session, [(attacker, 'B')])
        self.until(session, lambda s: self.trigger_count(s, attacker) == 1)
        self.cast_for(session, 'B', response, [equip.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[equip.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, attacker))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_original_pygmy_kavu_uses_opposing_color_and_accepts_zero(self):
        for expected in (1, 0):
            with self.subTest(expected=expected):
                session = self.session(608_208_230 + expected)
                source = self.add(session.engine, 'Pygmy Kavu', zone='hand')
                self.add(session.engine, 'Vampire Nighthawk', ref='controller-black-creature')
                self.add(session.engine, 'Serra Angel', seat='B')
                if expected:
                    self.add(session.engine, 'Vampire Nighthawk', seat='D', ref='opposing-black-creature')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 3, 'G': 1})
                self.checkpoint(session)
                before = len(session.state.players['A'].zones['hand'])
                self.cast(session, 'A', source)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(before - 1 + expected, len(session.state.players['A'].zones['hand']))
                self.replay(session)

    def test_original_arabella_shares_one_live_amount_across_damage_and_life_gain(self):
        session = self.session(699_120_301)
        source = self.add(session.engine, 'Arabella, Abandoned Doll')
        source.acquired_control_turn_count = -1
        bear = self.add(session.engine, 'Grizzly Bears')
        self.add(session.engine, 'Hill Giant')
        self.add(session.engine, 'Grizzly Bears', seat='C', ref='opposing-bear')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        shield = self.add(session.engine, 'Healing Salve', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        self.add(session.engine, 'Generic Bound Plains', seat='B')
        self.prepare(session)
        self.checkpoint(session)
        for card in (source, bounce, shield):
            self.assert_closed(session, card)
        private = session.state.cards[session.state.players['D'].zones['hand'][0]].ref

        self.attack(session, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast_for(session, 'B', bounce, [bear.ref])
        self.resolve_top(session)
        self.priority(session, 'B')
        action = self.action(session, 'B', shield)
        command = {'action_id': action['id'], 'modes': ['mode_2'], 'targets': ['B'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertEqual([40, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])

        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[bear.object_id].zone)
        self.assertEqual([41, 40, 39, 39], [session.state.players[seat].life for seat in 'ABCD'])
        batches = [event.details for event in session.state.events
                   if event.code == 'effect.damage' and 'damage_events' in event.details]
        self.assertEqual(1, len(batches))
        self.assertEqual(1, batches[0]['assigned_amount'])
        self.assertEqual([('B', 0), ('C', 1), ('D', 1)],
                         [(event['target'], event['amount']) for event in batches[0]['damage_events']])
        for seat in 'ABC':
            self.assertNotIn(private, json.dumps(session.packet('pilot:' + seat, full=True)))
        self.replay(session)

    def test_original_raiju_counts_after_the_counter_and_excludes_its_modified_source(self):
        session = self.session(699_120_302)
        source = self.add(session.engine, 'Thundering Raiju')
        source.counters['charge'] = 1
        target = self.add(session.engine, 'Grizzly Bears')
        removed = self.add(session.engine, 'Hill Giant')
        removed.counters['charge'] = 1
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)

        self.attack(session, [(source, 'B')])
        self.target(session, 'A', [target.ref])
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast_for(session, 'B', bounce, [removed.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[removed.object_id].zone)
        self.assertEqual(0, session.state.cards[target.object_id].counters.get('+1/+1', 0))

        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[target.object_id].counters['+1/+1'])
        self.assertEqual([40, 39, 39, 39], [session.state.players[seat].life for seat in 'ABCD'])
        self.replay(session)

    def test_original_gau_uses_last_effective_power_after_the_source_leaves(self):
        for left_graveyard in (False, True):
            with self.subTest(left_graveyard=left_graveyard):
                session = self.session(699_120_303 + left_graveyard)
                source = self.add(session.engine, 'Gau, Feral Youth')
                flashback = self.add(session.engine, 'Think Twice', zone='graveyard')
                growth = self.add(session.engine, 'Giant Growth', zone='hand')
                bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                self.add(session.engine, 'Island', seat='B')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'U': 1, 'G': 1})
                self.checkpoint(session)
                self.cast_for(session, 'A', growth, [source.ref])
                self.resolve_top(session)
                power = session.engine._numeric_stat(source.object_id, 'power')
                self.assertGreater(power, int(self.db.by_oracle_id(source.oracle_id).power))
                if left_graveyard:
                    self.priority(session, 'A')
                    action = self.action(session, 'A', flashback)
                    result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'flashback', 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                    self.resolve_top(session)
                    self.assertEqual('exile', session.state.cards[flashback.object_id].zone)

                history_controls.PublicHistoryQueryNativeTests.end_step(self, session)
                self.assertEqual(int(left_graveyard), self.trigger_count(session, source))
                if left_graveyard:
                    self.cast_for(session, 'B', bounce, [source.ref])
                    self.resolve_top(session)
                    self.assertEqual('hand', session.state.cards[source.object_id].zone)
                    session = self.replay(session, load=True)
                    self.assertIsNone(self.resolve(session))
                self.assertEqual([40] + [40 - power * left_graveyard] * 3,
                                 [session.state.players[seat].life for seat in 'ABCD'])
                self.replay(session)

    def test_original_avalanche_binds_hand_amount_after_the_independent_draw(self):
        session = self.session(608_208_245)
        spell = self.add(session.engine, 'Become the Avalanche', zone='hand')
        high = self.add(session.engine, 'Serra Angel', ref='avalanche-high')
        low = self.add(session.engine, 'Grizzly Bears', ref='avalanche-low')
        opponent = self.add(session.engine, 'Serra Angel', seat='C', ref='avalanche-opponent')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 4, 'G': 2})
        self.checkpoint(session)
        count = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', spell)
        self.assertEqual(count - 1, len(session.state.players['A'].zones['hand']))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(count, len(session.state.players['A'].zones['hand']))
        self.assertEqual(4 + count, session.engine._numeric_stat(high.object_id, 'power'))
        self.assertEqual(2 + count, session.engine._numeric_stat(low.object_id, 'power'))
        self.assertEqual(4, session.engine._numeric_stat(opponent.object_id, 'power'))
        self.replay(session)

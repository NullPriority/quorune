"""Independent fixed instructions continue after a source-self graveyard entry."""
from copy import deepcopy
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database

from quorune.abilities import parse_activated_abilities
from quorune.self_zone_move import SelfZoneMoveError, SelfZoneMoveSpec, compile_self_zone_move
from quorune.self_zone_move_followups import (
    FIELD, CAPABILITY, MECHANIC, SelfEntryFollowup, compiled_self_entry,
    decode_self_entry_followup, self_entry_followup_effect,
)
from quorune.rules.self_zone_move_followups import self_entry_followup_node_capabilities


class SelfGraveyardReturnFollowupModelTests(unittest.TestCase):
    def ability(self, text):
        ability, = parse_activated_abilities(card_name='Generic Return Source', oracle_text=text)
        return ability

    def test_two_complete_followups_preserve_printed_body_and_seal_graveyard_activation(self):
        for text, kind in [
            ('{1}{W}: Return this card from your graveyard to the battlefield tapped. '
             'You skip your next turn.', SelfEntryFollowup.SKIP_NEXT_TURN),
            ('{2}{B}: Return this card from your graveyard to the battlefield tapped. '
             'You lose 1 life. Activate only if an opponent has three or more poison counters.',
             SelfEntryFollowup.LOSE_ONE_LIFE),
        ]:
            with self.subTest(kind=kind):
                original = self.ability(text)
                spec = compile_self_zone_move(original)
                self.assertIsNotNone(spec)
                self.assertEqual(2, spec.schema_version)
                self.assertIs(kind, spec.followup)
                self.assertEqual(('graveyard',), spec.ability.zones)
                self.assertEqual(original.effect_text, spec.ability.effect_text)
                self.assertEqual(original.activation_conditions, spec.ability.activation_conditions)
                self.assertEqual(spec, SelfZoneMoveSpec.from_dict(spec.to_dict()))
                self.assertIs(kind, decode_self_entry_followup(spec.effect()))
                self.assertEqual('$controller', self_entry_followup_effect(kind)['player'])

    def test_legacy_spec_and_effect_keep_their_original_fields(self):
        spec = compile_self_zone_move(self.ability(
            '{1}{B}: Return this card from your graveyard to the battlefield tapped.'))
        self.assertEqual(1, spec.schema_version)
        self.assertNotIn('followup', spec.to_dict())
        self.assertNotIn(FIELD, spec.effect())
        self.assertEqual(spec, SelfZoneMoveSpec.from_dict(spec.to_dict()))

    def test_spec_versions_and_mismatched_or_unprinted_continuations_reject(self):
        spec = compile_self_zone_move(self.ability(
            '{1}{W}: Return this card from your graveyard to the battlefield tapped. '
            'You skip your next turn.'))
        for changes in ({'schema_version': True}, {'schema_version': 2.0},
                        {'schema_version': 1}, {'followup': 'unknown'}, {'followup': None},
                        {'followup': SelfEntryFollowup.LOSE_ONE_LIFE.value}, {'tapped': False}):
            with self.subTest(changes=changes), self.assertRaises((SelfZoneMoveError, ValueError)):
                SelfZoneMoveSpec.from_dict({**spec.to_dict(), **changes})
        with self.assertRaises(SelfZoneMoveError):
            replace(spec, followup=SelfEntryFollowup.LOSE_ONE_LIFE)

    def test_independent_followup_grammar_rejects_linked_optional_or_variable_results(self):
        for result in ('If you do, you lose 1 life.', 'You may lose 1 life.',
                       'You lose X life.', 'You skip your next turn unless you pay {2}.',
                       'It gains haste.', 'Draw a card.'):
            with self.subTest(result=result):
                self.assertIsNone(compile_self_zone_move(self.ability(
                    '{1}{W}: Return this card from your graveyard to the battlefield tapped. '+result)))

    def test_marked_movement_leaf_cannot_change_origin_or_destination(self):
        spec = compile_self_zone_move(self.ability(
            '{1}{W}: Return this card from your graveyard to the battlefield tapped. '
            'You skip your next turn.'))
        for change in ({'origin': 'hand'}, {'destination': 'exile'}, {'tapped': 1},
                       {FIELD: {'version': True, 'kind': 'skip_next_turn'}},
                       {FIELD: {'version': 1, 'kind': 'unknown'}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                decode_self_entry_followup({**deepcopy(spec.effect()), **change})

    def test_ordered_pair_requires_its_marker_and_exact_independent_player_result(self):
        spec = compile_self_zone_move(self.ability(
            '{1}{W}: Return this card from your graveyard to the battlefield tapped. '
            'You skip your next turn.'))
        _, effects, mechanics = compiled_self_entry(spec)
        self.assertIn(CAPABILITY, self_entry_followup_node_capabilities(
            effects=effects, target_schema=None, mechanic_ids=mechanics))
        missing = dict(effects[0])
        missing.pop(FIELD)
        alternatives = [(effects[1], effects[0]), (missing, effects[1]),
                        (effects[0], {**effects[1], 'player': 'B'}),
                        (effects[0], self_entry_followup_effect(SelfEntryFollowup.LOSE_ONE_LIFE)),
                        (effects[0],)]
        for alternative in alternatives:
            with self.subTest(alternative=alternative):
                self.assertEqual((), self_entry_followup_node_capabilities(
                    effects=alternative, target_schema=None, mechanic_ids=(MECHANIC,)))


class SelfGraveyardReturnFollowupNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish
    finish_turn = seat_actions.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'return-followups.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/self-graveyard-return-followups-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source return follow-ups', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def activation(self, session, source):
        self.priority(session, 'A')
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        return next((row for row in rows if row['id'].startswith('activate:'+source.ref+':')), None)

    def activate(self, session, source):
        offered = self.activation(session, source)
        self.assertIsNotNone(offered)
        command = {'action_id': offered['id'], 'targets': [], 'pay': 'auto'}
        self.reject(session, 'B', command)
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def test_both_originals_have_complete_normal_ability_and_source_admission(self):
        session = self.session(609301)
        for name in ('Chronosavant', 'Fleshless Gladiator'):
            with self.subTest(name=name):
                source = self.add(session.engine, name, zone='graveyard')
                self.assert_closed(session, source)
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertEqual('graveyard', ir.faces[0].nodes[0].active_zone)

    def test_chronosavant_enters_tapped_then_skips_its_controllers_next_turn(self):
        session = self.session(609310)
        source = self.add(session.engine, 'Chronosavant', zone='graveyard')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        self.activate(session, source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertTrue(current.tapped)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.finish(session, private)

    def test_exiling_the_activated_source_still_resolves_the_independent_skip(self):
        session = self.session(609320)
        source = self.add(session.engine, 'Chronosavant', zone='graveyard')
        crypt = self.add(session.engine, "Tormod's Crypt", seat='B')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, crypt):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        self.activate(session, source)
        self.priority(session, 'B')
        rows = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in rows if row['id'].startswith('activate:'+crypt.ref+':')]
        result = session.act('pilot:B', {'action_id': offered['id'], 'targets': ['A'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.finish(session, private)

    def test_gladiator_uses_one_opponents_poison_then_loses_life_after_tapped_entry(self):
        session = self.session(609330)
        source = self.add(session.engine, 'Fleshless Gladiator', zone='graveyard')
        spell = self.add(session.engine, 'Prologue to Phyresis', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        session.state.players['A'].poison = 4
        session.state.players['B'].poison = 1
        session.state.players['C'].poison = 2
        for card in (source, spell):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 3, 'B': 1, 'U': 1})
        self.checkpoint(session)
        self.assertIsNone(self.activation(session, source))
        self.cast(session, 'A', spell)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.players['C'].poison)
        before = session.state.players['A'].life
        self.activate(session, source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(before - 1, session.state.players['A'].life)
        self.finish(session, private)

    def test_missing_continuation_is_killed_by_the_complete_original_action(self):
        method = 'test_chronosavant_enters_tapped_then_skips_its_controllers_next_turn'
        from quorune.turn_step_owner import TurnStepOwner
        with patch.object(TurnStepOwner, 'schedule_skip', return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()

    def test_payload_minimum_rejects_stripped_capability_and_missing_movement_marker(self):
        from quorune.rules.self_zone_move_followups import self_entry_followup_runtime_dependencies
        session = self.session(609350)
        source = self.add(session.engine, 'Chronosavant', zone='graveyard')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                     if p.event == 'activate']
        self.assertIn(CAPABILITY, self_entry_followup_runtime_dependencies(program))
        deps = tuple(x for x in program.capability_dependencies if x != CAPABILITY)
        stripped = replace(program, coverage=(), capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry,
                                               profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])
        movement = dict(program.effects[0])
        movement.pop(FIELD)
        malformed = replace(program, coverage=(), effects=(movement, program.effects[1]))
        with self.assertRaises(ValueError):self_entry_followup_runtime_dependencies(malformed)

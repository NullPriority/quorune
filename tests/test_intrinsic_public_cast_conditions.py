"""Intrinsic cast legality reads current public counts or committed deaths."""
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
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiled_cast_timing import compiled_self_cast_restriction_reason
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.continuous_conditions import (
    FixedPublicStateConditionKind, FixedPublicStateFact, FixedPublicStateConditionSpec,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.self_cast_restrictions import SelfCastRestrictionHandler


class IntrinsicPublicCastConditionModelTests(unittest.TestCase):
    def descriptor(self, text, *, version=2):
        condition = fixed_public_state_condition(text, source_name='Generic Restriction')
        self.assertIsNotNone(condition, text)
        handler = SelfCastRestrictionHandler()
        return {'handler_id': handler.handler_id, 'schema_version': 1,
                'event': handler.event, 'source_condition': condition.to_dict(),
                **({'intrinsic_condition_version': 2} if version == 2 else {})}

    def test_current_public_subtype_counts_and_committed_death_have_explicit_new_contract(self):
        handler = SelfCastRestrictionHandler()
        for text in ('you control two or more Doctors', 'you control two or more Vampires',
                     'a creature died this turn'):
            with self.subTest(text=text):
                descriptor = self.descriptor(text)
                condition = handler.validate(descriptor)
                self.assertEqual(descriptor['source_condition'], condition.to_dict())
                self.assertEqual((condition,), handler.lower(descriptor, None))

    def test_legacy_contract_keeps_its_closed_spell_and_snow_land_conditions(self):
        handler = SelfCastRestrictionHandler()
        for text in ("you've cast a spell this turn", 'you control a snow land'):
            with self.subTest(text=text):
                handler.validate(self.descriptor(text, version=1))
        for text in ('you control two or more Doctors', 'a creature died this turn'):
            with self.subTest(text=text), self.assertRaises(SemanticNodeError):
                handler.validate(self.descriptor(text, version=1))

    def test_new_descriptor_versions_and_foreign_or_unbound_counts_fail_closed(self):
        handler = SelfCastRestrictionHandler()
        descriptor = self.descriptor('you control two or more Vampires')
        for version in (True, False, 2.0, None, 0, 3, '2'):
            with self.subTest(version=version), self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, 'intrinsic_condition_version': version})
        condition = fixed_public_state_condition('you control two or more Vampires',
                                                source_name='Generic Restriction')
        for change in ({'owner': 'B'}, {'controller': 'B'}, {'zones': ('graveyard',)},
                       {'include_phased_out': True}, {'exclude_ref': '$source'}):
            with self.subTest(change=change), self.assertRaises(SemanticNodeError):
                altered = deepcopy(descriptor)
                altered['source_condition']['quantity']['query'].update(change)
                handler.validate(altered)

    def test_unrepresented_public_facts_and_composed_boolean_conditions_stay_residual(self):
        handler = SelfCastRestrictionHandler()
        descriptor = self.descriptor('a creature died this turn')
        condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=1, schema_version=4, fact=FixedPublicStateFact.CONTROLLER_ATTACKED_THIS_TURN)
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, 'source_condition': condition.to_dict()})
        count = fixed_public_state_condition('you control two or more Vampires',
                                              source_name='Generic Restriction')
        combined = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ALL,
            schema_version=6, terms=(condition, count))
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, 'source_condition': combined.to_dict()})


ORIGINALS = ('Twice Upon a Time // Unlikely Meeting', 'Feast of Blood', 'Grim Wanderer')


class IntrinsicPublicCastConditionNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish
    finish_turn = seat_actions.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'intrinsic-public-casts.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/intrinsic-public-cast-condition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Intrinsic public casts', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def actions(self, session):
        self.priority(session, 'A')
        return session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']

    def main_rows(self, session, source):
        ref = session.state.cards[source.object_id].ref
        return [row for row in self.actions(session) if row['id'] == 'cast:'+ref
                or row['id'].endswith(':face:Twice Upon a Time')]

    def assert_blocked(self, session, source, targets=()):
        self.assertEqual([], self.main_rows(session, source))
        self.assertEqual('self_cast_condition', compiled_self_cast_restriction_reason(
            session.engine, 'A', session.state.cards[source.object_id]))
        self.reject(session, 'A', {'action_id': 'cast:'+session.state.cards[source.object_id].ref,
                                   'targets': list(targets), 'pay': 'auto'})

    def test_all_three_complete_originals_admit_their_nonempty_restriction_and_siblings(self):
        session = self.session(113601)
        for name in ORIGINALS:
            with self.subTest(name=name):
                source = self.add(session.engine, name, zone='hand')
                self.assert_closed(session, source)
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
                restriction, = [p for p in programs if p.event == 'cast.restriction']
                self.assertTrue(restriction.handlers)
                self.assertEqual(2, restriction.handlers[0]['intrinsic_condition_version'])

    def test_new_compiler_keeps_adjacent_history_and_functional_conditions_material(self):
        base = self.db.lookup('Grim Wanderer')
        for clause in ("Cast this spell only if you've cast another green spell this turn.",
                'Cast this spell only if you control a snow creature.',
                'Cast this spell only if a creature died last turn.',
                'Cast this spell only if an opponent controls two or more Vampires.',
                'Whenever a creature dies — Cast this spell only if a creature died this turn.',
                'Cast this spell only if you control two Doctors, then draw a card.'):
            with self.subTest(clause=clause):
                record = replace(base, oracle_text='Flash\n'+clause)
                self.assertNotEqual('exact', compile_oracle_card(record,
                    capability_registry=self.registry, capability_profile='commander_review').status)

    def test_original_feast_reads_current_controlled_vampires_and_rejects_a_stale_offer(self):
        session = self.session(113610)
        source = self.add(session.engine, 'Feast of Blood', zone='hand')
        first = self.add(session.engine, 'Vampire Neonate', ref='first-vampire')
        second = self.add(session.engine, 'Vampire Neonate', ref='second-vampire')
        foreign = self.add(session.engine, 'Vampire Neonate', seat='B', ref='opposing-vampire')
        foreign.owner = 'A'
        victim = self.add(session.engine, 'Grizzly Bears', seat='B')
        aura = self.add(session.engine, 'Song of the Dryads', zone='hand')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, first, second, foreign, victim, aura, bounce):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 8, 'B': 2, 'G': 1, 'U': 2})
        self.checkpoint(session)
        offered, = self.main_rows(session, source)
        command = {'action_id': offered['id'], 'targets': [victim.ref], 'pay': 'auto'}
        self.reject(session, 'B', command)
        self.cast(session, 'A', aura, [second.ref])
        self.assertIsNone(self.resolve(session))
        self.assert_blocked(session, source, [victim.ref])
        self.cast(session, 'A', bounce, [aura.ref])
        self.assertIsNone(self.resolve(session))
        offered, = self.main_rows(session, source)
        life = session.state.players['A'].life
        result = session.act('pilot:A', {**command, 'action_id': offered['id']})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual(life + 4, session.state.players['A'].life)
        self.finish(session, private)

    def test_twice_counts_present_doctors_and_keeps_cast_legality_after_a_response(self):
        session = self.session(113620)
        source = self.add(session.engine, 'Twice Upon a Time // Unlikely Meeting', zone='hand')
        live = self.add(session.engine, 'Universal Automaton', ref='present-doctor')
        phased = self.add(session.engine, 'Universal Automaton', ref='phased-doctor')
        phased.phased_out = True
        foreign = self.add(session.engine, 'Universal Automaton', seat='B', ref='foreign-doctor')
        arriving = self.add(session.engine, 'Universal Automaton', zone='hand', ref='arriving-doctor')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, live, phased, foreign, arriving, bounce):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 12, 'U': 5})
        self.checkpoint(session)
        self.assert_blocked(session, source)
        self.cast(session, 'A', arriving)
        self.assertIsNone(self.resolve(session))
        offered, = self.main_rows(session, source)
        command = {'action_id': offered['id'], 'targets': [], 'pay': 'auto'}
        self.reject(session, 'B', command)
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'A', bounce, [arriving.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[arriving.object_id].zone)
        self.assertFalse(session.state.extra_turns)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [turn.player for turn in session.state.extra_turns])
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.finish(session, private)

    def test_adventure_face_searches_a_doctor_without_the_front_count_and_later_casts_normally(self):
        session = self.session(113630)
        source = self.add(session.engine, 'Twice Upon a Time // Unlikely Meeting', zone='hand')
        found = self.add(session.engine, 'Universal Automaton', zone='library', ref='searched-doctor')
        second = self.add(session.engine, 'Universal Automaton', zone='hand', ref='second-searched-doctor')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, found, second):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 15, 'U': 5})
        self.checkpoint(session)
        self.assert_blocked(session, source)
        adventure, = [row for row in self.actions(session)
                       if row['id'].endswith(':face:Unlikely Meeting')]
        result = session.act('pilot:A', {'action_id': adventure['id'], 'targets': [], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.search', self.resolve(session).kind)
        schema = session.packet('pilot:A', full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertEqual({found.ref}, set(schema['legal_refs']))
        self.reject(session, 'B', {'action_id': 'choose', 'search_cards': [found.ref]})
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'search_cards': [found.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[found.object_id].zone)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assert_blocked(session, source)
        self.cast(session, 'A', found)
        self.assertIsNone(self.resolve(session))
        self.assert_blocked(session, source)
        self.cast(session, 'A', second)
        self.assertIsNone(self.resolve(session))
        offered, = self.main_rows(session, source)
        result = session.act('pilot:A', {'action_id': offered['id'], 'targets': [], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [turn.player for turn in session.state.extra_turns])
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.finish(session, private)

    def test_original_grim_uses_any_actual_death_this_turn_and_resets_after_the_turn(self):
        session = self.session(113640)
        source = self.add(session.engine, 'Grim Wanderer', zone='hand')
        later = self.add(session.engine, 'Grim Wanderer', zone='hand', ref='later-grim')
        self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='old-graveyard-creature')
        exile_victim = self.add(session.engine, 'Grizzly Bears', seat='B', ref='exile-counterexample')
        dying = self.add(session.engine, 'Grizzly Bears', seat='C', ref='another-players-death')
        exile = self.add(session.engine, 'Swords to Plowshares', zone='hand')
        kill = self.add(session.engine, 'Murder', zone='hand')
        for index in range(2):self.add(session.engine, 'Swamp', ref='later-black-'+str(index))
        self.add(session.engine, 'Generic Bound Plains', ref='later-generic')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, later, exile_victim, dying, exile, kill):self.assert_closed(session, card)
        self.prepare(session, active='B', mana={'C': 8, 'B': 4, 'W': 1})
        self.checkpoint(session)
        self.assert_blocked(session, source)
        self.cast(session, 'A', exile, [exile_victim.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[exile_victim.object_id].zone)
        self.assert_blocked(session, source)
        self.cast(session, 'A', kill, [dying.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[dying.object_id].zone)
        offered, = self.main_rows(session, source)
        result = session.act('pilot:A', {'action_id': offered['id'], 'targets': [], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        previous_turn = session.state.turn_sequence
        self.finish_turn(session)
        self.assertGreater(session.state.turn_sequence, previous_turn)
        self.assert_blocked(session, later)
        self.finish(session, private)

    def test_missing_current_restriction_is_killed_by_the_original_stale_offer_witness(self):
        method = 'test_original_feast_reads_current_controlled_vampires_and_rejects_a_stale_offer'
        with patch('quorune.rules.casting.proposal.compiled_self_cast_restriction_reason', return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()

    def test_payload_minimum_and_missing_new_condition_marker_fail_closed(self):
        session = self.session(113650)
        source = self.add(session.engine, 'Feast of Blood', zone='hand')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                     if p.event == 'cast.restriction']
        for cap in ('casting.restriction.self_fixed_public_condition',
                    'continuous.characteristics.fixed_public_state'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                changed = replace(program, coverage=(), capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile='commander_review')
                self.assertFalse(result['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+cap, result['blockers'])
        descriptor = dict(program.handlers[0])
        descriptor.pop('intrinsic_condition_version')
        with self.assertRaises(SemanticNodeError):SelfCastRestrictionHandler().validate(descriptor)
        with self.assertRaises(SemanticNodeError):
            replace(program, coverage=(), handlers=(descriptor,))

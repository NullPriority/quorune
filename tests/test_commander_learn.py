from __future__ import annotations

"""CR 701.48/903.11: Learn is an optional rummage in Commander."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


CAPABILITY = 'keyword_action.learn.commander'


class CommanderLearnCompilerTests(unittest.TestCase):
    def test_runtime_learn_requires_authoritative_commander_profile_and_locked_actor(self):
        from quorune.commander_learn_model import commander_learn_instruction
        from quorune.semantic_choices.commander_learn import prepare_commander_learn
        from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
        from quorune.semantic_choices.model import SemanticChoiceError
        for profile in ('', 'traditional', 'commander_review', 'unknown', 'commander_duel', 'commander_multiplayer'):
            query = SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'), active_order=tuple('ABCD'), rules_profile=profile)
            context = SemanticChoiceContext(actor='A', stack_ref='learn-stack', stack_controller='A',
                stack_label='Learn', source_ref=None, card_ref=None, semantic_program_id='learn-test',
                semantic_program_version=1, query=query)
            if profile in {'commander_duel', 'commander_multiplayer'}:
                prepared = prepare_commander_learn(commander_learn_instruction('A'), context)
                self.assertEqual((), prepared.request.choice.legal_refs)
            else:
                with self.assertRaises(SemanticChoiceError):
                    prepare_commander_learn(commander_learn_instruction('A'), context)
        context = SemanticChoiceContext(actor='B', stack_ref='learn-stack', stack_controller='A',
            stack_label='Learn', source_ref=None, card_ref=None, semantic_program_id='learn-test',
            semantic_program_version=1, query=query)
        with self.assertRaises(SemanticChoiceError):
            prepare_commander_learn(commander_learn_instruction('B'), context)

    def test_learn_spell_entry_death_and_activation_have_explicit_commander_scope(self):
        registry = load_default_capability_registry()
        for text, types in (
            ('Learn.', 'Sorcery'),
            ('When this creature enters, learn.', 'Creature'),
            ('When this creature dies, learn.', 'Creature'),
            ('{2}, Sacrifice this creature: Learn.', 'Creature'),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, types), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effect, = ir.faces[0].nodes[-1].effects
                self.assertEqual({'op': 'offer_optional_mana_payment', 'schema_version': 5,
                    'player': '$controller', 'action': 'learn', 'rules_scope': 'commander'}, dict(effect))
                self.assertIn(CAPABILITY, ir.faces[0].nodes[-1].capability_dependencies)

    def test_other_profiles_and_unrepresented_learn_subjects_remain_residual(self):
        registry = load_default_capability_registry()
        for text in ('Target player learns.', 'Each player learns.', 'Learn twice.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(record(text, 'Sorcery'),
                    capability_registry=registry, capability_profile='commander_review').status)
        self.assertNotEqual('exact', compile_oracle_card(record('Learn.', 'Sorcery'),
            capability_registry=registry, capability_profile='traditional').status)
        ir = compile_oracle_card(record('Learn.', 'Sorcery'), capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        for key, value in (('rules_scope', 'traditional'), ('schema_version', True), ('action', 'lesson'), ('effects', [])):
            effect = {**node.effects[0], key: value}
            self.assertFalse(capability_dependencies_for_node(effects=(effect,), target_schema=None, mechanic_ids=node.mechanics))

    def test_learn_discard_and_draw_owner_withdrawals_prevent_complete_admission(self):
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner in (CAPABILITY, 'choice.affected_player.fixed_discard', 'zone.draw.library_to_hand'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['Learn owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record('Learn.', 'Sorcery'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)


class CommanderLearnRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'commander-learn.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/commander-learn-cards.json',
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Learn witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_igneous_damage_then_private_discard_draw_rollback_load_and_replay(self):
        session = self.session(420001)
        source = self.add(session.engine, 'Igneous Inspiration', zone='hand', ref='learn-original')
        payment = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='learn-own-private')
        foreign = self.add(session.engine, 'Generic Bound Growth', seat='B', zone='hand', ref='learn-foreign-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, ['B'])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(['pilot:A'], session.pending_principals())
        self.assertEqual(37, session.state.players['B'].life)
        self.assertEqual([40, 40, 40], [session.state.players[s].life for s in 'ACD'])
        before_hand = set(session.state.players['A'].zones['hand'])
        before_library = len(session.state.players['A'].zones['library'])
        self.assertIn(payment.ref, json.dumps(session.packet('pilot:A', full=True)['decision']))
        for viewer in 'BCD':
            self.assertNotIn(payment.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        before = authoritative_state_hash(session.state)
        for principal, cards in (('pilot:B', [payment.ref]), ('pilot:A', [foreign.ref]), ('pilot:A', [payment.ref, foreign.ref])):
            self.assertFalse(session.act(principal, {'action_id': 'choose', 'cards': cards}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[payment.object_id].zone)
        self.assertEqual('hand', session.state.cards[foreign.object_id].zone)
        self.assertEqual(before_library - 1, len(session.state.players['A'].zones['library']))
        after_hand = set(session.state.players['A'].zones['hand'])
        self.assertEqual(len(before_hand), len(after_hand))
        drawn, = after_hand - before_hand
        for viewer in 'BCD':
            self.assertNotIn(session.state.cards[drawn].ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_learn_decline_and_empty_hand_never_draw_or_import_lesson(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                session = self.session(420010 + empty)
                if empty:
                    for object_id in tuple(session.state.players['A'].zones['hand']):
                        session.engine.move_card(object_id, 'graveyard', log=False)
                source = self.add(session.engine, 'Igneous Inspiration', zone='hand', ref='learn-decline-original')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'R': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source, ['B'])
                self.resolve(session)
                hand = tuple(session.state.players['A'].zones['hand'])
                library = tuple(session.state.players['A'].zones['library'])
                objects = set(session.state.cards)
                if empty:
                    self.assertEqual((), hand)
                result = session.act('pilot:A', {'action_id': 'choose', 'cards': []})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                self.assertEqual(hand, tuple(session.state.players['A'].zones['hand']))
                self.assertEqual(library, tuple(session.state.players['A'].zones['library']))
                self.assertEqual(objects, set(session.state.cards))
                self.assertEqual(37, session.state.players['B'].life)
                self.replay(session, load=True)

    def test_learn_discard_destination_replacement_resumes_once_before_draw(self):
        session = self.session(420030)
        source = self.add(session.engine, 'Igneous Inspiration', zone='hand', ref='learn-replaced-source')
        payment = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='learn-replaced-payment')
        self.add(session.engine, 'Generic Payment Destination Replacement', seat='B', ref='learn-exile-one')
        self.add(session.engine, 'Generic Payment Competing Replacement', seat='C', ref='learn-exile-two')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, ['B'])
        self.resolve(session)
        library = len(session.state.players['A'].zones['library'])
        paid = session.act('pilot:A', {'action_id': 'choose', 'cards': [payment.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIn('replacement', session.state.pending_decision.kind)
        self.assertEqual(library, len(session.state.players['A'].zones['library']))
        session = self.replay(session, load=True)
        for _ in range(4):
            decision = session.state.pending_decision
            if decision is None or 'replacement' not in decision.kind:
                break
            public = session.packet('pilot:A', full=True)['decision']
            chosen = session.act('pilot:A', {'action_id': 'choose', 'replacement': public['ctx']['options'][0]['id']})
            self.assertTrue(chosen.ok, chosen.summary)
        self.resolve(session)
        current = session.state.cards[payment.object_id]
        self.assertEqual('exile', current.zone)
        self.assertEqual(1, sum(current.counters.values()))
        self.assertEqual(library - 1, len(session.state.players['A'].zones['library']))
        self.replay(session, load=True)

    def test_two_learn_instructions_use_each_current_hand_after_prior_draw(self):
        session = self.session(420040)
        source = self.add(session.engine, 'Generic Sequential Learn', zone='hand', ref='learn-sequence-source')
        payment = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='learn-sequence-payment')
        self.assert_closed(session, source)
        self.prepare(session, mana={})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        hand = set(session.state.players['A'].zones['hand'])
        library = len(session.state.players['A'].zones['library'])
        paid = session.act('pilot:A', {'action_id': 'choose', 'cards': [payment.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.resolve(session)
        first_draw, = set(session.state.players['A'].zones['hand']) - hand
        ref = session.state.cards[first_draw].ref
        self.assertIn(ref, json.dumps(session.packet('pilot:A', full=True)['decision']))
        session = self.replay(session, load=True)
        paid = session.act('pilot:A', {'action_id': 'choose', 'cards': [ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[first_draw].zone)
        self.assertEqual(library - 2, len(session.state.players['A'].zones['library']))
        self.assertEqual(len(hand), len(session.state.players['A'].zones['hand']))
        self.replay(session, load=True)

    def test_actual_pop_quiz_draw_precedes_learn_and_professor_entry_uses_controller(self):
        for name, mana in (('Pop Quiz', {'C': 2, 'U': 1}), ('Professor of Symbology', {'C': 1, 'W': 1})):
            with self.subTest(name=name):
                session = self.session(420020 + int(name == 'Pop Quiz'))
                source = self.add(session.engine, name, zone='hand', ref='learn-composed-original')
                payment = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='learn-composed-payment')
                self.assert_closed(session, source)
                self.prepare(session, mana=mana)
                self.checkpoint(session)
                before = len(session.state.players['A'].zones['library'])
                self.cast(session, 'A', source)
                self.resolve(session)
                self.assertEqual(['pilot:A'], session.pending_principals())
                self.assertEqual(before - int(name == 'Pop Quiz'), len(session.state.players['A'].zones['library']))
                session = self.replay(session, load=True)
                result = session.act('pilot:A', {'action_id': 'choose', 'cards': [payment.ref]})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                self.assertEqual(before - 1 - int(name == 'Pop Quiz'), len(session.state.players['A'].zones['library']))
                self.assertEqual('graveyard', session.state.cards[payment.object_id].zone)
                self.assertEqual('battlefield' if name == 'Professor of Symbology' else 'graveyard', session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

from __future__ import annotations

"""CR608.2d/702.16: source-pinned temporary chosen-color protection."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import protection_specs
from quorune.carddb import CardDatabase
from quorune.compiler.source_protection_choices import CAPABILITY, COLORS, source_protection_choice_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_protection_choice_shapes import source_protection_choice_capabilities
import test_source_keyword_choices as controls
from scripts.build_test_database import build_fixture_database

NAMES = ('Jareth, Leonine Titan', 'Kami of the Painted Road', 'Knight of Dawn',
         'Cartel Aristocrat', 'Rappelling Scouts', 'Resilient Wanderer')


class SourceProtectionChoiceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.SourceKeywordChoiceTests.session
    add = controls.SourceKeywordChoiceTests.add
    prepare = controls.SourceKeywordChoiceTests.prepare
    checkpoint = controls.SourceKeywordChoiceTests.checkpoint
    cast = controls.SourceKeywordChoiceTests.cast
    resolve = controls.SourceKeywordChoiceTests.resolve
    replay = controls.SourceKeywordChoiceTests.replay
    binding = controls.SourceKeywordChoiceTests.binding
    actions = controls.SourceKeywordChoiceTests.actions
    private_replay = controls.SourceKeywordChoiceTests.private_replay
    action = controls.SourceKeywordChoiceTests.action
    activate = controls.SourceKeywordChoiceTests.activate
    choose = controls.SourceKeywordChoiceTests.choose
    advance = controls.SourceKeywordChoiceTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-protection.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/source-protection-choice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source protection', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def protection(self, session, source):
        return {value.quality for value in protection_specs(session.engine._effective_ability_fragments(session.state.cards[source.object_id]))}

    def priority(self, session, seat):
        for _ in range(8):
            if session.state.priority_player == seat:
                return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Expected priority did not reach the selected seat')

    def test_six_originals_have_complete_nonempty_source_color_programs_and_preflight(self):
        for index, name in enumerate(NAMES):
            session = self.session(739100 + index)
            source = self.add(session.engine, name)
            self.add(session.engine, 'Grizzly Bears')
            self.add(session.engine, 'Generic Bound Growth', zone='hand')
            self.prepare(session, mana={'W': 3, 'C': 3})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertTrue(self.binding(session, source)['strict_capability_ready'], name)
            program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(effect.get('op') == 'choose_option' for effect in p.effects)]
            self.assertIn(CAPABILITY, program.capability_dependencies)
            self.assertEqual(tuple(symbol for symbol, _ in COLORS), tuple(row['id'] for row in program.effects[0]['options']))
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'], name)

    def test_actual_cartel_pays_sacrifice_then_chooses_red_to_invalidate_a_real_bolt(self):
        session = self.session(739120)
        source = self.add(session.engine, 'Cartel Aristocrat')
        payer = self.add(session.engine, 'Grizzly Bears')
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['R'] = 1
        self.prepare(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.checkpoint(session)
        self.cast(session, 'B', bolt, [source.ref])
        self.priority(session, 'A')
        self.activate(session, source, cost_cards=[payer.ref])
        self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        for seat, word in (('B', 'R'), ('A', 'Pink'), ('A', 'WU')):
            self.assertFalse(session.act('pilot:' + seat, {'action_id': 'choose', 'choice': word}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, 'R')
        self.assertEqual({'R'}, self.protection(session, source))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
        self.assertEqual('graveyard', session.state.cards[bolt.object_id].zone)
        self.private_replay(session, private)

    def test_shape_rejects_wrong_quality_actor_source_or_incomplete_colors(self):
        _, effects, schema, mechanics = source_protection_choice_template(
            'This creature gains protection from the color of your choice until end of turn.',
            source_is_permanent=True, source_card_types=('Creature',), source_name='Generic Source')
        self.assertIn(CAPABILITY, source_protection_choice_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for case in ('actor', 'source', 'quality', 'colors', 'fields'):
            altered = deepcopy(effects)
            if case == 'actor': altered[0]['player'] = '$source.controller'
            if case == 'source': altered[0]['then_by_choice']['W'][0]['card'] = '$target.0'
            if case == 'quality': altered[0]['then_by_choice']['W'][0]['ability_fragments'][0]['value']['quality'] = 'U'
            if case == 'colors': altered[0]['options'].pop()
            if case == 'fields': altered[0]['then_by_choice']['W'][0]['extra'] = True
            self.assertEqual((), source_protection_choice_capabilities(effects=altered, target_schema=None, mechanic_ids=mechanics))

    def test_fresh_cartel_witness_kills_omitted_protection_grant(self):
        method = 'test_actual_cartel_pays_sacrifice_then_chooses_red_to_invalidate_a_real_bolt'
        with patch('quorune.effect_runtime.temporary_ability_bundle.create_resolution_continuous_effect', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_knight_white_protection_removes_pacifism_and_expiry_does_not_return_it(self):
        session = self.session(739130)
        source = self.add(session.engine, 'Knight of Dawn')
        aura = self.add(session.engine, 'Pacifism', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['A'].mana_pool['W'] = 2
        self.prepare(session, actor='B', active='B', mana={'W': 1, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'B', aura, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(source.object_id, session.state.cards[aura.object_id].attached_to)
        self.priority(session, 'A')
        self.activate(session, source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.choose(session, 'W')
        self.assertEqual({'W'}, self.protection(session, source))
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        turn = session.state.turn_sequence
        self.advance(session, lambda current: current.state.turn_sequence > turn)
        self.assertEqual(set(), self.protection(session, source))
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        self.private_replay(session, private)

    def test_actual_named_mana_and_private_discard_costs_precede_one_owned_color_choice(self):
        for index, (name, payment, word) in enumerate((
                ('Jareth, Leonine Titan', {'W': 1}, 'G'),
                ('Rappelling Scouts', {'W': 1, 'C': 2}, 'U'),
                ('Resilient Wanderer', {}, 'B'))):
            session = self.session(739140 + index)
            source = self.add(session.engine, name)
            discarded = self.add(session.engine, 'Generic Bound Growth', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='protection-private-C')
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            self.activate(session, source, **({'cost_cards': [discarded.ref]} if index == 2 else {}))
            self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
            self.assertEqual('graveyard' if index == 2 else 'hand', session.state.cards[discarded.object_id].zone)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            session = self.replay(session, load=True)
            self.choose(session, word)
            self.assertEqual({word}, self.protection(session, source))
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            self.private_replay(session, private)

    def test_actual_kami_spirit_and_arcane_casts_choose_for_the_observer_without_foreign_cast_triggers(self):
        for index, name in enumerate(('Lantern Kami', 'Reach Through Mists')):
            session = self.session(739150 + index)
            source = self.add(session.engine, 'Kami of the Painted Road')
            spell = self.add(session.engine, name, zone='hand')
            foreign = self.add(session.engine, 'Reach Through Mists', seat='B', zone='hand', ref='foreign-arcane')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'W': 1} if index == 0 else {'U': 1})
            hand = len(session.state.players['A'].zones['hand'])
            self.checkpoint(session)
            self.cast(session, 'A', spell)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:A'], session.pending_principals())
            session = self.replay(session, load=True)
            self.choose(session, 'U')
            self.assertEqual({'U'}, self.protection(session, source))
            if index == 0:
                self.assertEqual(set(), self.protection(session, spell))
            else:
                self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
            self.cast(session, 'B', foreign)
            self.assertIsNone(self.resolve(session))
            self.assertEqual({'U'}, self.protection(session, source))
            self.private_replay(session, private)

    def test_actual_blink_and_control_response_preserve_source_incarnation_and_original_choice_actor(self):
        for index, control in enumerate((False, True)):
            session = self.session(739160 + index)
            source = self.add(session.engine, 'Knight of Dawn')
            response = self.add(session.engine, 'Word of Seizing' if control else 'Ephemerate',
                seat='B' if control else 'A', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            identity = source.logical_object_id
            session.state.players['B'].mana_pool.update({'R': 2, 'C': 3})
            self.prepare(session, mana={'W': 3})
            self.checkpoint(session)
            self.activate(session, source)
            self.cast(session, 'B' if control else 'A', response, [source.ref])
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:A'], session.pending_principals())
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'choice': 'W'}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            session = self.replay(session, load=True)
            self.choose(session, 'W')
            current = session.state.cards[source.object_id]
            if control:
                self.assertEqual('B', current.controller)
                self.assertEqual(identity, current.logical_object_id)
                self.assertEqual({'W'}, self.protection(session, source))
            else:
                self.assertNotEqual(identity, current.logical_object_id)
                self.assertEqual(set(), self.protection(session, source))
            self.private_replay(session, private)

    def test_source_and_protection_authority_cannot_be_omitted_or_replaced_by_unknown_grammar(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import CapabilityRegistry
        session = self.session(739170)
        source = self.add(session.engine, 'Knight of Dawn')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(e.get('op') == 'choose_option' for e in p.effects)]
        for cap in (CAPABILITY, 'protection.typed.debt', 'continuous.resolution.fixed_characteristics_until_end_of_turn'):
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            missing = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn source protection'])
        self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Knight of Dawn'),
            capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)
        original = 'This creature gains protection from the color of your choice until end of turn.'
        for text in (original.replace('your choice', "its controller's choice"),
                     original.replace('color', 'card type'), original.replace('end of turn', 'your next turn'),
                     original + ' Draw a card.'):
            self.assertIsNone(source_protection_choice_template(text, source_is_permanent=True,
                source_card_types=('Creature',), source_name='Generic Source'))

    def test_unmatched_red_damage_still_kills_a_source_protected_from_blue(self):
        session = self.session(739180)
        source = self.add(session.engine, 'Cartel Aristocrat')
        payer = self.add(session.engine, 'Grizzly Bears')
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['R'] = 1
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source, cost_cards=[payer.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, 'U')
        self.assertEqual({'U'}, self.protection(session, source))
        self.cast(session, 'B', bolt, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_chosen_green_protection_rejects_a_real_green_blocker_and_allows_white(self):
        session = self.session(739181)
        source = self.add(session.engine, 'Jareth, Leonine Titan')
        green = self.add(session.engine, 'Grizzly Bears', seat='B')
        white = self.add(session.engine, 'Rappelling Scouts', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'W': 1})
        self.checkpoint(session)
        self.activate(session, source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, 'G')
        self.advance(session, lambda current: current.state.pending_decision is not None and current.state.pending_decision.kind == 'combat.attackers')
        result = session.act('pilot:A', {'action_id': 'attack', 'attacks': {source.ref: 'B'}})
        self.assertTrue(result.ok, result.summary)
        self.advance(session, lambda current: current.state.pending_decision is not None and current.state.pending_decision.kind == 'combat.blockers')
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:B', {'action_id': 'block', 'blocks': {green.ref: [source.ref]}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {white.ref: [source.ref]}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.private_replay(session, private)

    def test_fresh_original_admission_kills_omitted_source_protection_compilation(self):
        method = 'test_six_originals_have_complete_nonempty_source_color_programs_and_preflight'
        with patch('quorune.compiler.resolution_effect_templates.source_protection_choice_template', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

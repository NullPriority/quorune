from __future__ import annotations

"""CR608.2d: resolve one source-local ordinary keyword choice."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiler.source_keyword_choices import CAPABILITY, source_keyword_choice_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.source_keyword_choice_shapes import source_keyword_choice_capabilities
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls
import test_quantified_target_keywords as combat_controls

NAMES = ('Butcher of the Horde', 'Assassin Initiate', 'Shifting Ceratops',
         'Veteran Warleader', 'Lunar Avenger', 'Argivian Avenger')
CHOICES = ('Vigilance', 'Deathtouch', 'Reach', 'First Strike', 'Haste', 'Flying')
PRINTED_OPTIONS = (
    ('Vigilance', 'Lifelink', 'Haste'), ('Flying', 'Deathtouch', 'Lifelink'),
    ('Reach', 'Trample', 'Haste'), ('First Strike', 'Vigilance', 'Trample'),
    ('Flying', 'First Strike', 'Haste'), ('Flying', 'Vigilance', 'Deathtouch', 'Haste'),
)


class SourceKeywordChoiceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    private_replay = controls.CompoundDamageRuntimeTests.private_replay
    advance = combat_controls.QuantifiedTargetKeywordRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-choices.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/source-keyword-choice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source keyword choice', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def action(self, session, source):
        current = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + current.ref + ':'))

    def activate(self, session, source, **extra):
        result = session.act('pilot:A', {'action_id': self.action(session, source)['id'], 'pay': 'auto', **extra})
        self.assertTrue(result.ok, result.summary)

    def choose(self, session, word, *, seat='A'):
        result = session.act('pilot:' + seat, {'action_id': 'choose', 'choice': word})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

    def test_six_originals_have_nonempty_admitted_choice_programs_and_complete_preflight(self):
        for index, name in enumerate(NAMES):
            session = self.session(737100 + index)
            source = self.add(session.engine, name)
            self.add(session.engine, 'Hada Freeblade')
            source.counters['+1/+1'] = 1
            self.prepare(session, mana={'C': 2, 'G': 1})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertTrue(self.binding(session, source)['strict_capability_ready'], name)
            programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
            program, = [program for program in programs if any(effect.get('op') == 'choose_option' for effect in program.effects)]
            self.assertIn(CAPABILITY, program.capability_dependencies)
            self.assertEqual(PRINTED_OPTIONS[index], tuple(option['id'] for option in program.effects[-1]['options']))
            self.assertNotIn('choice', self.action(session, source).get('choice_schema', {}))
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'], name)

    def test_actual_six_source_costs_precede_resolution_choices_and_keep_private_loaded_replay(self):
        for index, (name, word) in enumerate(zip(NAMES, CHOICES)):
            session = self.session(737120 + index)
            source = self.add(session.engine, name)
            payer = self.add(session.engine, 'Hada Freeblade')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            if name == 'Lunar Avenger':
                source.counters['+1/+1'] = 2
            self.prepare(session, mana={'C': 1, 'G': 1})
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            self.checkpoint(session)
            self.activate(session, source, **({'cost_cards': [payer.ref]} if index in (0, 3) else {}))
            if index == 0:
                self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
            if index == 3:
                self.assertTrue(session.state.cards[payer.object_id].tapped)
            if index == 4:
                self.assertEqual(1, session.state.cards[source.object_id].counters['+1/+1'])
            self.assertNotIn(word.casefold(), session.engine._combat_keywords(session.state.cards[source.object_id]))
            decision = self.resolve(session)
            self.assertEqual('semantic.choice', decision.kind)
            if index == 5:
                self.assertEqual(4, session.engine._numeric_stat(source.object_id, 'power'))
            before = authoritative_state_hash(session.state)
            for seat, option in (('B', word), ('A', 'NotARealKeyword')):
                self.assertFalse(session.act('pilot:' + seat, {'action_id': 'choose', 'choice': option}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
            session = self.replay(session, load=True)
            self.choose(session, word)
            self.assertIn(word.casefold(), session.engine._combat_keywords(session.state.cards[source.object_id]))
            for other in CHOICES:
                if other != word and other != 'Flying':
                    self.assertNotIn(other.casefold(), session.engine._combat_keywords(session.state.cards[source.object_id]))
            if index == 5:
                self.assertEqual((4, 4), (session.engine._numeric_stat(source.object_id, 'power'),
                    session.engine._numeric_stat(source.object_id, 'toughness')))
            self.private_replay(session, private)

    def test_choice_shape_rejects_mismatched_branches_other_objects_and_unknown_grammar(self):
        text = 'This creature gains your choice of flying, deathtouch, or haste until end of turn.'
        template = source_keyword_choice_template(text, source_is_permanent=True,
            source_card_types=('Creature',), source_name='Generic Bound Choice')
        _, effects, schema, mechanics = template.compiled()
        self.assertIn(CAPABILITY, source_keyword_choice_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for change in ('actor', 'duplicate', 'branch', 'object', 'stats', 'unknown'):
            altered = deepcopy(effects)
            choice = altered[-1]
            if change == 'actor': choice['player'] = '$source.controller'
            if change == 'duplicate': choice['options'].append(choice['options'][0])
            if change == 'branch': del choice['then_by_choice']['Flying']
            if change == 'object': choice['then_by_choice']['Flying'][0]['card'] = '$target.0'
            if change == 'stats': choice['then_by_choice']['Flying'][0]['power'] = -1
            if change == 'unknown': choice['extra'] = True
            self.assertEqual((), source_keyword_choice_capabilities(effects=altered, target_schema=None, mechanic_ids=mechanics))
        for bad in (text.replace('haste', 'bands with other green creatures'), text.replace('haste', 'flying'),
                    text.replace('flying, deathtouch, or haste', 'flying or deathtouch, haste'),
                    text.replace('flying, deathtouch, or haste', 'flying or deathtouch or haste'),
                    text.replace('your choice', "its controller's choice"),
                    text.replace('end of turn', 'your next turn'),
                    'Until end of turn, this creature gets -X/-X and gains your choice of flying or haste.'):
            self.assertIsNone(source_keyword_choice_template(bad, source_is_permanent=True,
                source_card_types=('Creature',), source_name='Generic Bound Choice'))
        self.assertNotEqual('exact', compile_oracle_card(self.db.lookup("Jodah's Avenger"),
            capability_registry=self.registry, capability_profile='commander_review').status)

    def test_runtime_cannot_omit_choice_source_or_keyword_authority(self):
        session = self.session(737140)
        source = self.add(session.engine, 'Assassin Initiate')
        program, = [program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(effect.get('op') == 'choose_option' for effect in program.effects)]
        for cap in (CAPABILITY, 'continuous.resolution.fixed_source_characteristics_until_end_of_turn',
                    'combat.block.flying', 'damage.result.lifelink'):
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            missing = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])

    def test_fresh_original_admission_kills_omitted_choice_compilation(self):
        method = 'test_six_originals_have_nonempty_admitted_choice_programs_and_complete_preflight'
        with patch('quorune.compiler.resolution_effect_templates.source_keyword_choice_template', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_actual_choice_kills_omitted_characteristic_commit(self):
        method = 'test_actual_six_source_costs_precede_resolution_choices_and_keep_private_loaded_replay'
        with patch('quorune.effect_runtime.objects_stack_and_tokens.create_resolution_continuous_effect_components', return_value=[]):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_control_response_keeps_original_choice_actor_and_current_source_recipient(self):
        session = self.session(737150)
        source = self.add(session.engine, 'Assassin Initiate')
        response = self.add(session.engine, 'Word of Seizing', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool.update({'R': 2, 'C': 3})
        self.prepare(session, mana={'C': 1})
        self.checkpoint(session)
        self.activate(session, source)
        self.assertTrue(self.binding(session, response)['strict_capability_ready'])
        self.cast(session, 'B', response, [source.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertEqual(['pilot:A'], session.pending_principals())
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'choice': 'Lifelink'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, 'Lifelink')
        self.assertIn('lifelink', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.private_replay(session, private)

    def test_actual_bounce_and_blink_never_transfer_choice_to_a_different_incarnation(self):
        for index, blink in enumerate((False, True)):
            session = self.session(737160 + index)
            source = self.add(session.engine, 'Assassin Initiate')
            response = self.add(session.engine, 'Ephemerate' if blink else 'Unsummon',
                seat='A' if blink else 'B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            identity = source.logical_object_id
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'C': 1, 'W': 1})
            self.checkpoint(session)
            self.activate(session, source, pay='manual', payment={'C': 1})
            self.assertTrue(self.binding(session, response)['strict_capability_ready'], response.printed_name)
            self.cast(session, 'A' if blink else 'B', response, [source.ref])
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertNotEqual(identity, session.state.cards[source.object_id].logical_object_id)
            session = self.replay(session, load=True)
            self.choose(session, 'Flying')
            current = session.state.cards[source.object_id]
            self.assertEqual('battlefield' if blink else 'hand', current.zone)
            self.assertNotIn('flying', session.engine._combat_keywords(current))
            self.private_replay(session, private)

    def test_actual_argivian_prefix_applies_once_per_activation_and_both_effects_expire(self):
        session = self.session(737170)
        source = self.add(session.engine, 'Argivian Avenger')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        for index, word in enumerate(('Flying', 'Vigilance')):
            self.activate(session, source)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(4 - index, session.engine._numeric_stat(source.object_id, 'power'))
            session = self.replay(session, load=True)
            self.choose(session, word)
            self.assertEqual(4 - index, session.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual({'flying', 'vigilance'}, set(session.engine._combat_keywords(session.state.cards[source.object_id])))
        turn = session.state.turn_sequence
        self.advance(session, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((5, 5), (session.engine._numeric_stat(source.object_id, 'power'),
            session.engine._numeric_stat(source.object_id, 'toughness')))
        self.assertFalse(session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.private_replay(session, private)

    def test_actual_argivian_lethal_prefix_waits_for_the_rest_of_resolution(self):
        session = self.session(737171)
        source = self.add(session.engine, 'Argivian Avenger')
        source.marked_damage = 4
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 1})
        self.checkpoint(session)
        self.activate(session, source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.choose(session, 'Haste')
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_actual_assassin_chosen_lifelink_and_deathtouch_both_change_fight_consequences(self):
        session = self.session(737180)
        source = self.add(session.engine, 'Assassin Initiate')
        opponent = self.add(session.engine, 'Grizzly Bears', seat='B')
        fight = self.add(session.engine, 'Prey Upon', zone='hand')
        self.add(session.engine, 'Forest')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        for word in ('Lifelink', 'Deathtouch'):
            self.activate(session, source)
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.choose(session, word)
        self.cast(session, 'A', fight, [source.ref, opponent.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[opponent.object_id].zone)
        self.assertEqual([41, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
        self.private_replay(session, private)

    def test_actual_veteran_tap_cost_and_chosen_first_strike_kill_blocker_before_normal_damage(self):
        session = self.session(737181)
        source = self.add(session.engine, 'Veteran Warleader')
        ally = self.add(session.engine, 'Hada Freeblade')
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source, cost_cards=[ally.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, 'First Strike')
        self.advance(session, lambda current: current.state.pending_decision is not None and current.state.pending_decision.kind == 'combat.attackers')
        result = session.act('pilot:A', {'action_id': 'attack', 'attacks': {source.ref: 'B'}})
        self.assertTrue(result.ok, result.summary)
        self.advance(session, lambda current: current.state.pending_decision is not None and current.state.pending_decision.kind == 'combat.blockers')
        result = session.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: [source.ref]}})
        self.assertTrue(result.ok, result.summary)
        self.advance(session, lambda current: current.state.cards[blocker.object_id].zone == 'graveyard')
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
        self.assertTrue(session.state.cards[ally.object_id].tapped)
        self.private_replay(session, private)

    def test_withdrawn_choice_capability_keeps_original_sources_residual(self):
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        next(row for row in raw['capabilities'] if row['id'] == CAPABILITY).update(
            status='blocked', blockers=['withdrawn source choice authority'])
        registry = CapabilityRegistry(raw)
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, name)

    def test_actual_lunar_sunburst_entry_produces_the_counter_paid_for_its_chosen_keyword(self):
        session = self.session(737190)
        source = self.add(session.engine, 'Lunar Avenger', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 4, 'W': 1, 'U': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.cards[source.object_id].counters['+1/+1'])
        self.activate(session, source)
        self.assertEqual(2, session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.choose(session, 'First Strike')
        self.assertEqual((4, 4), (session.engine._numeric_stat(source.object_id, 'power'),
            session.engine._numeric_stat(source.object_id, 'toughness')))
        self.assertIn('first strike', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.private_replay(session, private)

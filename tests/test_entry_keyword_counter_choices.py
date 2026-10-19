from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.keyword_counters import (
    KeywordCounterError, FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY,
    FIXED_KEYWORD_COUNTER_CHOICE_HANDLER, KeywordCounterEntryChoiceSpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.model import StackItem
from quorune.replacement_effects import ReplacementEffect, ReplacementEffectError
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree


TEXT = 'This creature enters with your choice of a flying counter or a first strike counter on it.'


class EntryKeywordCounterChoiceCompilerTests(unittest.TestCase):
    def compile(self, text=TEXT, registry=None):
        return compile_oracle_card(replace(record(text, 'Creature — Human'), name='Fixture',
            oracle_id='fixture:entry-keyword-choice'),
            capability_registry=registry or load_default_capability_registry(), capability_profile='commander_review')

    def test_closed_choice_model_codec_labels_and_historical_defaults(self):
        spec = KeywordCounterEntryChoiceSpec((' Flying ', 'First Strike'))
        self.assertEqual(('flying', 'first strike'), spec.counter_names)
        self.assertEqual(('flying', 'first-strike'), spec.mechanics)
        self.assertEqual(spec, KeywordCounterEntryChoiceSpec.from_descriptor(spec.descriptor()))
        for names in ((), ('flying',), ('flying', 'flying'), ('flying', 'ward'),
                      ('flying', True), ('flying', 'reach', 'haste'), 'fr'):
            with self.subTest(names=names), self.assertRaises(KeywordCounterError):
                KeywordCounterEntryChoiceSpec(names)
        for changed in ({'schema_version': True}, {'schema_version': 2}, {'event': 'damage'},
                        {'rule_id': '614.1c'}, {'unexpected': True}):
            with self.subTest(changed=changed), self.assertRaises(KeywordCounterError):
                KeywordCounterEntryChoiceSpec.from_descriptor({**spec.descriptor(), **changed})
        legacy = {'effect_id': 'entry:legacy', 'source_id': 'source', 'event_kind': 'zone.change',
            'replacement_class': 5, 'conditions': {}, 'operations': [{'op': 'set', 'field': 'tapped', 'value': True}],
            'optional': False, 'chooser': 'affected_player', 'label': 'Enter tapped'}
        self.assertEqual(legacy, ReplacementEffect.from_dict(legacy).to_dict())
        named = {**legacy, 'optional': True,
            'decline_operations': [{'op': 'set', 'field': 'tapped', 'value': False}],
            'decline_label': 'Enter untapped'}
        self.assertEqual(named, ReplacementEffect.from_dict(named).to_dict())
        for label in ('', ' ', True, 2):
            with self.subTest(label=label), self.assertRaises(ReplacementEffectError):
                ReplacementEffect.from_dict({**named, 'decline_label': label})
        with self.assertRaises(ReplacementEffectError):
            ReplacementEffect.from_dict({**legacy, 'decline_label': 'Alternative'})

    def test_source_spanned_choices_require_both_existing_counter_owners(self):
        for text in (TEXT, TEXT.replace('This creature', 'Fixture'),
                     TEXT.replace('flying', 'lifelink').replace('first strike', 'deathtouch')):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertEqual(FIXED_KEYWORD_COUNTER_CHOICE_HANDLER, node.handlers[0]['handler_id'])
                self.assertIn(FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY, node.capability_dependencies)
                if text == TEXT:
                    self.assertEqual(('flying', 'first-strike'), node.mechanics)
                    self.assertIn('combat.block.flying', node.capability_dependencies)
                    self.assertIn('combat.damage.participation.strike_steps', node.capability_dependencies)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY, 'counter.characteristic.keyword',
                           'counter.placement.quantity_replacement'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['withdrawn entry counter owner'])
            with self.subTest(capability=capability):
                self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_nearby_unknown_choices_and_omitted_producer_remain_residual(self):
        for text in (TEXT.replace('first strike', 'ward'), TEXT.replace('a flying', 'two flying'),
                     TEXT.replace('your choice', 'an opponent\'s choice'), TEXT.replace(' on it', ''),
                     TEXT.replace('first strike counter', 'flying counter'),
                     TEXT.replace('counter on it.', 'counter or a reach counter on it.')):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        with patch('quorune.compiler.fixed_self_entry_counter_templates.fixed_keyword_counter_entry_choice_handler',
                   return_value=None):
            self.assertNotEqual('exact', self.compile().status)


class EntryKeywordCounterChoiceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'entry-keyword-choices.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/counter-replacement-cards.json',
            ROOT/'tests/fixtures/token-entry-boundary-cards.json',
            ROOT/'tests/fixtures/entry-keyword-counter-choice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Entry keyword choice witness',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast_to_choice(self, session, source, mana):
        self.assert_closed(session, source)
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana=mana)
        self.checkpoint(session)
        action = next(r for r in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if r['id'] == 'cast:'+source.ref)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('replacement.order', decision.kind)
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        self.assertEqual({}, session.state.cards[source.object_id].counters)

    def choose_counter(self, session, keyword, *, actor='A'):
        decision = session.packet('pilot:'+actor, full=True)['decision']
        option, = [r for r in decision['ctx']['options'] if r['label'] == 'Enter with a '+keyword+' counter']
        rendered = json.dumps(decision['legal_actions'])
        self.assertIn('Enter with a '+keyword+' counter', rendered)
        self.assertNotIn('Decline Enter', rendered)
        result = session.act('pilot:'+actor, {'action_id': 'choose', 'choices': {'replacement': option['id']}})
        self.assertTrue(result.ok, result.summary)

    def test_actual_both_branches_have_named_forms_atomic_authority_private_pending_load_and_replay(self):
        for i, keyword in enumerate(('vigilance', 'reach')):
            with self.subTest(keyword=keyword):
                session = self.session(12261430+i)
                source = self.add(session.engine, 'Flycatcher Giraffid', zone='hand', ref='choice-giraffid')
                hidden = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='choice-private-c')
                self.cast_to_choice(session, source, {'C': 4, 'G': 1})
                decision = session.packet('pilot:A', full=True)['decision']
                self.assertEqual(2, len(decision['ctx']['options']))
                for seat in 'BCD':
                    packet = session.packet('pilot:'+seat, full=True)
                    self.assertIsNone(packet['decision'])
                selection = decision['ctx']['options'][0]['id']
                self.reject(session, 'choose', 'D', choices={'replacement': selection})
                self.reject(session, 'choose', choices={'replacement': 'decline:unknown'})
                session = self.replay(session, load=True)
                self.choose_counter(session, keyword)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[source.object_id]
                self.assertEqual('battlefield', current.zone)
                self.assertEqual({keyword: 1}, current.counters)
                abilities = [v.casefold() for v in session.engine._effective_card_data(current)['keywords']]
                self.assertIn(keyword, abilities)
                self.assertNotIn('reach' if keyword == 'vigilance' else 'vigilance', abilities)
                for seat in 'ABD':
                    self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
                self.replay(session, load=True)

    def test_actual_alternative_counter_uses_the_same_quantity_replacement_and_replay(self):
        session = self.session(12261440)
        source = self.add(session.engine, 'Helica Glider', zone='hand', ref='choice-glider')
        doubler = self.add(session.engine, 'Doubling Season', ref='choice-doubler')
        counter_programs = [p for p in session.engine.semantics.programs_for_oracle(doubler.oracle_id)
                            if p.event == 'counter.place']
        self.assertTrue(counter_programs)
        self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in counter_programs))
        self.cast_to_choice(session, source, {'C': 2, 'W': 1})
        self.choose_counter(session, 'first strike')
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'first strike': 2}, session.state.cards[source.object_id].counters)
        self.replay(session, load=True)

    def test_runtime_omission_cannot_silently_satisfy_the_native_entry_choice(self):
        session = self.session(12261450)
        source = self.add(session.engine, 'Flycatcher Giraffid', zone='hand', ref='choice-mutant')
        with patch('quorune.semantic_runtime.entry_choices.KeywordCounterEntryChoiceHandler.subject_replacement_effect',
                   return_value=None), self.assertRaises(AssertionError):
            self.cast_to_choice(session, source, {'C': 4, 'G': 1})

    def test_unrepresented_counter_halving_source_stays_outside_complete_admission(self):
        session = self.session(12261455)
        source = self.add(session.engine, 'Helica Glider', zone='hand', ref='choice-zero-source')
        self.assert_closed(session, source)
        row = self.db.lookup('Vorinclex, Monstrous Raider')
        program = compile_best_available_card_program(self.db, row, semantic_registry=session.engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict_capability_ready'])
        ir = compile_oracle_card(row, capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(any('half that many' in residual.text
            for face in ir.faces for residual in face.residuals if residual.material))

    def test_manifested_original_has_no_entry_counter_or_new_entry_on_turn_up(self):
        session = self.session(12261460)
        spell = self.add(session.engine, 'Soul Summons', zone='hand', ref='choice-manifest-spell')
        source = self.add(session.engine, 'Flycatcher Giraffid', zone='library', ref='choice-manifest-top')
        library = session.state.players['A'].zones['library']
        library.remove(source.object_id)
        library.append(source.object_id)
        for card in (spell, source):
            self.assert_closed(session, card)
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'C': 6, 'W': 1, 'G': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:'+spell.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertTrue(current.face_down)
        self.assertEqual({}, current.counters)
        for seat in 'BCD':
            self.assertNotIn('Flycatcher Giraffid', str(session.packet('pilot:'+seat, full=True)))
        session = self.replay(session, load=True)
        action = 'turn-face-up:'+session.state.cards[source.object_id].ref+':manifest'
        offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertIn(action, [row['id'] for row in offered])
        result = session.act('pilot:A', {'action_id': action, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(session.state.cards[source.object_id].face_down)
        self.assertEqual({}, session.state.cards[source.object_id].counters)
        self.assertEqual([], session.state.stack)
        self.replay(session, load=True)

    def test_current_prospective_ability_removal_suppresses_the_original_entry_choice(self):
        session = self.session(12261470)
        source = self.add(session.engine, 'Helica Glider', zone='hand', ref='choice-humility-source')
        self.add(session.engine, 'Humility', ref='choice-humility')
        self.assert_closed(session, source)
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'C': 2, 'W': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:'+source.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertEqual({}, current.counters)
        self.assertEqual((), session.engine._effective_static_component_keys(current))
        self.assertEqual(1, session.engine._numeric_stat(current.object_id, 'power'))
        self.replay(session, load=True)

    def test_actual_copied_token_gets_its_own_choice_without_inheriting_source_counters(self):
        session = self.session(12261480)
        original = self.add(session.engine, 'Helica Glider', ref='choice-copy-original')
        original.counters['flying'] = 7
        spell = self.add(session.engine, 'Generic Entry One Copy', zone='hand', ref='choice-copy-spell')
        for card in (original, spell):
            self.assert_closed(session, card)
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'W': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:'+spell.ref, 'targets': [original.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('replacement.order', self.resolve(session).kind)
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        session = self.replay(session, load=True)
        self.choose_counter(session, 'first strike')
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual({'first strike': 1}, token.counters)
        self.assertEqual({'flying': 7}, session.state.cards[original.object_id].counters)
        self.assertNotIn('flying', session.engine._combat_keywords(token))
        self.assertIn('first strike', session.engine._combat_keywords(token))
        self.replay(session, load=True)

    def test_destination_controller_chooses_independently_of_the_entering_cards_owner(self):
        session = self.session(12261490)
        source = self.add(session.engine, 'Boot Nipper', zone='hand', ref='choice-owner-a-controller-b')
        # Set up the same public stack fixture used by the entry-owner tests;
        # the accepted choice below still uses B's ordinary issued capability.
        session.state.players['A'].zones['hand'].remove(source.object_id)
        source.zone = 'stack'
        source.controller = 'B'
        source.known_to = list('ABCD')
        source.revealed_to = list('ABCD')
        self.assert_closed(session, source)
        session.state.config.semantic_policy = 'trusted_only'
        item = StackItem(stack_id='entry-choice-controlled', ref='S-entry-choice-controlled',
            kind='spell', controller='B', label=source.printed_name, card_object_id=source.object_id,
            default_destination='battlefield', visibility=list('ABCD'))
        session.state.stack.append(item)
        session.engine._continue_resolution(stack_ref=item.ref, effects=[], destination=None, note='Controller-owned entry choice')
        self.assertEqual(['pilot:B'], session.pending_principals())
        decision = session.packet('pilot:B', full=True)['decision']
        self.reject(session, 'choose', 'A', choices={'replacement': decision['ctx']['options'][0]['id']})
        self.checkpoint(session)
        session = self.replay(session, load=True)
        self.choose_counter(session, 'lifelink', actor='B')
        current = session.state.cards[source.object_id]
        self.assertEqual(('A', 'B', 'battlefield'), (current.owner, current.controller, current.zone))
        self.assertEqual({'lifelink': 1}, current.counters)
        self.replay(session, load=True)

    def test_departed_subject_does_not_apply_the_old_choice_to_a_new_incarnation(self):
        session = self.session(12261500)
        source = self.add(session.engine, 'Flycatcher Giraffid', zone='hand', ref='choice-stale-subject')
        self.cast_to_choice(session, source, {'C': 4, 'G': 1})
        decision = session.packet('pilot:A', full=True)['decision']
        session.engine.move_card(source.object_id, 'graveyard', log=False, semantic_events=False)
        session.engine.move_card(source.object_id, 'hand', log=False, semantic_events=False)
        current = session.state.cards[source.object_id]
        identity = current.logical_object_id
        result = session.act('pilot:A', {'action_id': 'choose',
            'choices': {'replacement': decision['ctx']['options'][0]['id']}})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[source.object_id]
        self.assertEqual('hand', current.zone)
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual({}, current.counters)


if __name__ == '__main__':
    unittest.main()

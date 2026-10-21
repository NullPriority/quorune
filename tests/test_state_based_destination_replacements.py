from __future__ import annotations

"""CR 704.3, 704.5f-g and 616.1: choose before the complete SBA moves."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as controls


class StateBasedDestinationReplacementTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.BoundEffectProgramRuntimeTests.session
    add = controls.BoundEffectProgramRuntimeTests.add
    ready = controls.BoundEffectProgramRuntimeTests.ready
    checkpoint = controls.BoundEffectProgramRuntimeTests.checkpoint
    resolve = controls.BoundEffectProgramRuntimeTests.resolve
    replay = controls.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'state-based-destination.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/state-based-destination-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('State-based destination', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast(self, session, spell, targets, mana):
        action = self.ready(session, spell, mana)
        self.checkpoint(session)
        if targets:
            self.assertTrue(set(targets).issubset(action['target_schema']['legal_refs']))
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': targets,
                                         'pay': 'manual', 'payment': mana})
        self.assertTrue(result.ok, result.summary)

    def replacements(self, session, count=2):
        return [self.add(session.engine, 'Dauthi Voidwalker', ref='destination-source-'+str(i))
                for i in range(count)]

    def choose(self, session, seat='B'):
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        self.assertEqual(['pilot:'+seat], list(session.pending_principals()))
        options = session.packet('pilot:'+seat, full=True)['decision']['ctx']['options']
        result = session.act('pilot:'+seat, {'action_id':'choose', 'replacement':options[0]['id']})
        self.assertTrue(result.ok, result.summary)

    def test_actual_lethal_damage_choice_rolls_back_wrong_principals_and_replays_from_before_cast(self):
        session = self.session(750200)
        target = self.add(session.engine, 'Grizzly Bears', seat='B')
        sources = self.replacements(session)
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        spell = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.cast(session, spell, [target.ref], {'R':1})
        self.assertEqual('replacement.order', self.resolve(session).kind)
        self.assertEqual('state_based_zone_change', session.state.pending_decision.continuation['replacement_resume_kind'])
        self.assertEqual(('battlefield',3), (session.state.cards[target.object_id].zone, session.state.cards[target.object_id].marked_damage))
        for seat in 'ACD':self.assertIsNone(session.packet('pilot:'+seat, full=True)['decision'])
        session = self.replay(session, load=True)
        options = session.packet('pilot:B', full=True)['decision']['ctx']['options']
        for principal, value in (('pilot:A', options[0]['id']), ('pilot:B', 'not-an-offered-replacement')):
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act(principal, {'action_id':'choose', 'replacement':value}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        # A forged saved frame cannot authorize a choice for changed public facts.
        session.state.cards[target.object_id].marked_damage += 1
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id':'choose', 'replacement':options[0]['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session.state.cards[target.object_id].marked_damage -= 1
        self.choose(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile',1), (session.state.cards[target.object_id].zone, session.state.cards[target.object_id].counters.get('void',0)))
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.assertEqual(0, session.state.players['A'].mana_pool.get('R',0))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in sources))
        for seat in 'ABD':self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C',full=True)))
        self.replay(session, load=True)

    def test_zero_single_replacement_and_indestructible_are_distinct_native_results(self):
        for index, (name, count, expected) in enumerate((
                ('Grizzly Bears',0,'graveyard'), ('Grizzly Bears',1,'exile'), ('Darksteel Myr',2,'battlefield'))):
            with self.subTest(name=name, replacements=count):
                session = self.session(750210+index)
                target = self.add(session.engine, name, seat='B')
                self.replacements(session, count)
                spell = self.add(session.engine, 'Lightning Bolt', zone='hand')
                self.cast(session, spell, [target.ref], {'R':1})
                self.assertIsNone(self.resolve(session))
                self.assertEqual(expected, session.state.cards[target.object_id].zone)
                self.assertEqual(int(expected=='exile'), session.state.cards[target.object_id].counters.get('void',0))
                self.replay(session)

    def test_actual_zero_toughness_destination_choice_does_not_require_destruction(self):
        session = self.session(750220)
        target = self.add(session.engine, 'Grizzly Bears', seat='B')
        self.replacements(session)
        spell = self.add(session.engine, 'Disfigure', zone='hand')
        self.cast(session, spell, [target.ref], {'B':1})
        self.assertEqual('replacement.order', self.resolve(session).kind)
        self.assertEqual(0, session.engine._numeric_stat(target.object_id,'toughness'))
        self.assertEqual(0, session.state.cards[target.object_id].marked_damage)
        session = self.replay(session, load=True)
        self.choose(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile',1), (session.state.cards[target.object_id].zone, session.state.cards[target.object_id].counters.get('void',0)))
        self.replay(session)

    def test_actual_simultaneous_damage_retains_replacements_from_sources_that_also_leave(self):
        session = self.session(750230)
        bodies = [self.add(session.engine,'Grizzly Bears',seat=seat,ref='simultaneous-bear-'+seat) for seat in 'BC']
        sources = self.replacements(session)
        spell = self.add(session.engine,'Pyroclasm',zone='hand')
        self.cast(session,spell,[],{'R':1,'C':1})
        self.assertEqual('replacement.order', self.resolve(session).kind)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in (*bodies,*sources)))
        self.choose(session)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(['pilot:C'],list(session.pending_principals()))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in (*bodies,*sources)))
        session=self.replay(session,load=True)
        self.choose(session,seat='C')
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' and session.state.cards[c.object_id].counters.get('void')==1 for c in bodies))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in sources))
        self.replay(session)

    def test_fresh_actual_zone_preflight_omission_is_killed(self):
        method='test_actual_lethal_damage_choice_rolls_back_wrong_principals_and_replays_from_before_cast'
        with patch('quorune.state_based_replacement_coordination.prepare_state_based_zone_replacements', return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_umbra_then_destination_choices_retain_both_domains_before_any_commit(self):
        from quorune.attachments import attach_objects
        from quorune.replacement.replay import ReplacementContinuation
        from quorune.replacement.model import ReplacementEffectError
        session=self.session(750240)
        body=self.add(session.engine,'Grizzly Bears',seat='B')
        auras=[self.add(session.engine,name,seat='B') for name in ('Hyena Umbra','Spider Umbra')]
        # Fixture attachment uses the existing owner; only the damage and both
        # replacement choices under test are actual offered commands.
        for aura in auras:attach_objects(session.state.cards,aura,body,source_timestamp=session.engine._next_zone_timestamp())
        self.replacements(session)
        spell=self.add(session.engine,'Flame Slash',zone='hand')
        self.cast(session,spell,[body.ref],{'R':1})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        legacy=deepcopy(session.state.pending_decision.continuation)
        self.assertEqual('state_based_destruction',legacy['replacement_resume_kind'])
        self.assertNotIn('state_based_destruction_selections',legacy)
        self.assertEqual((),ReplacementContinuation.from_dict(legacy).state_based_destruction_selections)
        options=session.packet('pilot:B',full=True)['decision']['ctx']['options']
        selected=next(row['id'] for row in options if row['source']==auras[0].object_id)
        result=session.act('pilot:B',{'action_id':'choose','replacement':selected});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        value=deepcopy(session.state.pending_decision.continuation)
        self.assertEqual('state_based_zone_change',value['replacement_resume_kind'])
        self.assertEqual(1,len(value['state_based_destruction_selections']))
        self.assertEqual(4,session.state.cards[body.object_id].marked_damage)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in auras))
        self.assertEqual('state_based_zone_change',ReplacementContinuation.from_dict(value).resume_kind)
        for changes in ({'extra':0},{'state_based_destruction_selections':True},{'replacement_resume_kind':'state_based_destruction'}):
            with self.assertRaises(ReplacementEffectError):ReplacementContinuation.from_dict({**value,**changes})
        missing=deepcopy(value);del missing['state_based_destruction_selections']
        with self.assertRaises(ReplacementEffectError):ReplacementContinuation.from_dict(missing)
        mismatched=deepcopy(missing);mismatched['replacement_resume_kind']='state_based_destruction'
        with self.assertRaises(ReplacementEffectError):ReplacementContinuation.from_dict(mismatched)
        session=self.replay(session,load=True)
        self.choose(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield',0),(session.state.cards[body.object_id].zone,session.state.cards[body.object_id].marked_damage))
        self.assertEqual(('exile',1),(session.state.cards[auras[0].object_id].zone,session.state.cards[auras[0].object_id].counters.get('void',0)))
        self.assertEqual('battlefield',session.state.cards[auras[1].object_id].zone)
        self.replay(session)

    def test_fresh_actual_prior_destruction_selection_omission_is_killed(self):
        from dataclasses import replace
        from quorune.state_based_replacement_coordination import resume_state_based_replacement
        method='test_actual_umbra_then_destination_choices_retain_both_domains_before_any_commit'
        def omitted(host,restored,selection,*,error_type):
            return resume_state_based_replacement(host,replace(restored,state_based_destruction_selections=()),selection,error_type=error_type)
        with patch('quorune.state_based_replacement_coordination.resume_state_based_replacement',omitted):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

from __future__ import annotations

import unittest,json,tempfile
from pathlib import Path
from common import ROOT
import test_conditional_copy_values as controls


class CopyReplacementOccurrenceTests(unittest.TestCase):
    def test_occurrence_is_optional_closed_and_consumes_all_candidate_alternatives(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass,ReplaceableEvent
        from quorune.replacement.operations import SetField
        from quorune.replacement.applicability import replacement_choice
        from quorune.replacement.application import apply_replacement
        base=ReplacementEffect('first','source','zone.change',ReplacementClass.ENTERS_COPY,operations=(SetField('entry_copy_source','first'),),optional=True)
        self.assertNotIn('occurrence_id',base.to_dict())
        first=__import__('dataclasses').replace(base,occurrence_id='same-ability')
        second=__import__('dataclasses').replace(first,effect_id='second')
        self.assertEqual(first,ReplacementEffect.from_dict(json.loads(json.dumps(first.to_dict()))))
        event=ReplaceableEvent('event','zone.change','A',{'destination':'battlefield','entry_copy_source':None})
        choice=replacement_choice(event,(first,second));self.assertEqual(2,len(choice.options))
        applied=apply_replacement(choice,(first,second),'first')
        self.assertIsNone(replacement_choice(applied,(first,second)))
        declined=apply_replacement(choice,(first,second),'decline:first')
        self.assertIsNone(replacement_choice(declined,(first,second)))
        for value in ('',True,[]):
            with self.assertRaises(ValueError):__import__('dataclasses').replace(base,occurrence_id=value)
        with self.assertRaises(ValueError):
            __import__('dataclasses').replace(first,replacement_class=ReplacementClass.OTHER)

    def test_different_occurrence_can_apply_after_form_change_but_same_one_cannot(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass,ReplaceableEvent
        from quorune.replacement.operations import SetField
        from quorune.replacement.applicability import replacement_choice
        from quorune.replacement.application import apply_replacement
        first=ReplacementEffect('a','source','zone.change',ReplacementClass.ENTERS_COPY,
            conditions={'entry_copy_form':{'eq':None}},operations=(SetField('entry_copy_form','copier'),),occurrence_id='ability-a')
        second=ReplacementEffect('b','source','zone.change',ReplacementClass.ENTERS_COPY,
            conditions={'entry_copy_form':{'eq':'copier'}},operations=(SetField('entry_copy_form','final'),),occurrence_id='ability-b')
        event=ReplaceableEvent('e','zone.change','A',{})
        event=apply_replacement(replacement_choice(event,(first,second)),(first,second),'a')
        self.assertEqual(('b',),replacement_choice(event,(first,second)).options)
        event=apply_replacement(replacement_choice(event,(first,second)),(first,second),'b')
        self.assertIsNone(replacement_choice(event,(first,second)))

    def test_shared_occurrence_omission_is_killed_by_alternative_consumption(self):
        from dataclasses import replace
        from unittest.mock import patch
        import quorune.replacement.applicability as owner
        original=owner.canonical_effects
        def omit(effects):
            return tuple(replace(effect,occurrence_id=None) for effect in original(effects))
        method='test_occurrence_is_optional_closed_and_consumes_all_candidate_alternatives'
        with patch.object(owner,'canonical_effects',side_effect=omit),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


class NestedEntryCopyRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ConditionalCopyRuntimeTests.session
    add=controls.ConditionalCopyRuntimeTests.add
    prepare=controls.ConditionalCopyRuntimeTests.prepare
    cast=controls.ConditionalCopyRuntimeTests.cast
    resolve=controls.ConditionalCopyRuntimeTests.resolve
    replay=controls.ConditionalCopyRuntimeTests.replay
    checkpoint=controls.ConditionalCopyRuntimeTests.checkpoint
    choose_copy=controls.ConditionalCopyRuntimeTests.choose_copy
    assert_closed=controls.ConditionalCopyRuntimeTests.assert_closed
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'nested-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/conditional-copy-value-cards.json',ROOT/'tests/fixtures/copied-self-entry-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Nested copy',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fixture(self,seed,*,token=False):
        s=self.session(seed);e=s.engine
        copier=self.add(e,'Vizier of Many Faces',ref='uncopied-candidate');copier.counters['+1/+1']=1
        target=self.add(e,'Elvish Visionary',ref='nested-final-source')
        source=self.add(e,'Cackling Counterpart' if token else 'Clone',zone='hand',ref='nested-entering-source')
        self.prepare(s,mana={'U':8,'C':8});self.checkpoint(s)
        return s,source,copier,target

    def choose_current(self,s,target):
        decision=s.state.pending_decision;self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        rows=[r for r in options if target.ref in r['id'] and not r['id'].startswith('decline:')]
        self.assertTrue(rows,[(r['id'],r.get('label')) for r in options])
        result=s.act('pilot:A',{'action_id':'choose','replacement':rows[0]['id']});self.assertTrue(result.ok,result.summary)
        return self.resolve(s)

    def test_unselected_copier_candidate_does_not_break_safe_clone_choice(self):
        s,source,copier,target=self.fixture(707270)
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNone(self.choose_current(s,target))
        self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id))
        self.assertEqual('battlefield',s.state.cards[copier.object_id].zone);self.replay(s,load=True)

    def test_ordinary_token_copy_of_uncopied_vizier_acquires_entry_choice_without_embalm(self):
        s,source,copier,target=self.fixture(707271,token=True)
        self.cast(s,'A',source,[copier.ref]);self.assertIsNotNone(self.resolve(s))
        self.assertIsNone(self.choose_current(s,target))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens))
        data=s.engine._effective_card_data(tokens[0])
        self.assertEqual(('{1}{G}',2,['G']),(data['mana_cost'],data['mana_value'],data['colors']))
        self.assertNotIn('zombie',s.engine._type_parts(data['type_line'])[1]);self.replay(s,load=True)

    def test_chosen_vizier_copy_acquires_second_copy_before_only_final_entry_draw(self):
        from quorune.record import authoritative_state_hash
        s,source,copier,target=self.fixture(707272)
        private={seat:self.add(s.engine,'Generic Bound Body',seat=seat,zone='hand',ref='nested-private-hand-'+seat) for seat in 'ABCD'}
        self.assert_closed(s,source);self.assert_closed(s,copier);self.checkpoint(s)
        before=len(s.state.players['A'].zones['hand'])
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNotNone(self.choose_current(s,copier))
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True)
        option=next(row['id'] for row in s.packet('pilot:A',full=True)['decision']['ctx']['options'] if target.ref in row['id'] and not row['id'].startswith('decline:'))
        unchanged=authoritative_state_hash(s.state)
        rejected=s.act('pilot:C',{'action_id':'choose','replacement':option})
        self.assertFalse(rejected.ok);self.assertEqual(unchanged,authoritative_state_hash(s.state))
        for viewer in 'ABCD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            self.assertIn(private[viewer].ref,packet)
            for seat in 'ABCD':
                if seat!=viewer:self.assertNotIn(private[seat].ref,packet)
            self.assertNotIn('entry_copy_form',packet)
        self.assertIsNone(self.choose_current(s,target))
        self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id))
        self.assertEqual(before,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)


    def test_declining_acquired_copy_occurrence_does_not_offer_another_alternative_again(self):
        s,source,copier,target=self.fixture(707273)
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNotNone(self.choose_current(s,copier))
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        decline=next(row for row in options if row['id'].startswith('decline:'))
        result=s.act('pilot:A',{'action_id':'choose','replacement':decline['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_reacquired_same_copier_occurrence_terminates_without_repeating_forever(self):
        s,source,copier,target=self.fixture(707274)
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNotNone(self.choose_current(s,copier))
        self.assertIsNone(self.choose_current(s,copier))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_unchosen_counter_producer_does_not_leak_and_nested_final_counter_applies(self):
        s,source,copier,target=self.fixture(707275)
        counter=self.add(s.engine,'Generic Copied Counter Creature',seat='B',ref='nested-counter-source')
        counter.counters['+1/+1']=5;self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNone(self.choose_current(s,target))
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.replay(s,load=True)
        s,source,copier,target=self.fixture(707276)
        counter=self.add(s.engine,'Generic Copied Counter Creature',seat='B',ref='nested-final-counter-source')
        counter.counters['+1/+1']=5;self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNotNone(self.resolve(s))
        self.assertIsNotNone(self.choose_current(s,copier));self.assertIsNone(self.choose_current(s,counter))
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(s,load=True)

    def test_acquired_copy_and_final_counter_producer_omissions_fail_native_results(self):
        from unittest.mock import patch
        import quorune.semantic_runtime.entry_copy as owner
        original=owner._copy_programs
        def omit_acquired(host,source,subject):
            return () if source.annotations.get('copy_overrides') else original(host,source,subject)
        method='test_chosen_vizier_copy_acquires_second_copy_before_only_final_entry_draw'
        with patch.object(owner,'_copy_programs',side_effect=omit_acquired),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()
        method='test_unchosen_counter_producer_does_not_leak_and_nested_final_counter_applies'
        with patch.object(owner,'_copied_entry_effects',return_value=()),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()

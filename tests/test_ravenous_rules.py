"""Pinned CR107.3m/702.156: X entry counters and independent draw threshold."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='keyword.ravenous.entry_x_and_draw'
TEXT='Ravenous (This creature enters with X +1/+1 counters on it. If X is 5 or more, draw a card when it enters.)'


class RavenousCompilerTests(unittest.TestCase):
    def test_verified_ravenous_withdrawal_rejects_both_components_and_keeps_plain_x_entry(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Ravenous compound contract withdrawn'])
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,keywords,expected in ((TEXT,('Ravenous',),False),('This creature enters with X +1/+1 counters on it.',(),True)):
            ir=compile_oracle_card(replace(query_record(text),mana_cost='{X}{G}',type_line='Creature — Beast',keywords=keywords),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')
    def test_keyword_lowers_both_generic_components_with_source_span(self):
        record=replace(query_record(TEXT),mana_cost='{X}{G}',type_line='Creature — Beast',power='0',toughness='0',keywords=('Ravenous',))
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=ir.faces[0].nodes;self.assertEqual(2,len(nodes))
        self.assertEqual({'zone.change','permanent.enter.self'},{node.event for node in nodes})
        for node in nodes:self.assertEqual(TEXT,node.text);self.assertIn(CAPABILITY,node.capability_dependencies)

    def test_noncreature_and_unrepresented_keyword_parameter_remain_residual(self):
        for text,kind,cost in (('Ravenous 2','Creature — Beast','{X}{G}'),(TEXT,'Sorcery','{X}{G}'),(TEXT,'Creature — Beast','{1}{G}')):
            ir=compile_oracle_card(replace(query_record(text),type_line=kind,mana_cost=cost,keywords=('Ravenous',)),
                capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)


class RavenousModelTests(unittest.TestCase):
    def test_entry_x_codec_threshold_and_runtime_envelope_are_closed(self):
        from types import SimpleNamespace
        from quorune.ravenous_model import EntryXInformation,ENTRY_X_FIELD,ravenous_condition,ravenous_runtime_dependencies
        from quorune.rules.ravenous import ravenous_condition_value
        source=SimpleNamespace(object_id='object1',logical_object_id='object1@2',counters={'+1/+1':100})
        for amount,expected in ((0,False),(4,False),(5,True),(50,True)):
            info=EntryXInformation('object1','object1@1',amount)
            self.assertEqual(info,EntryXInformation.from_dict(info.to_dict()))
            self.assertEqual(expected,ravenous_condition_value(ravenous_condition(),source,{ENTRY_X_FIELD:info.to_dict()}))
        raw=EntryXInformation('object1','object1@1',5).to_dict()
        for change in ({'amount':True},{'amount':-1},{'schema_version':True},{'extra':1}):
            with self.assertRaises(ValueError):EntryXInformation.from_dict({**raw,**change})
        p=SimpleNamespace(event_condition=ravenous_condition(),event='permanent.enter.self',active_zone='battlefield',coverage=('ravenous_entry_x','intervening_condition'),target_schema=None,effects=({'op':'draw','player':'$controller','count':1,'private':True},))
        self.assertEqual((CAPABILITY,),ravenous_runtime_dependencies(p))
        p.event='creature.dies.self'
        with self.assertRaises(ValueError):ravenous_runtime_dependencies(p)

    def test_zone_occurrence_validates_entry_x_identity_and_keeps_legacy_absence(self):
        from test_zone_trigger_events import occurrence
        from quorune.zone_trigger_events import normalized_zone_trigger_events,ZoneTriggerEventError
        from quorune.ravenous_model import EntryXInformation,ENTRY_X_FIELD
        legacy=occurrence();self.assertNotIn(ENTRY_X_FIELD,legacy.to_dict())
        facts=EntryXInformation('object-1','object-1@1',5).to_dict()
        value=occurrence(origin='stack',previous_logical_object_id='object-1@0',current_logical_object_id='object-1@1',source_entry_x_information=facts)
        entered=next(event for event in normalized_zone_trigger_events(value) if event.kind=='permanent.enter')
        self.assertEqual(facts,dict(entered.context[ENTRY_X_FIELD]))
        for delta in ({'current_logical_object_id':'object-1@2'},{'origin':'graveyard'},{'destination':'hand'}):
            with self.assertRaises(ZoneTriggerEventError):replace(value,**delta)

    def test_substituting_counter_count_for_cast_x_is_killed(self):
        from unittest.mock import patch
        from quorune.rules import ravenous as owner
        original=owner.EntryXInformation.from_dict
        def substitute(value):return replace(original(value),amount=100)
        method='test_entry_x_codec_threshold_and_runtime_envelope_are_closed'
        baseline=unittest.TestResult();RavenousModelTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner.EntryXInformation,'from_dict',side_effect=substitute):
            result=unittest.TestResult();RavenousModelTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class RavenousNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ravenous.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/ravenous-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ravenous',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def cast_x(self,s,card,x):
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in rows if row.get('card')==card.ref)
        command={'action_id':action['id'],'x':x,'pay':'auto'}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state));result=s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)

    def test_actual_x_threshold_is_not_doubled_entry_counter_count_and_replays(self):
        for x,doubled in ((4,True),(5,False)):
            with self.subTest(x=x,doubled=doubled):
                s=self.session(702156200+x)
                source=self.add(s.engine,'Aberrant',zone='hand')
                if doubled:self.add(s.engine,'Hardened Scales')
                self.assert_closed(s,source);self.prepare(s,mana={'G':1,'C':x+1});self.checkpoint(s)
                initial_hand=set(s.state.players['A'].zones['hand']);hand=len(initial_hand);self.cast_x(s,source,x)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertEqual(x+int(doubled),current.counters.get('+1/+1',0))
                self.assertEqual(hand-1+int(x>=5),len(s.state.players['A'].zones['hand']))
                drawn=set(s.state.players['A'].zones['hand'])-initial_hand
                for seat in 'BCD':
                    packet=json.dumps(s.packet('pilot:'+seat,full=True))
                    for uid in drawn:self.assertNotIn(s.state.cards[uid].ref,packet)
                self.replay(s,load=True)

    def test_actual_blink_uses_zero_x_but_old_draw_survives_source_departure(self):
        s=self.session(702156210)
        source=self.add(s.engine,'Aberrant',zone='hand')
        blink=self.add(s.engine,'Cloudshift',zone='hand')
        land=self.add(s.engine,'Plains')
        self.assert_closed(s,source);self.assert_closed(s,blink)
        self.prepare(s,mana={'G':1,'C':6});self.checkpoint(s)
        self.cast_x(s,source,5);self.resolve_top(s)
        self.assertEqual(1,len(s.state.stack));old=s.state.cards[source.object_id].logical_object_id
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
        self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        hand=len(s.state.players['A'].zones['hand']);self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
        current=s.state.cards[source.object_id]
        self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual(0,current.counters.get('+1/+1',0))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_actual_permanent_spell_copy_retains_x_without_being_cast(self):
        s=self.session(702156211)
        observer=self.add(s.engine,'Reflections of Littjara');observer.annotations['chosen_creature_type']='tyranid'
        source=self.add(s.engine,'Aberrant',zone='hand')
        self.assert_closed(s,observer);self.assert_closed(s,source)
        self.prepare(s,mana={'G':1,'C':6});self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand']);casts=len(s.engine._current_turn_history('spell_cast'))
        self.cast_x(s,source,5);self.resolve_top(s)
        copies=[item for item in s.state.stack if item.kind=='spell_copy'];self.assertEqual(1,len(copies))
        self.assertEqual(5,copies[0].x_value)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        creatures=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.printed_name=='Aberrant']
        self.assertEqual(2,len(creatures));self.assertEqual(1,sum(card.is_token for card in creatures))
        self.assertTrue(all(card.counters.get('+1/+1')==5 for card in creatures))
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual(casts+1,len(s.engine._current_turn_history('spell_cast')))
        self.replay(s,load=True)

    def test_actual_hormagaunt_return_requires_printed_optional_payment(self):
        for pay in (False,True):
            with self.subTest(pay=pay):
                s=self.session(702156220+int(pay))
                source=self.add(s.engine,'Hormagaunt Horde',zone='graveyard')
                land=self.add(s.engine,'Forest',zone='hand')
                self.assert_closed(s,source);self.prepare(s,mana={'G':1,'C':2});self.checkpoint(s)
                actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                offered=next(row for row in actions if row.get('card')==land.ref and row.get('kind')=='play_land')
                self.assertTrue(s.act('pilot:A',{'action_id':offered['id']}).ok)
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',{'action_id':'choose','pay':True}).ok)
                self.assertEqual(before,authoritative_state_hash(s.state));s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','pay':pay});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('hand' if pay else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual(0 if pay else 3,sum(s.state.players['A'].mana_pool.values()))
                self.replay(s,load=True)

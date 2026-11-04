"""Pinned CR107.3m: self-entry X is captured from the resolving spell."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='quantity_expression.source_entry_x'


class EntryXResultCompilerTests(unittest.TestCase):
    def test_unrelated_source_text_does_not_construct_an_entry_binding(self):
        from quorune.compiler.entry_x_results import entry_x_trigger_effect_compiler
        def ordinary(*args,**kwargs):return 'existing compiler'
        def unexpected(*args,**kwargs):self.fail('Unrelated Oracle text reached the entry-X source matcher')
        for text,cost in (('When this creature enters, draw a card.','{2}{G}'),('Create X 1/1 green Saproling creature tokens.','{1}{G}')):
            record=replace(query_record(text),mana_cost=cost)
            self.assertIs(ordinary,entry_x_trigger_effect_compiler(record,text,card_name=record.name,ordinary=ordinary,match_source=unexpected))
    def test_verified_entry_x_withdrawal_keeps_ordinary_spell_x_and_plain_entry_damage(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Entry X result owner withdrawn'])
        registry=CapabilityRegistry(raw);declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,kind,cost,expected in (('When this creature enters, it deals X damage to any target.','Creature — Beast','{X}{R}',False),
                ('When this creature enters, it deals 2 damage to any target.','Creature — Beast','{2}{R}',True),
                ('This spell deals X damage to any target.','Sorcery','{X}{R}',True)):
            ir=compile_oracle_card(replace(query_record(text),mana_cost=cost,type_line=kind),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact',(text,ir.material_residuals))
    def test_entry_x_lowers_only_existing_result_slots(self):
        texts=('When this creature enters, it deals X damage to any target.',
            'When this creature enters, he deals X damage to target opponent.',
            'When this creature enters, create X 2/2 white Soldier creature tokens.',
            'When this enchantment enters, each creature gets -X/-X until end of turn.',
            'When this enchantment enters, create an X/X green Treefolk creature token.')
        registry=load_default_capability_registry()
        for text in texts:
            with self.subTest(text=text):
                record=replace(query_record(text),mana_cost='{X}{G}',type_line='Enchantment Creature — Beast',power='1',toughness='1')
                ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual('permanent.enter.self',node.event)
                self.assertIn(CAPABILITY,node.capability_dependencies)
                self.assertIn('source_entry_x_amount',str(node.effects))

    def test_unbound_other_events_locally_defined_x_and_new_target_grammar_do_not_gain_entry_x(self):
        for text,cost in (('When this creature dies, it deals X damage to any target.','{X}{G}'),
                ('Whenever this creature attacks, create X 1/1 green Saproling creature tokens.','{X}{G}'),
                ('When this creature enters, it deals X damage to any target.','{1}{G}'),
                ('When this creature enters, distribute X +1/+1 counters among any number of target creatures.','{X}{G}')):
            ir=compile_oracle_card(replace(query_record(text),mana_cost=cost),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)


class EntryXAmountModelTests(unittest.TestCase):
    def test_sealed_original_entry_is_independent_of_trigger_x_counters_and_current_source(self):
        from types import SimpleNamespace
        from quorune.entry_x_amount_model import EntryXAmountSpec,resolve_entry_x_amount,entry_x_amount_runtime_dependencies
        from quorune.ravenous_model import EntryXInformation,ENTRY_X_FIELD
        facts=EntryXInformation('source1','source1@2',4).to_dict()
        item=SimpleNamespace(source_object_id='source1',x_value=99,context={'event':'permanent.enter','source_logical_object_id':'source1@2',ENTRY_X_FIELD:facts})
        for coefficient in (-3,-2,-1,1,2,3):
            spec=EntryXAmountSpec(coefficient);self.assertEqual(spec,EntryXAmountSpec.from_dict(spec.to_dict()))
            self.assertEqual(4*coefficient,resolve_entry_x_amount(spec.to_dict(),item))
        item.context[ENTRY_X_FIELD]=EntryXInformation('source1','source1@2',0).to_dict()
        self.assertEqual(0,resolve_entry_x_amount(EntryXAmountSpec().to_dict(),item))
        for delta in ({'source_logical_object_id':'source1@3'},{'event':'creature.dies'},{ENTRY_X_FIELD:None}):
            changed=SimpleNamespace(**{**vars(item),'context':{**item.context,**delta}})
            with self.assertRaises(ValueError):resolve_entry_x_amount(EntryXAmountSpec().to_dict(),changed)
        effect={'op':'draw','player':'$controller','private':True,'count':EntryXAmountSpec().to_dict()}
        p=SimpleNamespace(event='permanent.enter.self',active_zone='battlefield',effects=(effect,))
        self.assertEqual((CAPABILITY,),entry_x_amount_runtime_dependencies(p))
        p.event='activate'
        with self.assertRaises(ValueError):entry_x_amount_runtime_dependencies(p)

    def test_codec_and_projection_reject_coercion_unknown_fields_and_nonresult_slots(self):
        from quorune.entry_x_amount_model import EntryXAmountSpec
        from quorune.compiler.declared_effect_amounts import declared_amount_shape_context
        value=EntryXAmountSpec().to_dict()
        for delta in ({'coefficient':True},{'coefficient':0},{'schema_version':True},{'extra':1}):
            with self.assertRaises(ValueError):EntryXAmountSpec.from_dict({**value,**delta})
        self.assertIsNone(declared_amount_shape_context(({'op':'draw','player':value,'count':1,'private':True},),{'declared-effect-amount','source-entry-x-result-amount'}))

    def test_substituting_trigger_stack_x_for_entry_x_is_killed(self):
        from unittest.mock import patch
        from quorune import entry_x_amount_model as owner
        def wrong(value,item):return owner.EntryXAmountSpec.from_dict(value).coefficient*item.x_value
        method='test_sealed_original_entry_is_independent_of_trigger_x_counters_and_current_source'
        result=unittest.TestResult();EntryXAmountModelTests(method).run(result);self.assertTrue(result.wasSuccessful(),result.errors+result.failures)
        with patch.object(owner,'resolve_entry_x_amount',side_effect=wrong):
            result=unittest.TestResult();EntryXAmountModelTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class EntryXResultNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'entry-x-results.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/entry-x-result-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Entry X results',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def cast_x(self,s,card,x):
        self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in actions if r.get('card')==card.ref)
        command={'action_id':row['id'],'x':x,'pay':'auto'};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_zoanthrope_retains_entry_x_after_source_return_and_counter_replacement(self):
        s=self.session(107300201)
        source=self.add(s.engine,'Zoanthrope',zone='hand');self.add(s.engine,'Hardened Scales')
        bounce=self.add(s.engine,'Unsummon',zone='hand');land=self.add(s.engine,'Island')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,bounce)
        self.prepare(s,mana={'U':1,'R':1,'C':4});self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand']);self.cast_x(s,source,4)
        self.target(s,'A',['B']);self.assertEqual(5,s.state.cards[source.object_id].counters['+1/+1'])
        s=self.replay(s,load=True);self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(r for r in actions if r.get('mana_ability') and land.ref in r['id'])
        self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'A',bounce,[source.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(36,s.state.players['B'].life)
        self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_zoanthrope_spell_copy_and_original_use_the_same_announced_x(self):
        s=self.session(107300202)
        observer=self.add(s.engine,'Reflections of Littjara');observer.annotations['chosen_creature_type']='tyranid'
        source=self.add(s.engine,'Zoanthrope',zone='hand')
        self.assert_closed(s,observer);self.assert_closed(s,source)
        self.prepare(s,mana={'U':1,'R':1,'C':3});self.checkpoint(s)
        self.cast_x(s,source,3);self.resolve_top(s)
        copies=[item for item in s.state.stack if item.kind=='spell_copy'];self.assertEqual(1,len(copies));self.assertEqual(3,copies[0].x_value)
        self.target(s,'A',['B']);s=self.replay(s,load=True)
        self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        sources=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.printed_name=='Zoanthrope']
        self.assertEqual(2,len(sources));self.assertEqual(1,sum(c.is_token for c in sources))
        self.assertEqual((37,37),(s.state.players['B'].life,s.state.players['C'].life))
        self.assertEqual(1,len(s.engine._current_turn_history('spell_cast')))
        self.replay(s,load=True)

    def test_actual_treefolk_stats_and_uncast_gadwick_entry_use_sealed_x_and_zero(self):
        s=self.session(107300203)
        tree=self.add(s.engine,'Arboreal Alliance',zone='hand')
        self.assert_closed(s,tree);self.prepare(s,mana={'G':2,'C':3});self.checkpoint(s)
        self.cast_x(s,tree,3);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.printed_name=='Treefolk']
        self.assertEqual(1,len(tokens));self.assertEqual((3,3),tuple(s.engine._numeric_stat(tokens[0].object_id,stat) for stat in ('power','toughness')))
        self.replay(s,load=True)
        s=self.session(107300204)
        source=self.add(s.engine,'Gadwick, the Wizened',zone='hand');blink=self.add(s.engine,'Cloudshift',zone='hand')
        land=self.add(s.engine,'Plains');self.assert_closed(s,source);self.assert_closed(s,blink)
        self.prepare(s,mana={'U':3,'C':2});self.checkpoint(s)
        initial=set(s.state.players['A'].zones['hand']);self.cast_x(s,source,2)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(len(initial)+1,len(s.state.players['A'].zones['hand']))
        drawn=set(s.state.players['A'].zones['hand'])-initial
        for seat in 'BCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for uid in drawn:self.assertNotIn(s.state.cards[uid].ref,packet)
        self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(r for r in actions if r.get('mana_ability') and land.ref in r['id']);self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        hand=len(s.state.players['A'].zones['hand']);old=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',blink,[source.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']));self.replay(s,load=True)

    def test_actual_meathook_negative_entry_x_changes_whole_set_before_death_triggers(self):
        s=self.session(107300205)
        source=self.add(s.engine,'The Meathook Massacre',zone='hand')
        own=self.add(s.engine,'Grizzly Bears');other=self.add(s.engine,'Grizzly Bears',seat='B',ref='meathook-other')
        self.assert_closed(s,source);self.prepare(s,mana={'B':2,'C':2});self.checkpoint(s)
        self.cast_x(s,source,2);self.assertEqual('trigger.order',self.resolve(s).kind)
        self.assertEqual(['graveyard','graveyard'],[s.state.cards[c.object_id].zone for c in (own,other)])
        context=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(2,len(context['triggers']))
        command={'action_id':'order','triggers':[r['id'] for r in context['triggers']]}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',command).ok);self.assertIsNone(self.resolve(s))
        self.assertEqual([41,39,39,39],[s.state.players[seat].life for seat in 'ABCD']);self.replay(s,load=True)

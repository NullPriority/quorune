from __future__ import annotations

"""CR614.12: existing sources modify prospective entry before counters/events."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_classic_flash_casts as controls
from quorune.rules.evidence_storage import read_evidence_document


class ExternalEntryCounterTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ClassicFlashCastTests.session
    add=controls.ClassicFlashCastTests.add
    prepare=controls.ClassicFlashCastTests.prepare
    checkpoint=controls.ClassicFlashCastTests.checkpoint
    resolve=controls.ClassicFlashCastTests.resolve
    replay=controls.ClassicFlashCastTests.replay
    binding=controls.ClassicFlashCastTests.binding
    priority=controls.ClassicFlashCastTests.priority
    cast=controls.ClassicFlashCastTests.cast
    offered_cast=controls.ClassicFlashCastTests.offered_cast
    private_replay=controls.ClassicFlashCastTests.private_replay
    advance=controls.ClassicFlashCastTests.advance
    offered=controls.ClassicFlashCastTests.offered

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'external-entry-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/external-entry-counter-cards.json',ROOT/'tests/fixtures/counter-replacement-cards.json',
            ROOT/'tests/fixtures/entry-copy-cards.json',ROOT/'tests/fixtures/classic-flash-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('External entry counters',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def finish_entry(self,session):
        decision=self.resolve(session)
        while decision is not None:
            self.assertEqual('replacement.order',decision.kind)
            ctx=session.packet(session.pending_principals()[0],full=True)['decision']['ctx']
            options=[r for r in ctx['options'] if not r['id'].startswith('decline:')]
            self.assertGreater(len(options),0)
            session=self.replay(session,load=True)
            result=session.act(session.pending_principals()[0],{'action_id':'choose','replacement':options[0]['id']});self.assertTrue(result.ok,result.summary)
            decision=self.resolve(session)
        return session

    def test_external_entry_counter_value_model_and_whole_source_grammar_are_closed(self):
        from quorune.external_entry_counter_model import ExternalEntryCounterSpec,ExternalEntryCounterSourceSnapshot
        from quorune.compiler.external_entry_counters import external_entry_counter_handler
        spec=ExternalEntryCounterSpec(required_subtype='warrior')
        self.assertEqual(spec,ExternalEntryCounterSpec.from_dict(spec.to_dict()))
        for changed in ({**spec.to_dict(),'schema_version':True},{**spec.to_dict(),'amount_source':'opponent_power'},
                {**spec.to_dict(),'required_subtype':'UnknownType'},{**spec.to_dict(),'add_mutant':True},{**spec.to_dict(),'extra':1}):
            with self.assertRaises(ValueError):ExternalEntryCounterSpec.from_dict(changed)
        snapshot=ExternalEntryCounterSourceSnapshot('source','source@1','A',-2)
        self.assertEqual(0,snapshot.nonnegative_amount)
        for text in ('Each other creature you control enters with two additional +1/+1 counters on it.',
                'Each other creature you control enters with an additional +1/+1 counter on it until end of turn.',
                'Each creature an opponent controls enters with an additional +1/+1 counter on it.',
                'Each other creature you control enters with an additional +1/+1 counter on it. Draw a card.'):
            self.assertIsNone(external_entry_counter_handler(text,source_name='Generic Bound Commander'),text)
        for name in ('Grumgully, the Generous','Renata, Called to the Hunt','Bramblewood Paragon','Sage of Fables',
                'Arwen, Weaver of Hope','Bloodspore Thrinax','Master Biomancer'):
            source=self.db.lookup(name)
            lowered=[external_entry_counter_handler(line,source_name=name) for line in source.oracle_text.splitlines()]
            self.assertEqual(1,sum(result is not None for result in lowered),name)

    def test_runtime_source_snapshot_lowers_owned_destination_and_subtype_predicates(self):
        from quorune.external_entry_counter_model import ExternalEntryCounterSourceSnapshot
        from quorune.compiler.external_entry_counters import external_entry_counter_handler
        from quorune.semantic_runtime.external_entry_counters import ExternalEntryCounterHandler
        from quorune.replacement.model import ReplaceableEvent,AffectedObject
        from quorune.replacement.applicability import condition_matches
        descriptor=external_entry_counter_handler(self.db.lookup('Grumgully, the Generous').oracle_text,source_name='Grumgully, the Generous')[1]
        handler=ExternalEntryCounterHandler();snapshot=ExternalEntryCounterSourceSnapshot('giver','giver@1','A',1)
        effect=handler.source_replacement_effect(descriptor,source_ref='giver',source_controller='A',component_id='entry-rule',source_context=snapshot)
        def event(types,controller='A',ref='entrant'):
            return ReplaceableEvent(event_id='entry-event',kind='zone.change',affected_player=None,
                affected_object=AffectedObject('entrant-id','D',controller),payload={'destination':'battlefield',
                    'destination_controller':controller,'object_ref':ref,'object_types':types})
        self.assertTrue(condition_matches(effect.conditions,event(['creature','bear'])))
        self.assertFalse(condition_matches(effect.conditions,event(['creature','human'])))
        self.assertFalse(condition_matches(effect.conditions,event(['artifact'])))
        self.assertFalse(condition_matches(effect.conditions,event(['creature','bear'],'B')))
        self.assertFalse(condition_matches(effect.conditions,event(['creature','goblin'],ref='giver')))
        self.assertEqual(1,effect.operations[0].amount)
        with self.assertRaises(ValueError):
            handler.source_replacement_effect(descriptor,source_ref='giver',source_controller='B',component_id='entry-rule',source_context=snapshot)

    def test_additional_counter_preserves_current_instruction_and_applied_history(self):
        from dataclasses import replace
        from quorune.replacement import (AffectedObject,ReplaceableEvent,ReplacementClass,ReplacementEffect,
            CreateAffectedObjectCounter,AddAffectedObjectCounter,MultiplyAmount,GrantAffectedObjectSubtype,
            replacement_choice,apply_replacement,operation_from_dict)
        event=ReplaceableEvent(event_id='entry',kind='zone.change',affected_player=None,
            affected_object=AffectedObject('entrant','D','A'),payload={'destination':'battlefield',
                'destination_controller':'A','object_ref':'entrant-ref','object_types':['creature','bear']})
        def effect(identity,kind,operation):
            return ReplacementEffect(effect_id=identity,source_id=identity,event_kind=kind,
                replacement_class=ReplacementClass.OTHER,operations=(operation,))
        base=effect('base','zone.change',CreateAffectedObjectCounter('+1/+1',1,'A','entrant-ref'))
        extra=effect('extra','zone.change',AddAffectedObjectCounter('+1/+1',1,'A','giver-ref'))
        double=effect('double','counter.place',MultiplyAmount('amount',2))
        def apply(current,selected):
            choice=replacement_choice(current,(selected,));self.assertIsNotNone(choice)
            return apply_replacement(choice,(selected,),selected.effect_id)
        seeded=apply(event,base);self.assertEqual(1,len(seeded.children))
        doubled=apply(seeded.children[0],double)
        after=apply(replace(seeded,children=(doubled,)),extra)
        self.assertEqual(1,len(after.children));self.assertEqual(3,after.children[0].payload['amount'])
        self.assertEqual(('double',),after.children[0].applied_effects)
        self.assertIsNone(replacement_choice(after.children[0],(double,)))
        before=apply(seeded,extra);self.assertEqual(1,len(before.children))
        self.assertEqual(4,apply(before.children[0],double).payload['amount'])
        for operation in (extra.operations[0],GrantAffectedObjectSubtype('mutant')):
            self.assertEqual(operation,operation_from_dict(operation.to_dict()))
            with self.assertRaises(ValueError):operation_from_dict({**operation.to_dict(),'extra':True})
        with self.assertRaises(ValueError):AddAffectedObjectCounter('+1/+1',-1,'A','giver-ref')
        with self.assertRaises(ValueError):GrantAffectedObjectSubtype('UnknownType')

    def test_external_entry_compiler_and_lowerer_omissions_are_killed(self):
        from unittest.mock import patch
        from dataclasses import replace
        from quorune.semantic_runtime.external_entry_counters import ExternalEntryCounterHandler
        from quorune.replacement import AddAffectedObjectCounter
        original=ExternalEntryCounterHandler.source_replacement_effect
        def wrong_amount(handler,*args,**kwargs):
            effect=original(handler,*args,**kwargs)
            return replace(effect,operations=(AddAffectedObjectCounter('+1/+1',2,'A','giver'),))
        method='test_external_entry_counter_value_model_and_whole_source_grammar_are_closed'
        with patch('quorune.compiler.external_entry_counters.external_entry_counter_handler',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_runtime_source_snapshot_lowers_owned_destination_and_subtype_predicates'
        with patch.object(ExternalEntryCounterHandler,'source_replacement_effect',wrong_amount):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_grumgully_excludes_itself_and_human_but_adds_before_nonhuman_entry(self):
        session=self.session(751700);source=self.add(session.engine,'Grumgully, the Generous',zone='hand')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');human=self.add(session.engine,'Generic Bound Body',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Grumgully must admit')
        self.prepare(session,mana={'G':2,'R':1,'W':1,'C':2});self.checkpoint(session)
        self.offered_cast(session,source,{'G':1,'R':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.offered_cast(session,human,{'W':1});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[human.object_id].counters.get('+1/+1',0))
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(1,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.assertEqual((3,3),(session.engine._numeric_stat(bear.object_id,'power'),session.engine._numeric_stat(bear.object_id,'toughness')))
        self.private_replay(session,private)

    def test_actual_renata_retains_devotion_definition_and_counter_bearing_entry(self):
        session=self.session(751701);source=self.add(session.engine,'Renata, Called to the Hunt',zone='hand')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Renata must admit')
        self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':2});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0));self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(1,session.state.cards[bear.object_id].counters.get('+1/+1',0));self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual((3,3),(session.engine._numeric_stat(bear.object_id,'power'),session.engine._numeric_stat(bear.object_id,'toughness')))
        self.private_replay(session,private)

    def test_actual_bramblewood_adds_to_other_warrior_and_current_counter_grants_trample(self):
        session=self.session(751702);first=self.add(session.engine,'Bramblewood Paragon',zone='hand',ref='first-paragon')
        second=self.add(session.engine,'Bramblewood Paragon',zone='hand',ref='second-paragon')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,first)['strict_capability_ready'])
        self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,first,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[first.object_id].counters.get('+1/+1',0))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.offered_cast(session,second,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(1,session.state.cards[second.object_id].counters.get('+1/+1',0))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[bear.object_id]));self.private_replay(session,private)

    def test_actual_sage_adds_to_wizard_then_pays_that_counter_for_private_draw(self):
        from quorune.record import authoritative_state_hash
        session=self.session(751703);sage=self.add(session.engine,'Sage of Fables',zone='hand')
        wizard=self.add(session.engine,'Master Biomancer',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,sage)['strict_capability_ready']);self.assertTrue(self.binding(session,wizard)['strict_capability_ready'])
        self.prepare(session,mana={'U':2,'G':1,'C':6});self.checkpoint(session)
        self.offered_cast(session,sage,{'U':1,'C':2});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[sage.object_id].counters.get('+1/+1',0))
        self.offered_cast(session,wizard,{'U':1,'G':1,'C':2});session=self.finish_entry(session)
        self.assertEqual(1,session.state.cards[wizard.object_id].counters.get('+1/+1',0))
        self.priority(session,'A');actions=[a for a in self.offered(session,'A') if a['id'].startswith('activate:'+sage.ref+':')]
        self.assertEqual(1,len(actions));command={'action_id':actions[0]['id'],'cost_cards':[wizard.ref],'pay':'manual','payment':{'C':2}}
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        count=len(session.state.players['A'].zones['hand']);result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.cards[wizard.object_id].counters.get('+1/+1',0))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(count+1,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_actual_arwen_reads_current_toughness_after_response_before_entry(self):
        session=self.session(751704);arwen=self.add(session.engine,'Arwen, Weaver of Hope',zone='hand')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');growth=self.add(session.engine,'Generic Bound Growth',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-arwen-control')
        self.assertTrue(self.binding(session,arwen)['strict_capability_ready'])
        self.prepare(session,mana={'G':4,'C':2});self.checkpoint(session)
        self.offered_cast(session,arwen,{'G':2,'C':1});session=self.finish_entry(session)
        self.assertEqual(0,session.state.cards[arwen.object_id].counters.get('+1/+1',0));self.assertEqual(1,session.engine._numeric_stat(arwen.object_id,'toughness'))
        self.offered_cast(session,bear,{'G':1,'C':1});self.offered_cast(session,growth,{'G':1},[arwen.ref])
        self.assertIsNone(controls.ClassicFlashCastTests.resolve_to_depth(self,session,1))
        self.assertEqual(3,session.engine._numeric_stat(arwen.object_id,'toughness'))
        session=self.replay(session,load=True);session=self.finish_entry(session)
        self.assertEqual(3,session.state.cards[bear.object_id].counters.get('+1/+1',0));self.assertEqual(5,session.engine._numeric_stat(bear.object_id,'power'))
        self.private_replay(session,private)

    def test_actual_biomancer_reads_current_power_and_adds_noncopiable_mutant(self):
        session=self.session(751705);source=self.add(session.engine,'Master Biomancer',zone='hand')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');growth=self.add(session.engine,'Generic Bound Growth',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-biomancer-control')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'G':3,'U':1,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':1,'U':1,'C':2});session=self.finish_entry(session)
        self.assertNotIn('mutant',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[1])
        self.offered_cast(session,bear,{'G':1,'C':1});self.offered_cast(session,growth,{'G':1},[source.ref])
        self.assertIsNone(controls.ClassicFlashCastTests.resolve_to_depth(self,session,1));self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
        session=self.finish_entry(session);self.assertEqual(4,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        data=session.engine._effective_card_data(session.state.cards[bear.object_id]);self.assertTrue({'bear','mutant'}<=session.engine._type_parts(data['type_line'])[1])
        self.assertEqual(6,session.engine._numeric_stat(bear.object_id,'power'));self.private_replay(session,private)

    def test_actual_zero_power_biomancer_still_adds_mutant_without_counters(self):
        session=self.session(751706);source=self.add(session.engine,'Master Biomancer')
        source.counters['-1/-1']=2;bear=self.add(session.engine,'Grizzly Bears',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.assertEqual(0,session.engine._numeric_stat(source.object_id,'power'))
        self.prepare(session,mana={'G':1,'C':1});self.checkpoint(session);self.offered_cast(session,bear,{'G':1,'C':1})
        session=self.finish_entry(session);self.assertEqual(0,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.assertTrue({'bear','mutant'}<=session.engine._type_parts(session.engine._effective_card_data(session.state.cards[bear.object_id])['type_line'])[1])
        self.private_replay(session,private)

    def test_actual_bloodspore_devours_then_uses_its_current_counter_count(self):
        session=self.session(751707);source=self.add(session.engine,'Bloodspore Thrinax',zone='hand')
        food=[self.add(session.engine,'Generic Bound Body',ref='bloodspore-food-'+str(i)) for i in range(2)]
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':2});decision=self.resolve(session);self.assertIsNotNone(decision)
        self.assertEqual('replacement.order',decision.kind);session=self.replay(session,load=True)
        options=[r for r in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if 'replacement.zone.devour-entry.v1:' in r['id'] and not r['id'].startswith('decline:')]
        self.assertEqual(1,len(options));result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]['id'],
            'replacement_objects':[c.ref for c in food]});self.assertTrue(result.ok,result.summary)
        session=self.finish_entry(session);self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in food))
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(2,session.state.cards[bear.object_id].counters.get('+1/+1',0));self.assertEqual(4,session.engine._numeric_stat(bear.object_id,'power'))
        self.private_replay(session,private)

    def test_entry_order_offers_counter_multiplier_before_or_after_additional_source(self):
        from quorune.replacement import (AffectedObject,ReplaceableEvent,ReplacementClass,ReplacementEffect,
            CreateAffectedObjectCounter,AddAffectedObjectCounter,MultiplyAmount,replacement_tree_choice,apply_tree_replacement)
        event=ReplaceableEvent(event_id='entry-order',kind='zone.change',affected_player=None,
            affected_object=AffectedObject('entrant','A','A'),payload={'destination':'battlefield',
                'destination_controller':'A','object_ref':'entrant-ref','object_types':['creature','bear']})
        def effect(identity,kind,operation,priority=ReplacementClass.OTHER):
            return ReplacementEffect(effect_id=identity,source_id=identity,event_kind=kind,
                replacement_class=priority,operations=(operation,))
        base=effect('base','zone.change',CreateAffectedObjectCounter('+1/+1',1,'A','entrant-ref'),ReplacementClass.SELF_REPLACEMENT)
        extra=effect('extra','zone.change',AddAffectedObjectCounter('+1/+1',1,'A','giver-ref'))
        double=effect('double','counter.place',MultiplyAmount('amount',2));effects=(base,extra,double)
        initial=replacement_tree_choice(event,effects);self.assertIsNotNone(initial)
        seeded=apply_tree_replacement(event,effects,initial,'base')
        pending=replacement_tree_choice(seeded,effects);self.assertIsNotNone(pending)
        self.assertEqual({'extra','double'},set(pending.choice.options),'CR616 permits either current entry modification first')
        for order,amount in ((('double','extra'),3),(('extra','double'),4)):
            current=seeded
            for selected in order:
                pending=replacement_tree_choice(current,effects);self.assertIsNotNone(pending)
                self.assertIn(selected,pending.choice.options);current=apply_tree_replacement(current,effects,pending,selected)
            self.assertIsNone(replacement_tree_choice(current,effects));self.assertEqual(1,len(current.children))
            self.assertEqual(amount,current.children[0].payload['amount'])

    def test_actual_devour_entry_counter_order_survives_private_pending_load_and_replay(self):
        from quorune.record import authoritative_state_hash
        for index,(first,expected) in enumerate((('replacement.counter.quantity.',3),('replacement.zone.external-entry-counter.v1:',4),('external_then_double_then_devour',3))):
            session=self.session(751710+index);source=self.add(session.engine,'Bloodspore Thrinax',zone='hand')
            food=self.add(session.engine,'Generic Bound Body');self.add(session.engine,'Grumgully, the Generous')
            self.add(session.engine,'Doubling Season');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'G':2,'C':2});self.checkpoint(session);self.offered_cast(session,source,{'G':2,'C':2})
            decision=self.resolve(session);self.assertIsNotNone(decision)
            if index==2:
                for prefix,objects in (('replacement.zone.external-entry-counter.v1:',None),
                        ('replacement.counter.quantity.',None),('replacement.zone.devour-entry.v1:',[food.ref])):
                    ctx=session.packet('pilot:A',full=True)['decision']['ctx']
                    options=[r['id'] for r in ctx['options'] if r['id'].startswith(prefix)]
                    self.assertEqual(1,len(options));command={'action_id':'choose','replacement':options[0]}
                    if objects is not None:command['replacement_objects']=objects
                    session=self.replay(session,load=True);result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                    self.resolve(session)
                session=self.finish_entry(session);self.assertEqual(expected,session.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual('graveyard',session.state.cards[food.object_id].zone);self.private_replay(session,private)
                continue
            ctx=session.packet('pilot:A',full=True)['decision']['ctx']
            devour=[r for r in ctx['options'] if 'replacement.zone.devour-entry.v1:' in r['id'] and not r['id'].startswith('decline:')]
            self.assertEqual(1,len(devour));result=session.act('pilot:A',{'action_id':'choose','replacement':devour[0]['id'],'replacement_objects':[food.ref]})
            self.assertTrue(result.ok,result.summary);decision=self.resolve(session);self.assertIsNotNone(decision)
            self.assertEqual('replacement.order',decision.kind);session=self.replay(session,load=True)
            ctx=session.packet('pilot:A',full=True)['decision']['ctx'];options=[r['id'] for r in ctx['options'] if not r['id'].startswith('decline:')]
            self.assertTrue(any(r.startswith('replacement.counter.quantity.') for r in options),options)
            self.assertTrue(any(r.startswith('replacement.zone.external-entry-counter.v1:') for r in options),options)
            choices=[r for r in options if r.startswith(first)];self.assertEqual(1,len(choices))
            before=authoritative_state_hash(session.state);command={'action_id':'choose','replacement':choices[0]}
            self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.assertEqual('stack',session.state.cards[source.object_id].zone);self.assertEqual('battlefield',session.state.cards[food.object_id].zone)
            self.assertFalse(session.state.cards[source.object_id].counters)
            result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);session=self.finish_entry(session)
            self.assertEqual(expected,session.state.cards[source.object_id].counters.get('+1/+1',0))
            self.assertEqual('graveyard',session.state.cards[food.object_id].zone);self.private_replay(session,private)

    def test_biomancer_subtype_is_not_copied_and_expires_with_its_zone_object(self):
        session=self.session(751712);source=self.add(session.engine,'Master Biomancer')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand');clone=self.add(session.engine,'Clone',zone='hand')
        bounce_source=self.add(session.engine,'Unsummon',zone='hand',ref='bounce-biomancer')
        bounce_bear=self.add(session.engine,'Unsummon',zone='hand',ref='bounce-mutant')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':2,'U':3,'C':5});self.checkpoint(session)
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(2,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.assertIn('mutant',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[bear.object_id])['type_line'])[1])
        self.offered_cast(session,bounce_source,{'U':1},[source.ref]);session=self.finish_entry(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.offered_cast(session,clone,{'U':1,'C':3});decision=self.resolve(session);self.assertIsNotNone(decision)
        session=self.replay(session,load=True)
        options=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if bear.ref in r['id'] and not r['id'].startswith('decline:')]
        self.assertEqual(1,len(options));result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]});self.assertTrue(result.ok,result.summary)
        session=self.finish_entry(session);copied=session.state.cards[clone.object_id]
        self.assertEqual({'bear'},session.engine._type_parts(session.engine._effective_card_data(copied)['type_line'])[1])
        self.assertFalse(copied.counters);self.assertEqual(2,session.engine._numeric_stat(copied.object_id,'power'))
        self.assertIn('mutant',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[bear.object_id])['type_line'])[1])
        old=session.state.cards[bear.object_id].logical_object_id
        self.offered_cast(session,bounce_bear,{'U':1},[bear.ref]);session=self.finish_entry(session)
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        current=session.state.cards[bear.object_id];self.assertNotEqual(old,current.logical_object_id);self.assertFalse(current.counters)
        self.assertEqual({'bear'},session.engine._type_parts(session.engine._effective_card_data(current)['type_line'])[1]);self.private_replay(session,private)

    def test_grumgully_uses_prospective_copy_subtypes_before_entry(self):
        session=self.session(751713);self.add(session.engine,'Grumgully, the Generous')
        human=self.add(session.engine,'Generic Bound Body',seat='B');bear=self.add(session.engine,'Grizzly Bears',seat='B')
        clones=[self.add(session.engine,'Clone',zone='hand',ref='grumgully-copy-'+str(i)) for i in range(2)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2,'C':6});self.checkpoint(session)
        for clone,target,expected in zip(clones,(human,bear),(0,1)):
            self.offered_cast(session,clone,{'U':1,'C':3});decision=self.resolve(session);self.assertIsNotNone(decision)
            session=self.replay(session,load=True)
            options=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if target.ref in r['id'] and not r['id'].startswith('decline:')]
            self.assertEqual(1,len(options));result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]});self.assertTrue(result.ok,result.summary)
            session=self.finish_entry(session);self.assertEqual(expected,session.state.cards[clone.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_external_producer_and_counter_owner_are_independent_runtime_requirements(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(751714);source=self.add(session.engine,'Master Biomancer',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(d.get('handler_id')=='replacement.zone.external-entry-counter.v1' for d in p.handlers)]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        for dependency in ('counter.producer.external_additional_entry','counter.placement.quantity_replacement'):
            self.assertIn(dependency,program.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=dependency)
            stripped=replace(program,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'],dependency);self.assertIn('capability:undeclared_runtime_dependency:'+dependency,bound['blockers'])

    def test_canonically_verified_producer_and_counter_withdrawal_blocks_all_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap in ('counter.producer.external_additional_entry','counter.placement.quantity_replacement'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn external entry authority'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Grumgully, the Generous','Renata, Called to the Hunt','Bramblewood Paragon','Sage of Fables',
                    'Arwen, Weaver of Hope','Bloodspore Thrinax','Master Biomancer'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_destination_controller_overrides_owner_and_simultaneous_sources_do_not_apply(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        session=self.session(751715);self.add(session.engine,'Grumgully, the Generous')
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='foreign-owned-entrant')
        own=self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='own-opponent-entrant')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session)
        with session.engine.transaction():
            ZoneTransitionOwner(session.engine).move_cards_simultaneously(((foreign.object_id,'battlefield'),(own.object_id,'battlefield')),
                destination_controllers={foreign.object_id:'A',own.object_id:'B'},reason='Prospective controller entry witness')
        self.assertEqual(('B','A',1),(session.state.cards[foreign.object_id].owner,session.state.cards[foreign.object_id].controller,
            session.state.cards[foreign.object_id].counters.get('+1/+1',0)))
        self.assertEqual(('A','B',0),(session.state.cards[own.object_id].owner,session.state.cards[own.object_id].controller,
            session.state.cards[own.object_id].counters.get('+1/+1',0)))
        session=self.session(751716);giver=self.add(session.engine,'Grumgully, the Generous',zone='graveyard')
        bear=self.add(session.engine,'Grizzly Bears',zone='graveyard');self.prepare(session)
        with session.engine.transaction():
            ZoneTransitionOwner(session.engine).move_cards_simultaneously(((giver.object_id,'battlefield'),(bear.object_id,'battlefield')),
                reason='Simultaneous source entry witness')
        self.assertFalse(session.state.cards[giver.object_id].counters);self.assertFalse(session.state.cards[bear.object_id].counters)

    def test_source_ability_loss_before_entry_suppresses_bonus_and_departure_stops_later_entries(self):
        session=self.session(751717);source=self.add(session.engine,'Grumgully, the Generous')
        bear=self.add(session.engine,'Grizzly Bears',zone='hand',ref='ability-loss-entrant')
        later=self.add(session.engine,'Grizzly Bears',zone='hand',ref='source-gone-entrant')
        humble=self.add(session.engine,'Humble',zone='hand');bounce=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'G':2,'W':1,'U':1,'C':3});self.checkpoint(session)
        self.offered_cast(session,bear,{'G':1,'C':1});self.offered_cast(session,humble,{'W':1,'C':1},[source.ref])
        self.assertIsNone(controls.ClassicFlashCastTests.resolve_to_depth(self,session,1));session=self.finish_entry(session)
        self.assertFalse(session.state.cards[bear.object_id].counters)
        self.offered_cast(session,bounce,{'U':1},[source.ref]);session=self.finish_entry(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.offered_cast(session,later,{'G':1,'C':1});session=self.finish_entry(session);self.assertFalse(session.state.cards[later.object_id].counters)
        self.private_replay(session,private)

    def test_negative_source_power_clamps_counters_but_keeps_mutant_and_sources_add(self):
        session=self.session(751718);source=self.add(session.engine,'Master Biomancer');source.counters['-1/-1']=3
        first=self.add(session.engine,'Grizzly Bears',zone='hand',ref='negative-power-entrant')
        second=self.add(session.engine,'Grizzly Bears',zone='hand',ref='cumulative-entry')
        self.assertEqual(-1,session.engine._numeric_stat(source.object_id,'power'))
        self.prepare(session,mana={'G':2,'C':2});self.checkpoint(session)
        self.offered_cast(session,first,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertFalse(session.state.cards[first.object_id].counters)
        self.assertIn('mutant',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[first.object_id])['type_line'])[1])
        # These are independent existing replacement sources, never duplicate IDs.
        self.add(session.engine,'Grumgully, the Generous');self.add(session.engine,'Renata, Called to the Hunt')
        self.checkpoint(session);self.offered_cast(session,second,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(2,session.state.cards[second.object_id].counters.get('+1/+1',0));self.replay(session,load=True)

    def test_native_source_subtype_and_replacement_order_omissions_are_killed(self):
        from unittest.mock import patch
        import quorune.semantic_runtime.zone_replacements as owner
        original=owner.collect_zone_change_replacement_effects
        def omitted(host,**kwargs):
            return tuple(e for e in original(host,**kwargs) if not e.effect_id.startswith('replacement.zone.external-entry-counter.v1:'))
        cases=(('quorune.semantic_runtime.zone_replacements.collect_zone_change_replacement_effects',omitted,
                'test_actual_grumgully_excludes_itself_and_human_but_adds_before_nonhuman_entry'),
            ('quorune.rules.entry_subtype_grants.commit_entry_subtype_grants',lambda *a:(),
                'test_actual_zero_power_biomancer_still_adds_mutant_without_counters'),
            ('quorune.replacement.entry_counter_ordering.entry_counter_order_effects',lambda event,effects:tuple(effects),
                'test_entry_order_offers_counter_multiplier_before_or_after_additional_source'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_source_control_change_uses_current_controller_for_later_entries(self):
        session=self.session(751719);source=self.add(session.engine,'Grumgully, the Generous',seat='B')
        steal=self.add(session.engine,'Confiscate',zone='hand');bear=self.add(session.engine,'Grizzly Bears',zone='hand')
        artifact=self.add(session.engine,'Doubling Season',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2,'G':2,'C':9});self.checkpoint(session)
        self.offered_cast(session,steal,{'U':2,'C':4},[source.ref]);session=self.finish_entry(session)
        self.assertEqual(('B','A'),(session.state.cards[source.object_id].owner,session.state.cards[source.object_id].controller))
        self.offered_cast(session,bear,{'G':1,'C':1});session=self.finish_entry(session)
        self.assertEqual(1,session.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.offered_cast(session,artifact,{'G':1,'C':4});session=self.finish_entry(session)
        self.assertFalse(session.state.cards[artifact.object_id].counters);self.private_replay(session,private)

    def test_additional_entry_operations_reject_wrong_event_and_combine_unmodified_contributions(self):
        from dataclasses import replace
        from quorune.replacement import (AffectedObject,ReplaceableEvent,ReplacementClass,ReplacementEffect,
            CreateAffectedObjectCounter,AddAffectedObjectCounter,replacement_choice,apply_replacement)
        event=ReplaceableEvent(event_id='combined-entry',kind='zone.change',affected_player=None,
            affected_object=AffectedObject('entrant','A','A'),payload={'destination':'battlefield',
                'destination_controller':'A','object_ref':'entrant-ref','object_types':['creature','bear']})
        def apply(current,identity,op):
            effect=ReplacementEffect(effect_id=identity,source_id=identity,event_kind=current.kind,
                replacement_class=ReplacementClass.OTHER,operations=(op,))
            choice=replacement_choice(current,(effect,));self.assertIsNotNone(choice)
            return apply_replacement(choice,(effect,),identity)
        first=apply(event,'base-one',CreateAffectedObjectCounter('+1/+1',1,'A','first'))
        second=apply(first,'base-two',CreateAffectedObjectCounter('+1/+1',2,'A','second'))
        self.assertEqual(2,len(second.children));combined=apply(second,'extra',AddAffectedObjectCounter('+1/+1',1,'A','giver'))
        self.assertEqual(1,len(combined.children));self.assertEqual(4,combined.children[0].payload['amount'])
        bad=replace(event,payload={**dict(event.payload),'destination':'hand'})
        before=bad.to_dict()
        with self.assertRaises(ValueError):apply(bad,'bad-extra',AddAffectedObjectCounter('+1/+1',1,'A','giver'))
        self.assertEqual(before,bad.to_dict())

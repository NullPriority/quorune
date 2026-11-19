from __future__ import annotations

"""Pinned CR 118.9/601.2/702.74 and complete MH2 Oracle/ruling sources."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.evoke import FixedManaEvokeSpec, compile_fixed_mana_evoke
from quorune.hand_exile_evoke_model import HandExileEvokeSpec, compile_hand_exile_evoke, HAND_EXILE_EVOKE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from quorune.semantic_runtime.cast_costs import HandExileEvokeCostHandler, hand_exile_evoke_handler_descriptor
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls

FIXTURE=ROOT/"tests/fixtures/hand-exile-evoke-cards.json"
CANDIDATES=("Grief","Fury","Solitude")
EXCLUDED=("Endurance","Subtlety")
LEGACY=("Mulldrifter","Shriekmaw")


class HandExileEvokeModelTests(unittest.TestCase):
    def spec(self,word="black"):
        line=f"Evoke—Exile a {word} card from your hand."
        return compile_hand_exile_evoke(material_line=line,oracle_line=line,line_index=2)

    def test_five_colors_lower_to_one_owned_hand_exile_without_oracle_prose(self):
        for word,symbol in zip(("white","blue","black","red","green"),"WUBRG"):
            spec=self.spec(word);self.assertEqual(symbol,spec.color)
            self.assertEqual(spec,HandExileEvokeSpec.from_dict(spec.to_dict()))
            self.assertNotIn("oracle_line",spec.to_dict())
            cost=FixedZoneChangeAdditionalCost.from_descriptor(spec.additional_cost())
            self.assertEqual(("exile_one_from_hand",1,"exile_cards"),(cost.operation,cost.count,cost.choice_field))
            self.assertEqual(("hand",),cost.predicate.zones);self.assertEqual("$actor",cost.predicate.owner)
            self.assertEqual((symbol,),cost.predicate.colors_all)
            option,=spec.cast_cost_options();self.assertFalse(any(option["requirements"].values()))
            self.assertEqual([spec.additional_cost()],option["_additional_option_costs"])

    def test_exclusions_and_legacy_mana_evoke_keep_their_original_codec(self):
        for line in ("Evoke {2}{U}","Evoke—Exile two black cards from your hand.",
            "Evoke—Exile a black card from your graveyard.","Evoke—Exile a black or red card from your hand.",
            "Evoke—Pay 2 life and exile a black card from your hand.","Evoke—Exile a black card from your hand. Draw a card."):
            with self.subTest(line=line):self.assertIsNone(compile_hand_exile_evoke(material_line=line,oracle_line=line,line_index=0))
        line="Evoke {2}{U}";legacy=compile_fixed_mana_evoke(material_line=line,oracle_line=line,line_index=1)
        wire=legacy.to_dict();self.assertEqual(wire,FixedManaEvokeSpec.from_dict(wire).to_dict())
        self.assertEqual(2,legacy.schema_version)
        legacy=FixedManaEvokeSpec.from_dict({**wire,'schema_version':1})
        from quorune.evoke import validate_evoke_payment_marker,evoke_sacrifice_source_reference
        old=legacy.cast_cost_options()[0]['evoke_payment'];new=self.spec().cast_cost_options()[0]['evoke_payment']
        self.assertEqual({'schema_version':1,'kind':'evoke','sacrifice_on_entry':True},old)
        self.assertEqual(2,new['schema_version']);self.assertTrue(validate_evoke_payment_marker(new))
        self.assertEqual('$source',evoke_sacrifice_source_reference({'evoke':True,'source_logical_object_id':'old@1'}))
        self.assertEqual('$source.zone_object',evoke_sacrifice_source_reference(
            {'evoke':True,'source_logical_object_id':'new@1','evoke_source_identity_version':2}))
        nonmana="Evoke—Exile a black card from your hand."
        self.assertIsNone(compile_fixed_mana_evoke(material_line=nonmana,oracle_line=nonmana,line_index=1))
        for change in ({'schema_version':True},{'schema_version':2},{'line_index':True},{'color':'C'},
            {'source_line_hash':'bad'},{'ability_id':'ab1'},{'oracle_line':nonmana}):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                HandExileEvokeSpec.from_dict({**self.spec().to_dict(),**change})

    def test_component_requires_complete_parent_and_does_not_compile_at_runtime(self):
        spec=self.spec();descriptor=hand_exile_evoke_handler_descriptor(spec)
        with patch('quorune.hand_exile_evoke_model.compile_hand_exile_evoke',side_effect=AssertionError('runtime compilation')):
            self.assertEqual(spec,HandExileEvokeCostHandler().validate(descriptor))
        field=next(k for k in descriptor if k not in {'handler_id','schema_version','event','evoke'})
        for change in ({field:False},{'schema_version':True},{'event':'activate'},{'unknown':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                HandExileEvokeCostHandler().validate({**descriptor,**change})
        self.assertNotEqual(spec.fingerprint,replace(spec,color='R').fingerprint)


class HandExileEvokeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'evoke-originals.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_complete_originals_close_without_dropping_unsupported_siblings(self):
        semantics=SemanticRegistry()
        for name in (*CANDIDATES,*EXCLUDED,*LEGACY):
            with self.subTest(name=name):
                card=self.db.lookup(name,fuzzy=False)
                register_generated_programs(self.db,semantics,(card,),trust_level='provisional',capability_registry=self.registry,
                    capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(name in (*CANDIDATES,'Mulldrifter'),bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])

    def test_source_span_and_all_cost_authorities_are_required(self):
        from quorune.rules.capabilities import CapabilityRegistry
        for name in CANDIDATES:
            card=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=next(n for f in ir.faces for n in f.nodes if n.template_id=='fixed-hand-exile-evoke-v1')
            self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
            for missing in (HAND_EXILE_EVOKE_CAPABILITY,'casting.evoke.fixed_mana','casting.additional_cost.zone_change.fixed_exile'):
                raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(r for r in raw['capabilities'] if r['id']==missing).update(status='blocked',blockers=['evoke cost authority withdrawn'])
                registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
                with self.subTest(name=name,missing=missing):
                    self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review').status)


class HandExileEvokeRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachmentFollowupRuntimeTests.session
    add=controls.AttachmentFollowupRuntimeTests.add
    prepare=controls.AttachmentFollowupRuntimeTests.prepare
    closed=controls.AttachmentFollowupRuntimeTests.closed
    actions=controls.AttachmentFollowupRuntimeTests.actions
    cast_offered=controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint=controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve=controls.AttachmentFollowupRuntimeTests.resolve
    replay=controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'hand-evoke.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Hand-exile Evoke',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self,seed,name='Grief',mana=None,extra=()):
        s=self.session(seed);source=self.add(s.engine,name,zone='hand')
        mono=self.add(s.engine,'Dark Ritual' if name=='Grief' else 'Shock',zone='hand',ref='mono-evoke-price')
        multi=self.add(s.engine,'Terminate',zone='hand',ref='multi-evoke-price')
        wrong=self.add(s.engine,'Sol Ring',zone='hand',ref='wrong-evoke-price')
        private=self.add(s.engine,'Unsummon',seat='D',zone='hand',ref='private-evoke-hand')
        victim=self.add(s.engine,'Shock',seat='B',zone='hand',ref='grief-discard')
        for index,n in enumerate(extra):self.add(s.engine,n,seat='C',ref=f'evoke-control-{index}')
        self.prepare(s,source,mana or {})
        return s,source,mono,multi,wrong,private,victim

    def offer(self,s,source):return next(r for r in self.actions(s) if r['id']=='cast:'+source.ref)
    def options(self,s,source):return {o['id']:o for o in self.offer(s,source)['cost_options']}

    def cast(self,s,source,pitch=None,*,evoke=True):
        command={'action_id':self.offer(s,source)['id'],'pay':'auto'}
        if evoke:command['cost_option']='evoke'
        if pitch is not None:command['exile_cards']=[pitch.ref]
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def reject(self,s,command,seat='A'):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def settle(self,s,*,body_first=True,targets=('B',),allocation=None,discard=None,pause_after_discard=False):
        for _ in range(160):
            d=s.state.pending_decision
            if d is not None and d.kind=='trigger.order':
                principal=s.pending_principals()[0];rows=s.packet(principal,full=True)['decision']['ctx']['triggers']
                rows=sorted(rows,key=lambda r:('evoke' not in r['label'].casefold()) if body_first else ('evoke' in r['label'].casefold()))
                result=s.act(principal,{'action_id':'order','triggers':[r['id'] for r in rows]});self.assertTrue(result.ok,result.summary);continue
            if d is not None and d.kind=='semantic.target':
                command={'action_id':'choose','targets':list(targets)}
                if allocation is not None:command['damage_allocation']=allocation
                result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary);continue
            if d is not None and d.kind=='semantic.choice':
                ctx=s.packet(s.pending_principals()[0],full=True)['decision']['ctx']
                self.assertEqual('fixed_hand_inspection',ctx['operation'])
                result=s.act(s.pending_principals()[0],{'action_id':'choose','card':discard});self.assertTrue(result.ok,result.summary)
                if pause_after_discard:return s.state.pending_decision
                continue
            if d is not None and d.kind!='priority':return d
            if not s.state.stack:return None
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Evoke scenario did not settle')

    def private_replay(self,s,private):
        for seat in 'ABC':self.assertNotIn(private.ref,str(s.packet('pilot:'+seat,full=True)))
        return self.replay(s,load=True)

    def test_grief_color_cost_and_both_trigger_orders_survive_source_sacrifice(self):
        for index,(body_first,blink) in enumerate(((True,False),(False,False),(True,True))):
            s,source,mono,multi,_,private,victim=self.world(299100+index)
            if blink:
                response=self.add(s.engine,'Ephemerate',zone='hand')
                second=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='second-grief-discard')
                self.closed(s,response);self.prepare(s,source,{'W':1})
            self.checkpoint(s);legal=self.options(s,source)['evoke']['choice_schema']['exile_cards']['legal_refs']
            self.assertEqual({mono.ref,multi.ref},set(legal));self.assertNotIn(source.ref,legal)
            chosen=mono if body_first else multi;self.cast(s,source,chosen)
            self.assertEqual('exile',s.state.cards[chosen.object_id].zone);self.assertEqual('stack',s.state.cards[source.object_id].zone)
            retained=multi if body_first else mono
            for seat in 'BCD':self.assertNotIn(retained.ref,str(s.packet('pilot:'+seat,full=True)))
            self.assertFalse(any(e.code=='card.discard' and chosen.ref in str(e.details) for e in s.state.events))
            s=self.private_replay(s,private)
            if blink:
                self.settle(s,body_first=True,discard=victim.ref,pause_after_discard=True)
                before=s.state.cards[source.object_id].logical_object_id
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.cast_offered(s,response,targets=(source.ref,))
                self.assertIsNone(self.settle(s,discard=second.ref))
                self.assertNotEqual(before,s.state.cards[source.object_id].logical_object_id)
            else:self.assertIsNone(self.settle(s,body_first=body_first,discard=victim.ref))
            self.assertEqual('battlefield' if blink else 'graveyard',s.state.cards[source.object_id].zone)
            self.assertEqual('graveyard',s.state.cards[victim.object_id].zone);self.private_replay(s,private)

    def test_fury_announces_its_division_or_zero_targets_after_real_exile_payment(self):
        for index,(empty,body_first) in enumerate(((False,True),(True,True),(False,False))):
            s,source,mono,_,_,private,_=self.world(299110+index,'Fury')
            first=self.add(s.engine,'Ornithopter',seat='B',ref='fury-first')
            second=self.add(s.engine,'Ornithopter',seat='C',ref='fury-second')
            self.prepare(s,source,{});self.checkpoint(s);self.cast(s,source,mono)
            refs=() if empty else (first.ref,second.ref);division={} if empty else {first.ref:2,second.ref:2}
            self.assertIsNone(self.settle(s,body_first=body_first,targets=refs,allocation=division))
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            self.assertEqual('battlefield' if empty else 'graveyard',s.state.cards[first.object_id].zone)
            self.assertEqual('exile',s.state.cards[mono.object_id].zone);self.private_replay(s,private)

    def test_illegal_exiles_reject_atomically_and_no_matching_card_hides_evoke(self):
        s,source,mono,multi,wrong,private,_=self.world(299120,mana={'B':2,'C':2})
        self.checkpoint(s);row=self.offer(s,source)
        for selected in ([],[wrong.ref],[private.ref],[source.ref],[mono.ref,mono.ref],[mono.ref,multi.ref]):
            self.reject(s,{'action_id':row['id'],'cost_option':'evoke','exile_cards':selected,'pay':'auto'})
        self.reject(s,{'action_id':row['id'],'cost_option':'evoke','exile_cards':[mono.ref],'pay':'auto'},'B')
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.private_replay(s,private)
        s,source,mono,multi,_,_,_=self.world(299121,mana={'B':2,'C':2})
        for card in (mono,multi):s.engine.move_card(card.object_id,'exile',log=False)
        self.prepare(s,source,{'B':2,'C':2});self.checkpoint(s)
        self.assertNotIn('evoke',self.options(s,source))
        self.reject(s,{'action_id':self.offer(s,source)['id'],'cost_option':'evoke','pay':'auto'})

    def test_normal_cast_keeps_its_body_without_exile_or_evoke_sacrifice(self):
        s,source,mono,multi,_,private,victim=self.world(299130,mana={'B':2,'C':2})
        self.checkpoint(s);self.cast(s,source,evoke=False);self.assertIsNone(self.settle(s,discard=victim.ref))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        for card in (mono,multi):self.assertEqual('hand',s.state.cards[card.object_id].zone)
        self.private_replay(s,private)

    def test_countered_spell_keeps_the_price_paid_and_never_enters(self):
        s,source,mono,_,_,private,_=self.world(299140)
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');self.closed(s,counter)
        s.state.players['B'].mana_pool.update({'U':2});self.checkpoint(s);self.cast(s,source,mono)
        result=s.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.cast_offered(s,counter,seat='B',targets=(s.state.stack[-1].ref,))
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('exile',s.state.cards[mono.object_id].zone)
        self.assertFalse(any(item.context.get('evoke') for item in s.state.stack));self.private_replay(s,private)

    def test_spell_tax_is_added_to_the_nonmana_alternative_and_mana_value_stays_four(self):
        s,source,mono,_,_,private,victim=self.world(299150,extra=('Sphere of Resistance',))
        self.assertFalse(any(r['id']=='cast:'+source.ref for r in self.actions(s)))
        self.add(s.engine,'Swamp',ref='evoke-tax-land');self.prepare(s,source,{});self.checkpoint(s)
        option=self.options(s,source)['evoke'];self.assertEqual(1,option['requirements']['GENERIC'])
        self.cast(s,source,mono)
        from quorune.object_query import object_query_result
        card=s.state.cards[source.object_id];effective=s.engine._effective_card_data(card)
        row=object_query_result(card,effective,type_parts=s.engine._type_parts(effective['type_line']),known_to_actor=True,attached_to_ref=None)
        self.assertEqual(4,row.mana_value);self.assertIsNone(self.settle(s,discard=victim.ref));self.private_replay(s,private)

    def test_trigger_prohibition_and_ability_loss_keep_the_evoked_creature(self):
        for index,name in enumerate(('Torpor Orb','Dress Down')):
            s,source,mono,_,_,private,victim=self.world(299160+index,extra=(name,))
            control=next(c for c in s.state.cards.values() if c.ref=='evoke-control-0');self.closed(s,control)
            self.checkpoint(s);self.cast(s,source,mono);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            self.assertEqual('hand',s.state.cards[victim.object_id].zone);self.assertEqual('exile',s.state.cards[mono.object_id].zone)
            self.private_replay(s,private)

    def test_exiled_commander_payment_remains_paid_after_its_owner_returns_it(self):
        s,source,_,_,_,private,victim=self.world(299170)
        paid=self.add(s.engine,'Lady Orca',zone='hand',ref='evoke-commander-price')
        for card in s.state.cards.values():
            if card.owner=='A' and card.is_commander:card.is_commander=False;card.commander_designation_id=None
        paid.is_commander=True;paid.commander_designation_id='commander:'+paid.object_id;s.state.commander_oracle_ids['A']=[paid.oracle_id]
        self.prepare(s,source,{})
        self.checkpoint(s);self.cast(s,source,paid);self.assertEqual('state.commander_zone',s.state.pending_decision.kind)
        self.assertEqual('exile',s.state.cards[paid.object_id].zone)
        self.reject(s,{'action_id':'command','choice':'command'},'B');s=self.private_replay(s,private)
        result=s.act('pilot:A',{'action_id':'command','choice':'command'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('command',s.state.cards[paid.object_id].zone);self.assertIsNone(self.settle(s,discard=victim.ref))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_legacy_mana_evoke_still_uses_the_same_sacrifice_lifecycle(self):
        s=self.session(299180);source=self.add(s.engine,'Mulldrifter',zone='hand')
        self.prepare(s,source,{'C':2,'U':1});self.checkpoint(s);before=len(s.state.players['A'].zones['hand'])
        self.cast(s,source);self.assertIsNone(self.settle(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(before+1,len(s.state.players['A'].zones['hand']));self.replay(s,load=True)

    def test_payload_minimum_cannot_hide_the_new_cost_without_coverage(self):
        s,source,*_=self.world(299190)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if HAND_EXILE_EVOKE_CAPABILITY in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=HAND_EXILE_EVOKE_CAPABILITY)
        changed=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertTrue(any(HAND_EXILE_EVOKE_CAPABILITY in x for x in bound['blockers']),bound['blockers'])

    def test_wrong_color_query_mutation_is_killed_by_a_fresh_real_cast(self):
        from quorune.rules.casting_additional_costs import query_objects
        s,source,_,_,wrong,_,_=self.world(299200)
        def omit(rows,query):return query_objects(rows,replace(query,colors_all=()))
        with patch('quorune.rules.casting_additional_costs.query_objects',side_effect=omit):
            self.prepare(s,source,{});self.checkpoint(s);self.cast(s,source,wrong)
            with self.assertRaises(AssertionError):self.assertEqual('hand',s.state.cards[wrong.object_id].zone)


if __name__=='__main__':unittest.main()

from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.cast_lifecycles import FixedCastLifecycleKind,FixedCastLifecycleSpec,FixedCastLifecycleError,compile_fixed_cast_lifecycle,fixed_cast_lifecycle_stack_replacement
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class AftermathCompilerTests(unittest.TestCase):
    def test_selected_half_has_bare_lifecycle_and_other_face_remains_ordinary(self):
        with CardDatabase(ROOT/'data/scryfall-current.sqlite3') as db:
            # Explicit pinned Oracle identity avoids same-name synthetic aliases.
            ir=compile_oracle_card(db.by_oracle_id('16da9e4b-c6ee-4e70-8653-31ca147f3b01'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[(f.face_name,n) for f in ir.faces for n in f.nodes if 'aftermath' in n.mechanics]
        self.assertEqual(1,len(nodes));self.assertEqual('Fame',nodes[0][0]);self.assertEqual('all',nodes[0][1].active_zone)
        self.assertEqual('aftermath',nodes[0][1].handlers[0]['lifecycle']['kind']);self.assertIn('casting.aftermath.selected_half',nodes[0][1].capability_dependencies)

    def test_aftermath_descriptor_rejects_costs_wrong_shapes_and_nonsplit_carrier(self):
        spec=compile_fixed_cast_lifecycle(material_line='Aftermath',oracle_line='Aftermath',line_index=0)
        self.assertEqual(FixedCastLifecycleKind.AFTERMATH,spec.kind);self.assertEqual(spec.to_dict(),FixedCastLifecycleSpec.from_dict(spec.to_dict()).to_dict())
        for changes in ({'schema_version':True},{'cost_text':'{W}'},{'counter_count':1},{'oracle_line':'Aftermath {W}'},{'exile_count':1}):
            with self.assertRaises(FixedCastLifecycleError):FixedCastLifecycleSpec.from_dict({**spec.to_dict(),**changes})
        from quorune.cast_lifecycles import fixed_cast_lifecycle_handler_descriptor
        from quorune.semantic_runtime.cast_lifecycles import FixedCastLifecycleHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        descriptor=fixed_cast_lifecycle_handler_descriptor(spec);handler=FixedCastLifecycleHandler()
        self.assertEqual(spec,handler.validate(descriptor))
        for changes in ({'schema_version':True},{'requires_complete_card_program':False},{'event':'activate'}):
            with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,**changes})
        ir=compile_oracle_card(replace(query_record('Aftermath'),keywords=('Aftermath',)),capability_registry=load_default_capability_registry())
        self.assertNotEqual('exact',ir.status)

    def test_zone_cast_dependency_omission_blocks_aftermath_half(self):
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(c for c in value['capabilities'] if c['id']=='casting.lifecycle.fixed_zone_cast');row['status']='blocked';row['blockers']=['omitted-zone-cast-owner']
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        with CardDatabase(ROOT/'data/scryfall-current.sqlite3') as db:
            self.assertNotEqual('exact',compile_oracle_card(db.by_oracle_id('16da9e4b-c6ee-4e70-8653-31ca147f3b01'),capability_registry=registry).status)


class AftermathRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'aftermath.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/aftermath-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Selected cast face',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_both_split_faces_publish_and_accept_own_costs_with_rollback_and_replay(self):
        s=self.session(320001);e=s.engine;source=self.add(e,'Generic Split Draw',zone='hand');self.ready(s,source,{'W':1,'U':1,'C':3});self.checkpoint(s)
        offers=[a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref]
        self.assertEqual({'First Draw','Second Draw'},{a['face'] for a in offers});self.assertEqual(2,len({a['id'] for a in offers}))
        second=next(a for a in offers if a['face']=='Second Draw');first=next(a for a in offers if a['face']=='First Draw')
        self.assertEqual(3,second['cost_options'][0]['requirements']['GENERIC']);self.assertEqual(1,second['cost_options'][0]['requirements']['U'])
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:B',{'action_id':second['id'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        underpaid=s.act('pilot:A',{'action_id':second['id'],'pay':'manual','payment':{'W':1}});self.assertFalse(underpaid.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        override=s.act('pilot:A',{'action_id':first['id'],'face':'Second Draw','pay':'auto'});self.assertFalse(override.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        count=len(s.state.players['A'].zones['hand'])
        accepted=s.act('pilot:A',{'action_id':second['id'],'pay':'manual','payment':{'U':1,'C':3}});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,s.state.players['A'].mana_pool['W']);self.assertEqual(0,s.state.players['A'].mana_pool['U'])
        self.assertEqual('Second Draw',s.state.stack[-1].label);self.assertEqual(4,e._effective_card_data(s.state.cards[source.object_id])['mana_value'])
        self.resolve(s);self.assertEqual(count+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual(5,e._effective_card_data(s.state.cards[source.object_id])['mana_value']);self.replay(s,load=True)

    def test_actual_aftermath_only_offers_its_half_from_graveyard_resolves_and_replays(self):
        s=self.session(320002);e=s.engine;source=self.add(e,'Claim // Fame',zone='graveyard');recipient=self.add(e,'Generic Bound Body')
        action=self.ready(s,source,{'R':1,'C':1});self.checkpoint(s)
        self.assertEqual('Fame',action['face']);self.assertEqual({'Fame'},{a['face'] for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref})
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(['R'],e._effective_card_data(s.state.cards[source.object_id])['colors']);self.assertEqual(2,e._effective_card_data(s.state.cards[source.object_id])['mana_value'])
        self.assertNotIn('prospective_cast_face',s.state.cards[source.object_id].annotations)
        self.assertEqual('aftermath',s.state.stack[-1].context['fixed_cast_lifecycle']['kind'])
        self.resolve(s);self.assertEqual('exile',s.state.cards[source.object_id].zone);self.assertEqual(4,e._numeric_stat(recipient.object_id,'power'));self.assertIn('haste',e._combat_keywords(s.state.cards[recipient.object_id]));self.replay(s,load=True)

    def test_hand_half_is_ordinary_and_aftermath_cannot_override_its_offer(self):
        s=self.session(320003);e=s.engine;source=self.add(e,'Claim // Fame',zone='hand');recipient=self.add(e,'Generic Bound Body',zone='graveyard');foreign=self.add(e,'Generic Bound Body',seat='B',zone='graveyard',ref='foreign-graveyard-target')
        action=self.ready(s,source,{'B':1,'R':1,'C':1});self.checkpoint(s)
        self.assertEqual('Claim',action['face']);self.assertNotIn(foreign.ref,action['target_schema']['legal_refs']);before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:A',{'action_id':action['id'],'targets':[foreign.ref],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        rejected=s.act('pilot:A',{'action_id':action['id'],'face':'Fame','targets':[recipient.ref],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('battlefield',s.state.cards[recipient.object_id].zone);self.replay(s)

    def test_omitting_selected_face_cost_mutant_is_killed(self):
        from quorune.engine import CommanderEngine
        original=CommanderEngine._compiled_printed_cost_options
        def first_cost(host,seat,card,**kwargs):return original(host,seat,replace(card,active_face=None),**kwargs)
        with patch.object(CommanderEngine,'_compiled_printed_cost_options',first_cost):
            with self.assertRaises(AssertionError):self.test_both_split_faces_publish_and_accept_own_costs_with_rollback_and_replay()

    def test_actual_countered_aftermath_exiles_and_uncast_copy_has_no_replacement(self):
        s=self.session(320004);e=s.engine;source=self.add(e,'Claim // Fame',zone='graveyard');recipient=self.add(e,'Generic Bound Body');counter=self.add(e,'Counterspell',seat='B',zone='hand')
        action=self.ready(s,source,{'R':1,'C':1});s.state.players['B'].mana_pool['U']=2;self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        original_ref=s.state.stack[-1].ref
        self.assertIsNotNone(fixed_cast_lifecycle_stack_replacement(s.state.cards[source.object_id]))
        copied=replace(s.state.cards[source.object_id],object_kind='spell_copy')
        self.assertIsNone(fixed_cast_lifecycle_stack_replacement(copied))
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==counter.ref)
        accepted=s.act('pilot:B',{'action_id':action['id'],'targets':[original_ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.assertEqual(2,e._numeric_stat(recipient.object_id,'power'));self.replay(s,load=True)

    def test_omitting_aftermath_stack_designation_mutant_is_killed(self):
        from quorune.rules.casting import commit
        original=commit.mark_card_fixed_cast_lifecycle
        def omit(card,spec):
            if spec.kind is not FixedCastLifecycleKind.AFTERMATH:original(card,spec)
        with patch.object(commit,'mark_card_fixed_cast_lifecycle',side_effect=omit):
            with self.assertRaises(AssertionError):self.test_actual_countered_aftermath_exiles_and_uncast_copy_has_no_replacement()

    def test_actual_market_pending_private_discard_reloads_without_repeating_draw(self):
        from quorune.session import CommanderSession
        s=self.session(320005);source=self.add(s.engine,'Farm // Market',zone='graveyard');action=self.ready(s,source,{'U':1,'C':2});self.checkpoint(s)
        count=len(s.state.players['A'].zones['hand']);accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual(count+2,len(s.state.players['A'].zones['hand']))
        self.assertIsNone(s.packet('pilot:B',full=True)['decision']);before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:B',{'action_id':'choose','cards':[]});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'discard';s.save(path);s=CommanderSession.load(self.db,path)
        refs=[s.state.cards[o].ref for o in s.state.players['A'].zones['hand'][:2]]
        accepted=s.act('pilot:A',{'action_id':'choose','cards':refs});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(count,len(s.state.players['A'].zones['hand']));self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_canonical_free_cast_permission_never_overrides_other_zone_prohibition(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
        from quorune.rules.casting.proposal import build_cast_offer
        s=self.session(320006);e=s.engine;source=self.add(e,'Claim // Fame',zone='exile');recipient=self.add(e,'Generic Bound Body')
        grant=TemporaryCastPermissionGrant('A',ImpulseAccessDuration.END_OF_TURN,None,True,'canonical selected-half permission witness')
        grant_temporary_cast_permission(e,card=source,grant=grant,reason='canonical permission boundary')
        self.assertEqual('aftermath_graveyard_only',build_cast_offer(e,'A',source,face_name='Fame').reason)
        e.move_card(source.object_id,'graveyard');grant_temporary_cast_permission(e,card=source,grant=grant,reason='canonical permission boundary')
        action=self.ready(s,source,{});self.checkpoint(s);self.assertEqual('Fame',action['face'])
        self.assertTrue(all(not any(o['requirements'].values()) for o in action['cost_options']))
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNotNone(fixed_cast_lifecycle_stack_replacement(s.state.cards[source.object_id]));self.resolve(s)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_selected_half_color_cost_query_ignores_other_half_and_replays(self):
        s=self.session(320007);e=s.engine;self.add(e,'Jet Medallion');source=self.add(e,'Claim // Fame',zone='graveyard');recipient=self.add(e,'Generic Bound Body')
        action=self.ready(s,source,{'R':1,'C':1});self.checkpoint(s);self.assertEqual('Fame',action['face'])
        self.assertEqual(1,action['cost_options'][0]['requirements']['GENERIC'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s)

    def test_modal_back_spell_uses_own_cost_and_permanent_destination_and_replays(self):
        s=self.session(320008);e=s.engine;source=self.add(e,'Generic Modal Cast',zone='hand');self.ready(s,source,{'W':1,'U':1,'C':3});self.checkpoint(s)
        offers=[a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref]
        self.assertEqual({'Modal Draw','Modal Body'},{a['face'] for a in offers});action=next(a for a in offers if a['face']=='Modal Body')
        self.assertEqual(3,action['cost_options'][0]['requirements']['GENERIC'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'U':1,'C':3}});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        current=s.state.cards[source.object_id];self.assertEqual('battlefield',current.zone);self.assertEqual('Modal Body',current.active_face)
        data=e._effective_card_data(current);self.assertEqual(4,data['mana_value']);self.assertEqual(['U'],data['colors']);self.replay(s,load=True)

    def test_strict_modal_unknown_face_is_hidden_and_rejected_before_mutation(self):
        from quorune.rules.casting.proposal import build_cast_offer,build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest,CastProposalError
        s=self.session(320009);e=s.engine;source=self.add(e,'Generic Modal Boundary',zone='hand');s.state.config.semantic_policy='trusted_only'
        action=self.ready(s,source,{'W':1,'U':1,'C':3});self.checkpoint(s)
        self.assertEqual('Boundary Bird',action['face'])
        result=build_cast_offer(e,'A',source,face_name='Unknown History');self.assertEqual('semantic_policy_requires_trusted',result.reason)
        before=authoritative_state_hash(s.state)
        with self.assertRaises(CastProposalError):build_cast_proposal(e,CastProposalRequest(actor='A',card_ref=source.ref,zones=('hand',),face='Unknown History'))
        self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s);self.replay(s,load=True)

    def test_prospective_face_values_preserve_devoid_layer_before_cost_query(self):
        s=self.session(320010);e=s.engine;self.add(e,'Pearl Medallion');source=self.add(e,'Generic Modal Devoid',zone='hand')
        self.ready(s,source,{'W':4,'C':3});self.checkpoint(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref and a.get('face')=='Colorless Draw')
        self.assertEqual(3,action['cost_options'][0]['requirements']['GENERIC'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual([],e._effective_card_data(s.state.cards[source.object_id])['colors']);self.resolve(s);self.replay(s,load=True)

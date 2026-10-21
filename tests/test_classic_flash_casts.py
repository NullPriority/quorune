from __future__ import annotations

"""CR603.7g and pinned rulings distinguish permission use from spell timing."""

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_temporary_cast_restrictions as controls
import test_fixed_copy_type_exceptions as copy_controls


class ClassicFlashCastTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TemporaryCastRestrictionTests.session
    add=controls.TemporaryCastRestrictionTests.add
    prepare=controls.TemporaryCastRestrictionTests.prepare
    checkpoint=controls.TemporaryCastRestrictionTests.checkpoint
    resolve=controls.TemporaryCastRestrictionTests.resolve
    replay=controls.TemporaryCastRestrictionTests.replay
    binding=controls.TemporaryCastRestrictionTests.binding
    priority=controls.TemporaryCastRestrictionTests.priority
    cast=controls.TemporaryCastRestrictionTests.cast
    top=controls.TemporaryCastRestrictionTests.top
    private_replay=controls.TemporaryCastRestrictionTests.private_replay
    advance=controls.TemporaryCastRestrictionTests.advance
    offered=controls.TemporaryCastRestrictionTests.offered
    choose_copy=copy_controls.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'classic-flash.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/classic-flash-cards.json',ROOT/'tests/fixtures/additional-conditional-modifier-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',ROOT/'tests/fixtures/temporary-cast-restriction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Classic instant permissions',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def offered_cast(self,session,source,payment,targets=(),**choices):
        self.priority(session,'A');matches=[r for r in self.offered(session,'A') if r['id']=='cast:'+source.ref]
        self.assertEqual(1,len(matches),'The complete original must offer its legal cast')
        command={'action_id':matches[0]['id'],'targets':list(targets),'pay':'manual','payment':payment,**choices}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def next_cleanup_trigger(self,session,source):
        self.advance(session,lambda s:s.state.phase=='ending' and s.state.step=='cleanup'
            and any(item.source_object_id==source.object_id and item.kind=='triggered_ability' for item in s.state.stack))
        matches=[item for item in session.state.stack if item.source_object_id==source.object_id and item.kind=='triggered_ability']
        self.assertEqual(1,len(matches));self.assertEqual('A',matches[0].controller)

    def resolve_to_depth(self,session,remaining):
        for _ in range(48):
            if len(session.state.stack)<=remaining:return None
            decision=session.state.pending_decision
            if decision.kind!='priority':return decision
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Expected the next original spell to resolve')

    def test_actual_lightning_reflexes_off_turn_grants_stats_then_sacrifices_at_next_cleanup(self):
        session=self.session(751500);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Lightning Reflexes must admit')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref])
        self.assertEqual(1,len(session.state.delayed_triggers),'Intrinsic permission creates its delayed ability while the original spell is on the stack')
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual((3,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual((2,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]));self.private_replay(session,private)

    def test_actual_parapet_off_turn_uses_permission_and_preserves_its_controller_anthem(self):
        session=self.session(751501);source=self.add(session.engine,'Parapet',zone='hand')
        own=self.add(session.engine,'Grizzly Bears');foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='foreign-parapet-creature')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Parapet must admit')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'W':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(3,session.engine._numeric_stat(own.object_id,'toughness'));self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'toughness'))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(own.object_id,'toughness'));self.private_replay(session,private)

    def test_six_more_originals_keep_each_full_body_entry_choice_and_one_cleanup_batch(self):
        session=self.session(751510);private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        body=self.add(session.engine,'Grizzly Bears')
        cases=(('Spider Climb',{'G':1},(2,5),'reach'),('Armor of Thorns',{'G':1,'C':1},(4,7),'reach'),
            ('Soar',{'U':1,'C':1},(4,8),'flying'),('Ward of Lights',{'W':2},(4,8),'flying'),
            ('Grave Servitude',{'B':1,'C':1},(5,5),'flying'),('Mystic Veil',{'U':1,'C':1},(5,5),'shroud'))
        sources=[]
        for name,payment,stats,keyword in cases:
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')
            sources.append((source,payment,stats,keyword))
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'G':2,'U':2,'B':1,'W':2,'C':4});self.checkpoint(session)
        for source,payment,_,_ in reversed(sources):self.offered_cast(session,source,payment,[body.ref],timing_permission='intrinsic')
        self.assertEqual(6,len(session.state.delayed_triggers));session=self.replay(session,load=True)
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        for index,(source,_,stats,keyword) in enumerate(sources):
            remaining=5-index;decision=self.resolve_to_depth(session,remaining)
            if source.printed_name=='Ward of Lights':
                self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
                options=[row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if row['id'].endswith(':W')]
                self.assertEqual(1,len(options));session=self.replay(session,load=True)
                result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':options[0]}});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve_to_depth(session,remaining))
            else:self.assertIsNone(decision)
            self.assertEqual('battlefield',session.state.cards[source.object_id].zone,source.printed_name)
            self.assertEqual(stats,(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
            self.assertIn(keyword,session.engine._combat_keywords(session.state.cards[body.object_id]))
            data=session.engine._effective_card_data(session.state.cards[body.object_id])
            if source.printed_name=='Grave Servitude':
                self.assertEqual({'B'},set(data['colors']))
                self.assertEqual('graveyard',session.state.cards[sources[1][0].object_id].zone,'Black creature makes Armor of Thorns illegal')
            if source.printed_name=='Ward of Lights':
                specs=protection_specs(ability_fragment_from_dict(r) for r in data.get('ability_fragments',()))
                self.assertEqual(['W'],[spec.quality for spec in specs])
        self.assertEqual('battlefield',session.state.cards[sources[3][0].object_id].zone,'White protection retains its own Ward')
        self.assertEqual(5,sum(session.state.cards[source.object_id].zone=='battlefield' for source,_,_,_ in sources))
        self.advance(session,lambda s:s.state.phase=='ending' and s.state.step=='cleanup' and s.state.pending_decision is not None
            and (s.state.pending_decision.kind=='trigger.order' or any(i.kind=='triggered_ability' for i in s.state.stack)))
        if session.state.pending_decision.kind=='trigger.order':
            refs=[item.ref for item in session.state.pending_trigger_batches[0].items];self.assertEqual(6,len(refs))
            session=self.replay(session,load=True)
            result=session.act('pilot:A',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        self.assertEqual(6,len(session.state.stack));self.assertIsNone(self.resolve(session))
        for source,_,_,_ in sources:self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual((2,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertFalse(session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertEqual({'G'},set(session.engine._effective_card_data(session.state.cards[body.object_id])['colors']))
        self.assertFalse(any(t.active for t in session.state.delayed_triggers));self.private_replay(session,private)

    def test_normal_main_phase_cast_retains_its_aura_without_a_delayed_sacrifice(self):
        session=self.session(751511);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref]);self.assertEqual([],session.state.delayed_triggers)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));turn=session.state.turn_sequence
        self.advance(session,lambda s:s.state.turn_sequence>turn and s.state.phase=='precombat_main')
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.assertIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.private_replay(session,private)

    def test_independent_flash_and_explicit_intrinsic_routes_preserve_ruling_and_costs(self):
        for intrinsic in (False,True):
            with self.subTest(intrinsic=intrinsic):
                session=self.session(751512+int(intrinsic));source=self.add(session.engine,'Lightning Reflexes',zone='hand')
                orrery=self.add(session.engine,'Vedalken Orrery');body=self.add(session.engine,'Grizzly Bears')
                bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,orrery)['strict_capability_ready'])
                self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':1,'C':1});session.state.players['B'].mana_pool.update({'U':2})
                self.checkpoint(session);self.priority(session,'A');matches=[r for r in self.offered(session,'A') if r['id']=='cast:'+source.ref]
                self.assertEqual(1,len(matches));action=matches[0]
                self.assertEqual(['normal','intrinsic'],action['choice_schema']['timing_permission']['legal_values'])
                normal=[r for r in action['cost_options'] if r['id']=='normal'];self.assertEqual(1,len(normal));self.assertEqual(1,normal[0]['requirements']['GENERIC'])
                before=authoritative_state_hash(session.state)
                result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'timing_permission':'unowned','pay':'manual','payment':{'R':1,'C':1}})
                self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                self.offered_cast(session,source,{'R':1,'C':1},[body.ref],**({'timing_permission':'intrinsic'} if intrinsic else {}))
                self.assertEqual(int(intrinsic),len(session.state.delayed_triggers))
                data=session.engine._effective_card_data(session.state.cards[source.object_id]);self.assertNotIn('flash',{str(k).casefold() for k in data.get('keywords',())})
                self.cast(session,'B',bounce,[orrery.ref],{'U':2});self.top(session);self.assertIsNone(self.resolve(session))
                self.assertEqual('hand',session.state.cards[orrery.object_id].zone)
                if intrinsic:
                    self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                    self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                else:
                    turn=session.state.turn_sequence;self.advance(session,lambda s:s.state.turn_sequence>turn and s.state.phase=='precombat_main')
                    self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.private_replay(session,private)

    def test_countered_original_retains_one_noop_delayed_trigger_and_cleanup_priority(self):
        session=self.session(751514);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':1,'C':1});session.state.players['B'].mana_pool.update({'U':2});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref]);self.assertEqual(1,len(session.state.delayed_triggers))
        self.cast(session,'B',counter,[session.state.stack[-1].ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.next_cleanup_trigger(session,source);self.assertEqual('priority',session.state.pending_decision.kind)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertFalse(any(t.active for t in session.state.delayed_triggers));self.private_replay(session,private)

    def test_old_cleanup_reference_does_not_sacrifice_a_returned_and_recast_incarnation(self):
        session=self.session(751515);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        self.add(session.engine,'Vedalken Orrery');body=self.add(session.engine,'Grizzly Bears')
        bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':2,'C':2});session.state.players['B'].mana_pool.update({'U':2});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref],timing_permission='intrinsic');self.assertIsNone(self.resolve(session))
        old=session.state.cards[source.object_id].logical_object_id
        self.cast(session,'B',bounce,[source.ref],{'U':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref],timing_permission='normal');session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old,session.state.cards[source.object_id].logical_object_id);self.assertEqual(1,len(session.state.delayed_triggers))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(3,session.engine._numeric_stat(body.object_id,'power'));self.private_replay(session,private)

    def test_current_permanent_controller_sacrifices_with_original_delayed_controller_retained(self):
        session=self.session(751516);source=self.add(session.engine,'Parapet',zone='hand')
        control=self.add(session.engine,'Confiscate',seat='B',zone='hand')
        own=self.add(session.engine,'Grizzly Bears');foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='confiscate-anthem-recipient')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,control)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B',mana={'U':2,'C':4});session.state.players['A'].mana_pool.update({'W':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'B',control,[source.ref],{'U':2,'C':4});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertEqual((2,3),(session.engine._numeric_stat(own.object_id,'toughness'),session.engine._numeric_stat(foreign.object_id,'toughness')))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True)
        with patch.object(session.engine,'_dispatch_semantic_event',wraps=session.engine._dispatch_semantic_event) as observed:
            self.assertIsNone(self.resolve(session))
        sacrifices=[c.args[1] for c in observed.call_args_list if len(c.args)>1 and c.args[0]=='permanent.sacrificed' and c.args[1].get('card')==source.ref]
        self.assertEqual(1,len(sacrifices));self.assertEqual('B',sacrifices[0]['previous_controller'])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('graveyard',session.state.cards[control.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'toughness'));self.private_replay(session,private)

    def test_copying_the_permanent_copies_anthem_without_copying_its_accepted_cast(self):
        session=self.session(751517);source=self.add(session.engine,'Parapet',zone='hand')
        clone=self.add(session.engine,'Copy Enchantment',zone='hand');self.add(session.engine,'Vedalken Orrery')
        body=self.add(session.engine,'Grizzly Bears');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,clone)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'W':1,'U':1,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1},timing_permission='intrinsic');self.assertIsNone(self.resolve(session))
        self.offered_cast(session,clone,{'U':1,'C':2});session=self.choose_copy(session,source)
        self.assertEqual(1,len(session.state.delayed_triggers),'Copying characteristics does not repeat the prior accepted cast')
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'toughness'))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('battlefield',session.state.cards[clone.object_id].zone)
        self.assertEqual(3,session.engine._numeric_stat(body.object_id,'toughness'));self.private_replay(session,private)

    def test_intrinsic_timing_cannot_override_a_resolved_casting_prohibition(self):
        session=self.session(751518);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');silence=self.add(session.engine,'Silence',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'W':1});session.state.players['A'].mana_pool.update({'R':1,'C':1});self.checkpoint(session)
        self.cast(session,'B',silence,payment={'W':1});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        self.assertFalse(any(a['id']=='cast:'+source.ref for a in self.offered(session,'A')))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':[body.ref],'timing_permission':'intrinsic','pay':'manual','payment':{'R':1,'C':1}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual([],session.state.delayed_triggers);self.private_replay(session,private)

    def test_resolution_cast_permission_waives_mana_without_using_intrinsic_timing(self):
        session=self.session(751519);source=self.add(session.engine,'Violent Outburst',zone='hand')
        aura=self.add(session.engine,'Lightning Reflexes',zone='hand');body=self.add(session.engine,'Grizzly Bears')
        session.engine.move_card(aura.object_id,'library',position='top',log=False)
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':1,'G':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'G':1,'C':1});decision=self.resolve(session)
        self.assertIsNotNone(decision);self.assertEqual('selection.exile_cast',decision.kind)
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'cast','targets':[body.ref]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',session.state.cards[aura.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.assertEqual([],session.state.delayed_triggers)
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[aura.object_id].zone)
        turn=session.state.turn_sequence;self.advance(session,lambda s:s.state.turn_sequence>turn and s.state.phase=='precombat_main')
        self.assertEqual('battlefield',session.state.cards[aura.object_id].zone);self.private_replay(session,private)

    def test_unavailable_normal_route_unknown_origin_and_short_payment_reject_without_schedule(self):
        session=self.session(751520);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'R':1,'C':1});self.checkpoint(session);self.priority(session,'A')
        actions=[a for a in self.offered(session,'A') if a['id']=='cast:'+source.ref];self.assertEqual(1,len(actions))
        self.assertEqual(['intrinsic'],actions[0]['choice_schema']['timing_permission']['legal_values'])
        command={'action_id':actions[0]['id'],'targets':[body.ref],'pay':'manual','payment':{'R':1,'C':1}}
        before=authoritative_state_hash(session.state)
        for change in ({'timing_permission':'normal'},{'timing_permission':True},{'from':'graveyard'},{'payment':{'R':1}}):
            result=session.act('pilot:A',{**command,**change});self.assertFalse(result.ok,change)
            self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual([],session.state.delayed_triggers)
        self.offered_cast(session,source,{'R':1,'C':1},[body.ref],timing_permission='intrinsic')
        self.assertEqual(1,len(session.state.delayed_triggers));self.assertIsNone(self.resolve(session));self.private_replay(session,private)

    def test_canonically_verified_intrinsic_permission_withdrawal_keeps_complete_originals_residual(self):
        from quorune.cast_timing import CLASSIC_FLASH_CAPABILITY as cap
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'));known=set(discover_tests(ROOT).values())
        for withdrawn in (cap,'trigger.placement.apnap'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==withdrawn).update(status='blocked',blockers=['withdrawn intrinsic timing/cleanup dependency'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Spider Climb','Lightning Reflexes','Armor of Thorns','Mystic Veil','Parapet','Grave Servitude','Soar','Ward of Lights'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+withdrawn+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_cast_incarnation_and_current_controller_guards_have_actual_consequence_mutants(self):
        from quorune.rules.classic_flash import schedule_delayed_trigger as schedule
        def mutated(field,value):
            def run(host,**kwargs):
                data=deepcopy(kwargs)
                for effect in data['stack_template']['context']['dynamic_effects']:
                    if value is None:effect.pop(field,None)
                    else:effect[field]=value
                return schedule(host,**data)
            return run
        for method,omission in (
                ('test_old_cleanup_reference_does_not_sacrifice_a_returned_and_recast_incarnation',mutated('expected_zone_change_counter',None)),
                ('test_current_permanent_controller_sacrifices_with_original_delayed_controller_retained',mutated('required_controller','A'))):
            with patch('quorune.rules.classic_flash.schedule_delayed_trigger',omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_intrinsic_permission_marker_and_descriptor_reject_malformed_authority(self):
        from quorune.rules.classic_flash import ClassicFlashCastMark
        from quorune.semantic_runtime.cast_permissions import ClassicFlashPermissionHandler
        from quorune.cast_timing import CLASSIC_FLASH_HANDLER,CastTimingPermission
        mark=ClassicFlashCastMark();self.assertEqual(mark,ClassicFlashCastMark.from_dict(mark.to_dict()))
        for changed in ({**mark.to_dict(),'schema_version':True},{**mark.to_dict(),'used_intrinsic_permission':1},
                {**mark.to_dict(),'used_intrinsic_permission':False},{**mark.to_dict(),'extra':1}):
            with self.assertRaises(ValueError):ClassicFlashCastMark.from_dict(changed)
        handler=ClassicFlashPermissionHandler();descriptor={'handler_id':CLASSIC_FLASH_HANDLER,'schema_version':1,
            'event':'cast.permission','permission':CastTimingPermission().to_dict()}
        self.assertEqual((CastTimingPermission(),),handler.lower(descriptor,None))
        for changed in ({**descriptor,'schema_version':True},{**descriptor,'event':'resolve'},{**descriptor,'duration':'end_step'}):
            with self.assertRaises(ValueError):handler.validate(changed)

    def test_emitted_permission_independently_requires_cleanup_owner_at_binding(self):
        from quorune.cast_timing import CLASSIC_FLASH_CAPABILITY as cap
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(751502);source=self.add(session.engine,'Lightning Reflexes',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_full_original_permission_producer_and_accepted_cast_schedule_omissions_are_killed(self):
        method='test_actual_lightning_reflexes_off_turn_grants_stats_then_sacrifices_at_next_cleanup'
        for owner,omission in (('quorune.compiler.classic_flash.classic_flash_node',lambda **kwargs:None),
                ('quorune.rules.classic_flash.schedule_delayed_trigger',lambda *a,**kwargs:None)):
            with patch(owner,omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

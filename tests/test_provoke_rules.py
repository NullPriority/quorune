from __future__ import annotations

"""CR 702.39/509.1c: one optional, targeted, combat-bound block requirement."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database
import test_paid_color_entry as controls
from quorune.rules.evidence_storage import read_evidence_document


class ProvokeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PaidColorEntryTests.session
    add=controls.PaidColorEntryTests.add
    prepare=controls.PaidColorEntryTests.prepare
    checkpoint=controls.PaidColorEntryTests.checkpoint
    actions=controls.PaidColorEntryTests.actions
    resolve=controls.PaidColorEntryTests.resolve
    replay=controls.PaidColorEntryTests.replay
    binding=controls.PaidColorEntryTests.binding
    advance=controls.PaidColorEntryTests.advance
    private_replay=controls.PaidColorEntryTests.private_replay
    priority=controls.PaidColorEntryTests.priority
    cast_paid=controls.PaidColorEntryTests.cast_paid

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'provoke.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/provoke-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Provoke combat',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def attack(self,session,attacks):
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':attacks});self.assertTrue(result.ok,result.summary)

    def provoke(self,session,target,*,choice='apply'):
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertIsNotNone(decision)
        result=session.act('pilot:A',{'action_id':'choose','choice':choice});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def blockers(self,session):
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')

    def block(self,session,blocks,*,valid=True):
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:B',{'action_id':'block','blocks':blocks});self.assertEqual(valid,result.ok,result.summary)
        if not valid:self.assertEqual(before,authoritative_state_hash(session.state))

    def activate(self,session,source):
        self.priority(session,'A')
        action=next((r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability')),None)
        self.assertIsNotNone(action)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def test_declining_does_not_untap_or_require_a_block(self):
        session=self.session(749110);attacker=self.add(session.engine,'Goblin Grappler');attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        self.add(session.engine,'Wind Drake',seat='B')
        self.prepare(session);self.checkpoint(session);self.attack(session,{attacker.ref:'B'})
        self.provoke(session,target,choice='decline')
        self.assertTrue(target.tapped);self.assertFalse(session.state.combat.blocking_requirements)
        self.blockers(session);self.block(session,{})
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(39,session.state.players['B'].life);self.replay(session)

    def test_stun_replaces_the_untap_and_impossible_requirement_does_not_force_a_block(self):
        session=self.session(749111);attacker=self.add(session.engine,'Goblin Grappler');attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True;target.counters['stun']=1
        self.add(session.engine,'Wind Drake',seat='B')
        self.prepare(session);self.checkpoint(session);self.attack(session,{attacker.ref:'B'});self.provoke(session,target)
        self.assertTrue(target.tapped);self.assertEqual(0,target.counters.get('stun',0));self.assertEqual(1,len(session.state.combat.blocking_requirements))
        session=self.replay(session,load=True);self.blockers(session);self.block(session,{})
        self.advance(session,lambda s:s.state.phase=='postcombat_main');self.assertEqual(39,session.state.players['B'].life);self.replay(session)

    def test_flying_restriction_keeps_ground_target_legal_but_unable_to_block(self):
        session=self.session(749112);attacker=self.add(session.engine,'Swooping Talon');attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        self.add(session.engine,'Wind Drake',seat='B')
        self.prepare(session);self.checkpoint(session);self.attack(session,{attacker.ref:'B'});self.provoke(session,target)
        self.assertFalse(target.tapped);self.assertEqual(1,len(session.state.combat.blocking_requirements))
        self.blockers(session);self.block(session,{target.ref:[attacker.ref]},valid=False);self.block(session,{})
        self.advance(session,lambda s:s.state.phase=='postcombat_main');self.assertEqual(38,session.state.players['B'].life);self.replay(session)

    def test_seven_originals_admit_complete_source_and_retain_their_other_combat_bodies(self):
        names=('Brontotherium','Crested Craghorn','Deftblade Elite','Goblin Grappler','Krosan Vorine','Lowland Tracker','Swooping Talon')
        for index,name in enumerate(names):
            with self.subTest(name=name):self.original_body(name,index)

    def original_body(self,name,index):
        session=self.session(749120+index)
        attacker=self.add(session.engine,name,zone='hand' if name=='Crested Craghorn' else 'battlefield')
        if name!='Crested Craghorn':attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        extra=self.add(session.engine,'Grizzly Bears',seat='B',ref='vorine-extra-blocker') if name=='Krosan Vorine' else None
        ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);self.assertTrue(self.binding(session,attacker)['strict_capability_ready'])
        for face in ir.faces:
            for node in face.nodes:self.assertEqual(node.text,face.oracle_text[node.span.start:node.span.end])
        self.prepare(session,mana={'W':2,'R':1,'C':5});self.checkpoint(session)
        if name=='Crested Craghorn':self.cast_paid(session,attacker,{'R':1,'C':4});self.assertIsNone(self.resolve(session))
        if name in ('Deftblade Elite','Swooping Talon'):self.activate(session,attacker)
        if name=='Swooping Talon':self.assertNotIn('flying',session.engine._combat_keywords(attacker))
        self.attack(session,{attacker.ref:'B'});self.provoke(session,target);self.blockers(session)
        if extra:self.block(session,{target.ref:[attacker.ref],extra.ref:[attacker.ref]},valid=False)
        self.block(session,{target.ref:[attacker.ref]})
        if name=='Brontotherium':
            self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.damage')
            result=session.act('pilot:A',{'action_id':'assign_damage','assignments':[
                {'source':attacker.ref,'target':target.ref,'amount':2},{'source':attacker.ref,'target':'B','amount':3}]})
            self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        source=session.state.cards[attacker.object_id];blocker=session.state.cards[target.object_id]
        if name=='Deftblade Elite':
            self.assertEqual(('battlefield','battlefield'),(source.zone,blocker.zone))
            self.assertEqual((0,0),(source.marked_damage,blocker.marked_damage))
        elif name=='Lowland Tracker':self.assertEqual(('battlefield','graveyard'),(source.zone,blocker.zone));self.assertEqual(0,source.marked_damage)
        elif name=='Goblin Grappler':self.assertEqual(('graveyard','battlefield'),(source.zone,blocker.zone))
        elif name in ('Crested Craghorn','Krosan Vorine'):self.assertEqual(('graveyard','graveyard'),(source.zone,blocker.zone))
        else:self.assertEqual(('battlefield','graveyard'),(source.zone,blocker.zone));self.assertEqual(2,source.marked_damage)
        self.assertEqual(37 if name=='Brontotherium' else 40,session.state.players['B'].life)
        self.assertFalse(session.state.combat.blocking_requirements);self.replay(session)

    def test_fresh_actual_requirement_commit_and_untap_omissions_are_killed(self):
        method='test_actual_grappler_attack_offers_defending_player_target_even_when_tapped'
        for owner in ('commit_blocking_requirement','untap_permanent'):
            with patch('quorune.rules.blocking_requirements.'+owner,return_value=None):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_two_native_provokes_share_the_maximum_requirement_solver(self):
        session=self.session(749140);one=self.add(session.engine,'Goblin Grappler');one.acquired_control_turn_count=-1
        two=self.add(session.engine,'Goblin Grappler',ref='second-grappler');two.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        self.prepare(session);self.checkpoint(session);self.attack(session,{one.ref:'B',two.ref:'B'})
        for _ in range(5):
            decision=session.state.pending_decision
            if decision.kind=='semantic.target':command={'action_id':'choose','targets':[target.ref]}
            elif decision.kind=='trigger.order':command={'action_id':'order','triggers':[item.ref for item in session.state.pending_trigger_batches[0].items]}
            else:break
            result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(session.state.stack))
        for _ in range(2):
            self.assertIsNotNone(self.resolve(session));result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(2,len(session.state.combat.blocking_requirements))
        session=self.replay(session,load=True);self.blockers(session)
        self.block(session,{},valid=False);self.block(session,{target.ref:[one.ref,two.ref]},valid=False)
        self.block(session,{target.ref:[two.ref]});self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(39,session.state.players['B'].life)
        self.assertEqual(('battlefield','graveyard'),(session.state.cards[one.object_id].zone,session.state.cards[two.object_id].zone));self.replay(session)

    def test_native_blink_does_not_substitute_a_returned_source_or_target(self):
        for index,blink_source in enumerate((True,False)):
            with self.subTest(blink_source=blink_source):
                session=self.session(749150+index);attacker=self.add(session.engine,'Goblin Grappler');attacker.acquired_control_turn_count=-1
                target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
                # Cloudshift can target only a creature the caster controls; use B's copy for the target contrast.
                blink=self.add(session.engine,'Cloudshift',seat='A' if blink_source else 'B',zone='hand')
                self.add(session.engine,'Plains',seat='A' if blink_source else 'B')
                self.prepare(session);self.checkpoint(session);self.attack(session,{attacker.ref:'B'})
                result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
                selected=attacker if blink_source else target;incarnation=selected.logical_object_id
                seat='A' if blink_source else 'B';self.priority(session,seat)
                result=session.act('pilot:'+seat,{'action_id':'cast:'+blink.ref,'targets':[selected.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                decision=self.resolve(session)
                self.assertNotEqual(incarnation,session.state.cards[selected.object_id].logical_object_id)
                if blink_source:
                    self.assertIsNotNone(decision)
                    result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[target.object_id].tapped)
                else:self.assertIsNone(decision)
                self.assertFalse(session.state.combat.blocking_requirements);self.replay(session,load=True)

    def test_requirement_codec_and_projection_lock_incarnations_without_requiring_abilities(self):
        from types import SimpleNamespace
        from quorune.blocking_requirement_model import ResolvedBlockingRequirement,normalize_blocking_requirements
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.model import CombatState
        from quorune.declaration_requirement_runtime import typed_blocker_requirements
        a=SimpleNamespace(object_id='a',logical_object_id='a@0',ref='A1',zone='battlefield',phased_out=False)
        b=SimpleNamespace(object_id='b',logical_object_id='b@0',ref='B1',zone='battlefield',phased_out=False)
        requirement=ResolvedBlockingRequirement('r1',ContinuousObjectIdentity('a','a@0'),ContinuousObjectIdentity('b','b@0'))
        combat=CombatState(attackers={'a':'B'},blocking_requirements=(requirement,))
        host=SimpleNamespace(state=SimpleNamespace(combat=combat,cards={'a':a,'b':b}),_effective_ability_fragments=lambda *args,**kwargs:())
        project=lambda:typed_blocker_requirements(host,{'B1':b},error_type=ValueError)
        self.assertEqual(('B1','A1'),(project()[0].variable,project()[0].option))
        self.assertEqual(combat.to_dict(),CombatState.from_dict(combat.to_dict()).to_dict())
        self.assertNotIn('blocking_requirements',CombatState().to_dict())
        for card in (a,b):
            for field,value in (('logical_object_id','new@1'),('zone','hand'),('phased_out',True)):
                previous=getattr(card,field);setattr(card,field,value);self.assertEqual([],project());setattr(card,field,previous)
        combat.attackers.clear();self.assertEqual([],project())
        for raw in ({**requirement.to_dict(),'schema_version':True},{**requirement.to_dict(),'extra':1},
            {**requirement.to_dict(),'requirement_id':''},{**requirement.to_dict(),'blocker':requirement.attacker.to_dict()}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):ResolvedBlockingRequirement.from_dict(raw)
        with self.assertRaises(ValueError):normalize_blocking_requirements([requirement,requirement])
        with self.assertRaises(ValueError):normalize_blocking_requirements({})

    def test_native_retap_and_ability_loss_preserve_the_correct_resolved_requirement(self):
        for index,retap in enumerate((True,False)):
            with self.subTest(retap=retap):
                session=self.session(749155+index);attacker=self.add(session.engine,'Goblin Grappler');attacker.acquired_control_turn_count=-1
                target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
                self.add(session.engine,'Wind Drake',seat='B')
                response=self.add(session.engine,'Icy Manipulator' if retap else 'Humble',zone='battlefield' if retap else 'hand')
                for i in range(2):self.add(session.engine,'Plains',ref='provoke-response-land-'+str(i))
                self.prepare(session);self.checkpoint(session);self.attack(session,{attacker.ref:'B'});self.provoke(session,target)
                self.priority(session,'A')
                action=next(r for r in self.actions(session) if r['id'].startswith(('activate:' if retap else 'cast:')+response.ref))
                result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref if retap else attacker.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));self.assertEqual(1,len(session.state.combat.blocking_requirements))
                session=self.replay(session,load=True);self.blockers(session)
                if retap:
                    self.assertTrue(session.state.cards[target.object_id].tapped);self.block(session,{})
                else:
                    self.block(session,{},valid=False);self.block(session,{target.ref:[attacker.ref]})
                self.advance(session,lambda s:s.state.phase=='postcombat_main')
                self.assertEqual(39 if retap else 40,session.state.players['B'].life)
                if not retap:
                    self.assertEqual('graveyard',session.state.cards[attacker.object_id].zone);self.assertEqual(0,session.state.cards[target.object_id].marked_damage)
                self.assertFalse(session.state.combat.blocking_requirements);self.replay(session)

    def test_defending_player_target_rechecks_current_control_before_resolution(self):
        session=self.session(749159);attacker=self.add(session.engine,'Goblin Grappler');attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        self.prepare(session);self.attack(session,{attacker.ref:'B'})
        result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
        # Exact target-owner boundary: a legal current control transition precedes the retained resolution checkpoint.
        session.engine.change_control(target.object_id,'C',reason='defending-player revalidation owner witness')
        self.checkpoint(session);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertTrue(session.state.cards[target.object_id].tapped)
        self.assertFalse(session.state.combat.blocking_requirements);self.replay(session)

    def test_fresh_fixed_self_prevention_predicate_omission_is_killed(self):
        import quorune.compiler.prevention_templates as owner
        original=owner._all_combat_damage
        def omitted(text):
            value=original(text)
            if value is None or value[0]!='damage-prevention-all-combat-to-from-self-v1':return value
            name,effects,schema,rules=value
            return name,tuple({k:v for k,v in effect.items() if k!='source_predicate'} for effect in effects),schema,rules
        # The producer tuple holds the original function, so replace just that registered production.
        producers=tuple(omitted if fn is original else fn for fn in owner._PREVENTION_PRODUCTIONS)
        method='test_actual_deftblade_prevents_both_directions_through_the_existing_shield_owner'
        with patch.object(owner,'_PREVENTION_PRODUCTIONS',producers):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_deftblade_prevents_both_directions_through_the_existing_shield_owner(self):
        self.original_body('Deftblade Elite',9)

    def test_verified_withdrawals_and_stripped_runtime_minima_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        path=ROOT/'quorune/rules/capability-registry.json';baseline=json.loads(path.read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Goblin Grappler'),capability_registry=verified(baseline),capability_profile='commander_review').status)
        caps=('trigger.keyword.provoke.fixed','combat.block.requirement_this_combat','effect.choice.optional_fixed','permanent.untap.effect','target.revalidate_resolution')
        for cap in caps:
            raw=json.loads(path.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn Provoke authority'])
            ir=compile_oracle_card(self.db.lookup('Goblin Grappler'),capability_registry=verified(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(749160);source=self.add(session.engine,'Goblin Grappler')
        program,=session.engine.semantics.programs_for_oracle(source.oracle_id)
        for cap in caps:
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        for change in ({'event':'permanent.enter.self'},{'event_condition':None},{'target_schema':None},{'effects':[{'op':'untap','card':'$target.0'}]}):
            changed=replace(program,**change);self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_repeated_keyword_instances_are_separate_and_parameter_or_rider_forms_reject(self):
        from dataclasses import replace
        from test_bound_effect_programs import record
        from quorune.compiler.keyword_event_effect_nodes import FixedKeywordEventEffectSpec
        card=replace(record('Provoke\nProvoke','Creature — Goblin'),keywords=('Provoke',),power='1',toughness='1')
        ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual(2,len(ir.faces[0].nodes))
        self.assertEqual(2,len({n.node_id for n in ir.faces[0].nodes}))
        with self.assertRaises(ValueError):FixedKeywordEventEffectSpec('provoke',2)
        for text in ('Provoke 2','Provoke twice','Provoke. Draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(card,oracle_text=text),capability_registry=self.registry,capability_profile='commander_review').status)

    def test_actual_grappler_attack_offers_defending_player_target_even_when_tapped(self):
        session=self.session(749100);attacker=self.add(session.engine,'Goblin Grappler')
        attacker.acquired_control_turn_count=-1
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.tapped=True
        foreign=self.add(session.engine,'Grizzly Bears',seat='C',ref='foreign-provoke-creature')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':'attack','attacks':{attacker.ref:'B'}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'attack','attacks':{attacker.ref:'B'}});self.assertTrue(result.ok,result.summary)
        decision=session.state.pending_decision;self.assertIsNotNone(decision)
        self.assertEqual('semantic.target',decision.kind)
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertIn(target.ref,offered);self.assertNotIn(foreign.ref,offered)
        self.assertTrue(any(item.source_object_id==attacker.object_id and item.kind=='triggered_ability' for item in session.state.stack))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':[foreign.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:D',{'action_id':'choose','targets':[target.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);choice=self.resolve(session);self.assertIsNotNone(choice)
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[target.object_id].tapped)
        self.assertEqual(1,len(session.state.combat.blocking_requirements))
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'block','blocks':{target.ref:[attacker.ref]}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual('graveyard',session.state.cards[attacker.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(40,session.state.players['B'].life);self.assertFalse(session.state.combat.blocking_requirements)
        self.private_replay(session,private)

    def test_fresh_provoke_trigger_producer_omission_is_killed(self):
        method='test_actual_grappler_attack_offers_defending_player_target_even_when_tapped'
        import quorune.compiler.keyword_event_effect_nodes as owner
        original=owner.fixed_keyword_event_effect_spec
        def without_provoke(text):
            value=original(text);return None if value is not None and value.keyword=='provoke' else value
        with patch.object(owner,'fixed_keyword_event_effect_spec',side_effect=without_provoke):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

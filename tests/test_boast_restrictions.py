from __future__ import annotations

"""CR 702.142a: Boast retains its source-attack and once-per-turn limits."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as base
import test_self_public_zone_casting as controls
import test_temporary_keyword_removal as turns


class BoastRestrictionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=base.BoundEffectProgramRuntimeTests.session
    add=base.BoundEffectProgramRuntimeTests.add
    checkpoint=base.BoundEffectProgramRuntimeTests.checkpoint
    resolve=base.BoundEffectProgramRuntimeTests.resolve
    replay=base.BoundEffectProgramRuntimeTests.replay
    prepare=controls.SelfPublicZoneRuntimeTests.prepare
    cast=controls.SelfPublicZoneRuntimeTests.cast
    assert_closed=controls.SelfPublicZoneRuntimeTests.assert_closed
    advance=turns.TemporaryKeywordRemovalRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'boast.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/boast-restriction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Boast restrictions',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def actions(self,session):return session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']

    def ability(self,session,source):
        return next((r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':')),None)

    def test_actual_duskwielder_rejects_boast_before_attacking_without_any_cost_or_effect(self):
        session=self.session(751100);source=self.add(session.engine,'Duskwielder');source.acquired_control_turn_count=-1
        self.assert_closed(session,source)
        self.prepare(session,mana={'B':2});self.checkpoint(session)
        ability=session.engine._activated_abilities(source)[0]
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'activate:'+source.ref+':'+ability.ability_id,'targets':['C'],'pay':'auto'})
        self.assertFalse(result.ok,'Boast must reject before this source incarnation attacks during this turn')
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual((40,40),(session.state.players['A'].life,session.state.players['C'].life))
        offered=self.ability(session,source)
        self.assertTrue(offered is None or offered.get('status')!='payable')

    def test_actual_tapped_attacker_boasts_once_and_resolves_full_life_transfer_with_private_replay(self):
        import json
        session=self.session(751101);source=self.add(session.engine,'Duskwielder');source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for i in range(4):self.add(session.engine,'Swamp',ref='boast-mana-'+str(i))
        self.prepare(session);self.checkpoint(session)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.assertTrue(source.tapped)
        self.advance(session,lambda s:s.state.priority_player=='A');ability=self.ability(session,source);self.assertIsNotNone(ability)
        command={'action_id':ability['id'],'targets':['C'],'pay':'auto'};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((41,39),(session.state.players['A'].life,session.state.players['C'].life))
        self.advance(session,lambda s:s.state.priority_player=='A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)));self.replay(session,load=True)

    def test_fresh_boast_producer_and_actual_source_attack_condition_omissions_are_killed(self):
        method='test_actual_duskwielder_rejects_boast_before_attacking_without_any_cost_or_effect'
        def without_keyword(prefix,effect,conditions):return None,conditions,False
        with patch('quorune.abilities.keyword_activation_restrictions',side_effect=without_keyword):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch('quorune.rules.activation.source_attack.source_attack_condition_status',return_value=('payable',None)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_actual_usage_commit_omission_is_killed(self):
        method='test_actual_tapped_attacker_boasts_once_and_resolves_full_life_transfer_with_private_replay'
        with patch('quorune.rules.activation.payment_commit.commit_activation_usage',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_source_attack_condition_distinguishes_turn_incarnation_controller_and_missing_history(self):
        from types import SimpleNamespace
        from quorune.model import TurnHistory,TurnHistoryEvent
        from quorune.rules.activation.source_attack import source_attack_condition_status
        event=TurnHistoryEvent('creature_attacked',actor='A',object_incarnation='source@0',target='B',target_kind='player')
        state=SimpleNamespace(turn_sequence=4,turn_history=TurnHistory(turn_sequence=4,events=[event]))
        host=SimpleNamespace(state=state);source=SimpleNamespace(zone='battlefield',logical_object_id='source@0',controller='C')
        self.assertEqual(('payable',None),source_attack_condition_status(host,source))
        source.logical_object_id='source@1';self.assertEqual('unavailable',source_attack_condition_status(host,source)[0])
        source.logical_object_id='source@0';source.zone='hand';self.assertEqual('unresolved',source_attack_condition_status(host,source)[0])
        source.zone='battlefield';state.turn_sequence=5;self.assertEqual('unresolved',source_attack_condition_status(host,source)[0])
        state.turn_history=TurnHistory(turn_sequence=5);self.assertEqual('unavailable',source_attack_condition_status(host,source)[0])
        state.turn_history=None;self.assertEqual('unresolved',source_attack_condition_status(host,source)[0])

    def test_implicit_keyword_restrictions_survive_without_reminder_and_have_closed_metadata(self):
        from dataclasses import replace
        from quorune.abilities import parse_activated_abilities,ActivatedAbility,ActivationCondition,ActivationConditionKind
        from quorune.activation_usage import ActivationLimit
        effect='Target opponent loses 1 life and you gain 1 life.'
        for reminder in ('',' (Activate only if this creature attacked this turn and only once each turn.)'):
            ability,=parse_activated_abilities(card_name='Boast metadata fixture',oracle_text='Boast — {1}{B}: '+effect+reminder,keywords=('Boast',))
            self.assertTrue(ability.boast);self.assertEqual(ActivationLimit.ONCE_PER_TURN,ability.activation_limit)
            self.assertEqual((ActivationCondition(ActivationConditionKind.SOURCE_ATTACKED_THIS_TURN),),ability.activation_conditions)
            self.assertEqual(ability.to_dict(),ActivatedAbility.from_dict(ability.to_dict()).to_dict())
        ordinary,=parse_activated_abilities(card_name='Decorative fixture',oracle_text='Ordinary label — {1}{B}: '+effect,keywords=())
        self.assertFalse(ordinary.boast);self.assertIsNone(ordinary.activation_limit);self.assertNotIn('boast',ordinary.to_dict())
        for changes in ({'boast':'yes'},{'activation_conditions':()},{'activation_limit':None},{'zones':('graveyard',)}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(ability,**changes)
        for value in (False,1,'true'):
            with self.subTest(value=value),self.assertRaises(ValueError):ActivatedAbility.from_dict({**ability.to_dict(),'boast':value})

    def test_verified_boast_withdrawal_and_runtime_minimum_stripping_reject(self):
        import json
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        p=ROOT/'quorune/rules/capability-registry.json';baseline=json.loads(p.read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'));known=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Duskwielder'),capability_registry=verified(baseline),capability_profile='commander_review').status)
        caps=('activation.boast.fixed_restrictions','activation.usage.once_per_turn')
        for cap in caps:
            raw=json.loads(p.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn Boast authority'])
            ir=compile_oracle_card(self.db.lookup('Duskwielder'),capability_registry=verified(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(751130);source=self.add(session.engine,'Duskwielder')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects and any(d.get('ability',{}).get('boast') for d in p.handlers))
        for cap in caps:
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_native_blink_rejects_boast_for_a_returned_incarnation_that_did_not_attack(self):
        session=self.session(751140);source=self.add(session.engine,'Duskwielder');source.acquired_control_turn_count=-1
        blink=self.add(session.engine,'Cloudshift',zone='hand');self.add(session.engine,'Plains')
        for i in range(2):self.add(session.engine,'Swamp',ref='blink-boast-mana-'+str(i))
        self.prepare(session);self.checkpoint(session)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.priority_player=='A');ability=self.ability(session,source);self.assertIsNotNone(ability)
        incarnation=source.logical_object_id
        self.cast(session,'A',blink,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(incarnation,session.state.cards[source.object_id].logical_object_id)
        self.advance(session,lambda s:s.state.priority_player=='A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':ability['id'],'targets':['C'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.replay(session)

    def test_actual_white_and_red_boast_originals_create_complete_tokens(self):
        for index,(name,land,types,power,toughness,color) in enumerate((
            ('Usher of the Fallen','Plains',{'human','warrior'},'1','1','W'),
            ('Fearless Liberator','Mountain',{'dwarf','berserker'},'2','1','R'))):
            with self.subTest(name=name):
                session=self.session(751150+index);source=self.add(session.engine,name);source.acquired_control_turn_count=-1
                for i in range(3):self.add(session.engine,land,ref='token-boast-mana-'+str(i))
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
                self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
                result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
                self.advance(session,lambda s:s.state.priority_player=='A');ability=self.ability(session,source);self.assertIsNotNone(ability)
                result=session.act('pilot:A',{'action_id':ability['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens))
                data=session.engine._effective_card_data(tokens[0]);self.assertEqual((power,toughness,[color]),(data['power'],data['toughness'],data['colors']))
                self.assertTrue(types.issubset(session.engine._type_parts(data['type_line'])[1]));self.replay(session)

    def test_native_countered_boast_still_consumes_its_paid_once_per_turn_usage(self):
        session=self.session(751160);source=self.add(session.engine,'Duskwielder');source.acquired_control_turn_count=-1
        counter=self.add(session.engine,'Stifle',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        for i in range(4):self.add(session.engine,'Swamp',ref='countered-boast-mana-'+str(i))
        self.prepare(session);self.checkpoint(session)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.priority_player=='A');ability=self.ability(session,source);self.assertIsNotNone(ability)
        command={'action_id':ability['id'],'targets':['C'],'pay':'auto'}
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((40,40),(session.state.players['A'].life,session.state.players['C'].life))
        self.advance(session,lambda s:s.state.priority_player=='A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.replay(session)

"""Shared public predicates govern activation offers and pre-payment legality."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_cast_spell_mana_values as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.abilities import parse_activated_abilities
from quorune.activation_condition_model import ActivationCondition,ActivationConditionKind,activation_restriction_spec,ACTIVATION_PUBLIC_STATE_CAPABILITY as CAPABILITY
from quorune.compiler.activated_mana_nodes import _activated_effect_material
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.model import CardInstance,TurnHistory,TurnHistoryEvent
from quorune.object_query import object_query_result
from quorune.characteristic_evaluation import type_parts
from quorune.rules.activation.public_state_conditions import activation_public_state_status
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


def state_host():
    players={s:SimpleNamespace(life=40,poison=0,in_game=True,stats={},zones={z:[] for z in ('hand','graveyard','battlefield','library','exile')}) for s in 'ABCD'}
    source=CardInstance(object_id='source',ref='source',oracle_id='generic-source',printed_name='Generic Source',owner='A',controller='A',zone='battlefield')
    state=SimpleNamespace(players=players,turn_order=list('ABCD'),turn_sequence=1,active_player='A',cards={'source':source},monarch=None,turn_history=TurnHistory(turn_sequence=1))
    class Host:
        def __init__(self):self.state=state;self.power='4';self.layers=[]
        def _public_object_query_result(self,card,*,_maximum_layer):
            self.layers.append(_maximum_layer);data={'type_line':'Creature — Soldier','power':self.power,'toughness':'4','colors':[],'keywords':[]}
            return object_query_result(card,data,type_parts=type_parts(data['type_line']),known_to_actor=True,attached_to_ref=None)
    return Host(),source


class ActivationPublicStateCompilerTests(unittest.TestCase):
    def test_new_condition_codec_round_trips_and_preserves_legacy_payloads(self):
        spec=activation_restriction_spec("if this creature's power is 4 or greater",source_name='Generic Source').conditions[0]
        self.assertEqual(spec,ActivationCondition.from_dict(spec.to_dict()));self.assertEqual(2,spec.to_dict()['schema_version'])
        old=ActivationCondition(ActivationConditionKind.CONTROLS_TYPE,minimum=1,card_type='land')
        self.assertEqual({'kind':'controls_type','minimum':1,'card_type':'land'},old.to_dict())
        for change in ({'schema_version':True},{'schema_version':1},{'kind':'controls_type'},{'extra':None}):
            with self.assertRaises(ValueError):ActivationCondition.from_dict({**spec.to_dict(),**change})
        with self.assertRaises(ValueError):replace(spec,minimum=1)

    def test_source_predicate_uses_complete_current_value_and_missing_is_unresolved(self):
        host,source=state_host();condition=activation_restriction_spec("if this creature's power is 4 or greater",source_name='Generic Source').conditions[0]
        self.assertEqual(('payable',None),activation_public_state_status(host,'A',source,condition));self.assertEqual([None],host.layers)
        self.assertEqual('unavailable',activation_public_state_status(host,'B',source,condition)[0])
        host.power='3';self.assertEqual('unavailable',activation_public_state_status(host,'A',source,condition)[0])
        host.power='*';self.assertEqual('unresolved',activation_public_state_status(host,'A',source,condition)[0])
        host.power='0';zero=ActivationCondition(ActivationConditionKind.FIXED_PUBLIC_STATE,public_state=fixed_public_state_condition("this creature's power is 0 or greater",source_name='Generic Source'))
        self.assertEqual(('payable',None),activation_public_state_status(host,'A',source,zero))

    def test_opponent_poison_is_not_sum_or_own_poison_and_history_is_current(self):
        host,source=state_host();host.state.players['A'].poison=4;host.state.players['B'].poison=1;host.state.players['C'].poison=2
        condition=activation_restriction_spec('if an opponent has three or more poison counters',source_name='Generic Source').conditions[0]
        self.assertEqual('unavailable',activation_public_state_status(host,'A',source,condition)[0])
        host.state.players['B'].poison=3;self.assertEqual(('payable',None),activation_public_state_status(host,'A',source,condition))
        host.state.players['B'].in_game=False;self.assertEqual('unavailable',activation_public_state_status(host,'A',source,condition)[0])
        condition=activation_restriction_spec('if five or more creatures died this turn',source_name='Generic Source').conditions[0]
        host.state.turn_history.events=[TurnHistoryEvent('creature_died',actor='C',object_incarnation=str(i),types=('creature',)) for i in range(5)]
        self.assertEqual(('payable',None),activation_public_state_status(host,'A',source,condition))
        host.state.turn_history=None;self.assertEqual('unresolved',activation_public_state_status(host,'A',source,condition)[0])

    def test_named_source_and_complete_suffix_composition_reuse_shared_grammar(self):
        text="{T}: Draw a card. Activate only if Kitsa's power is 3 or greater and only once each turn."
        ability=parse_activated_abilities(card_name='Kitsa, Otterball Elite',oracle_text=text,keywords=())[0]
        self.assertEqual('Draw a card.',_activated_effect_material(ability,source_name='Kitsa, Otterball Elite'))
        self.assertEqual('once_per_turn',ability.activation_limit.value)
        self.assertIsNone(activation_restriction_spec("if Another Card's power is 3 or greater",source_name='Kitsa, Otterball Elite'))
        for tail in ('if you will draw a card this turn','if you control a creature that fought this turn','if this creature is secretly chosen'):
            self.assertIsNone(activation_restriction_spec(tail,source_name='Generic Source'))

    def test_bypassing_current_predicate_mutant_is_killed(self):
        import quorune.rules.activation.public_state_conditions as owner
        host,source=state_host();host.power='3';condition=activation_restriction_spec("if this creature's power is 4 or greater",source_name='Generic Source').conditions[0]
        def witness():self.assertEqual('unavailable',owner.activation_public_state_status(host,'A',source,condition)[0])
        witness()
        with patch.object(owner,'activation_public_state_status',return_value=('payable',None)),self.assertRaises(AssertionError):witness()

    def test_verified_shared_activation_withdrawal_keeps_existing_basic_mana(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=query_record("{T}: Target creature gets +4/+4 until end of turn. Activate only if this creature's power is 4 or greater.")
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['activation predicate owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(replace(record,oracle_text='{T}: Add {G}.'),capability_registry=registry).status)


class ActivationPublicStateNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.CastSpellManaValueNativeTests.session
    add=native.CastSpellManaValueNativeTests.add
    prepare=native.CastSpellManaValueNativeTests.prepare
    cast=native.CastSpellManaValueNativeTests.cast
    cast_for=native.CastSpellManaValueNativeTests.cast_for
    action=native.CastSpellManaValueNativeTests.action
    priority=native.CastSpellManaValueNativeTests.priority
    checkpoint=native.CastSpellManaValueNativeTests.checkpoint
    resolve=native.CastSpellManaValueNativeTests.resolve
    replay=native.CastSpellManaValueNativeTests.replay
    assert_closed=native.CastSpellManaValueNativeTests.assert_closed
    until=native.CastSpellManaValueNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'activation-public-state.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/activation-public-state-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Activation predicates',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def offered(self,session,source,ability_id):
        return next((r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='activate:'+source.ref+':'+ability_id),None)

    def test_runtime_shared_activation_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603637001);source=self.add(session.engine,'Greenhilt Trainee');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_trainee_offers_and_commands_follow_power_but_queued_result_survives_departure(self):
        session=self.session(603637010);source=self.add(session.engine,'Greenhilt Trainee',zone='hand');target=self.add(session.engine,'Grizzly Bears');target.counters['+1/+1']=3
        self.add(session.engine,'Fervor');pump=self.add(session.engine,'Giant Growth',zone='hand');again=self.add(session.engine,'Giant Growth',zone='hand',ref='second-growth');self.add(session.engine,'Forest')
        weaken=self.add(session.engine,'Disfigure',seat='B',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':2});session.state.players['B'].mana_pool.update({'B':1,'U':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'G':1});self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab1'))
        self.cast(session,'A',pump,[source.ref]);self.assertIsNone(self.resolve(session));self.priority(session,'A');old=self.offered(session,source,'ab1');self.assertIsNotNone(old)
        self.cast(session,'B',weaken,[source.ref]);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab1'))
        before=authoritative_state_hash(session.state);command={'action_id':old['id'],'targets':[target.ref],'pay':'auto'}
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',again,[source.ref]);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNotNone(self.offered(session,source,'ab1'))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped)
        self.cast(session,'B',bounce,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone);self.assertEqual(9,session.engine._numeric_stat(target.object_id,'power'))
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_shrine_counter_restriction_is_not_rechecked_after_accepted_activation(self):
        session=self.session(603637020);source=self.add(session.engine,'Luxa River Shrine');source.counters['brick']=3
        remover=self.add(session.engine,'Vampire Hexmage',seat='B');self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        life=session.state.players['A'].life;self.priority(session,'A');offered=self.offered(session,source,'ab2');self.assertIsNotNone(offered)
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.priority(session,'B');remove=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+remover.ref+':'))
        result=session.act('pilot:B',{'action_id':remove['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual({},session.state.cards[source.object_id].counters)
        self.assertEqual(life+2,session.state.players['A'].life);self.replay(session,load=True)

    def test_actual_sphere_uses_one_opponent_poison_threshold_and_closed_mana_output(self):
        session=self.session(603637030);source=self.add(session.engine,'Glistening Sphere');spell=self.add(session.engine,'Prologue to Phyresis',zone='hand')
        session.state.players['A'].poison=4;session.state.players['B'].poison=1;session.state.players['C'].poison=2
        self.assert_closed(session,source);self.assert_closed(session,spell);self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
        self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab4'))
        expected=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        self.cast(session,'A',spell);self.assertIsNone(self.resolve(session));self.priority(session,'A')
        self.assertEqual(3,session.state.players['C'].poison);offered=self.offered(session,source,'ab4');self.assertIsNotNone(offered)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'mana_output':{'G':3}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'G':4}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'G':3}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['G']);self.assertTrue(session.state.cards[source.object_id].tapped)
        for seat in 'BCD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_flywheel_conditional_mana_is_shared_with_automatic_payment_after_crew(self):
        session=self.session(603637040);source=self.add(session.engine,'Flywheel Racer');source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Grizzly Bears');spell=self.add(session.engine,'Giant Growth',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);self.priority(session,'A')
        self.assertIsNone(self.offered(session,source,'ab2'));before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.assertFalse(source.tapped)
        crew=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if a.crew_threshold is not None)
        offered=self.offered(session,source,crew.ability_id);self.assertIsNotNone(offered)
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNotNone(self.offered(session,source,'ab2'))
        self.cast(session,'A',spell,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(5,session.engine._numeric_stat(body.object_id,'power'));self.replay(session,load=True)

    def test_actual_scrounger_history_counts_foreign_token_death_and_excludes_exile(self):
        session=self.session(603637050);source=self.add(session.engine,'Undercity Scrounger',zone='hand');self.add(session.engine,'Fervor')
        create=self.add(session.engine,'Raise the Alarm',seat='B',zone='hand');exile=self.add(session.engine,'Final Reward',zone='hand');kill=self.add(session.engine,'Doom Blade',zone='hand')
        self.add(session.engine,'Vedalken Orrery',seat='B');self.assert_closed(session,source)
        self.prepare(session,mana={'C':7,'B':3});session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':5,'B':2});self.assertIsNone(self.resolve(session));self.cast(session,'B',create);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token];self.assertEqual(2,len(tokens));self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab1'))
        self.cast_for(session,'A',exile,[tokens[0].ref],reserve={'C':1,'B':1});self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab1'))
        self.cast(session,'A',kill,[tokens[1].ref]);self.assertIsNone(self.resolve(session));self.priority(session,'A');offered=self.offered(session,source,'ab1');self.assertIsNotNone(offered)
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(1,sum(c.zone=='battlefield' and c.is_token and 'Treasure' in c.printed_name for c in session.state.cards.values()));self.replay(session,load=True)

    def test_actual_temple_fixed_mana_reuses_existing_control_count_after_land_play(self):
        session=self.session(603637060);source=self.add(session.engine,'Temple of the False God')
        for i in range(3):self.add(session.engine,'Plains',ref='existing-plains-'+str(i))
        fifth=self.add(session.engine,'Plains',zone='hand',ref='fifth-land')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);self.priority(session,'A');self.assertIsNone(self.offered(session,source,'ab1'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'activate:'+source.ref+':ab1','mana_output':{'C':2}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'play-land:'+fifth.ref});self.assertTrue(result.ok,result.summary)
        self.priority(session,'A');offered=self.offered(session,source,'ab1');self.assertIsNotNone(offered)
        result=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'C':2}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['C']);self.assertTrue(session.state.cards[source.object_id].tapped);self.replay(session,load=True)

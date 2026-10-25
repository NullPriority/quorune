from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import TurnHistory
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.turn_cast_grant_model import (
    TurnCastGrantSpec,TURN_CAST_MILL_HANDLER,TURN_CAST_LIBRARY_SHARED_HANDLER,
    TURN_CAST_MILL_CAPABILITY as MILL,TURN_CAST_LIBRARY_SHARED_CAPABILITY as SHARED,
)
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Raul, Trouble Shooter','Kagha, Shadow Archdruid')
HISTORY='zone.mill.graveyard_arrival_history'


class QualifiedGraveyardGrantTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    advance=controls.TurnCastGrantTests.advance
    options=controls.TurnCastGrantTests.options
    grants=controls.TurnCastGrantTests.grants
    use=controls.TurnCastGrantTests.use

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'qualified-graveyard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-graveyard-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified current graveyard permissions',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def top(self,s,name,ref,seat='A'):
        card=self.add(s.engine,name,seat=seat,zone='library',ref=ref)
        library=s.state.players[seat].zones['library'];library.remove(card.object_id);library.append(card.object_id)
        return card

    def activate_mill(self,s,source):
        self.priority(s,'A')
        action=next((a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':')),None)
        self.assertIsNotNone(action)
        command={'action_id':action['id'],'pay':'auto'};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))

    def land_offers(self,s,card):
        self.priority(s,'A')
        return [a for a in self.actions(s) if a.get('kind')=='play_land' and a.get('card')==card.ref]

    def play(self,s,offer):
        command={'action_id':offer['id']};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_have_complete_admission_and_independent_qualifier_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(803000)
        for name,handler,cap in ((NAMES[0],TURN_CAST_MILL_HANDLER,MILL),(NAMES[1],TURN_CAST_LIBRARY_SHARED_HANDLER,SHARED)):
            source=self.add(s.engine,name);bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(d.get('handler_id')==handler for d in p.handlers)),None)
            self.assertIsNotNone(program);deps=tuple(d for d in program.capability_dependencies if d!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            runtime=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(runtime['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,runtime['blockers'])

    def test_legacy_history_and_grant_wires_and_closed_qualification_are_preserved(self):
        from quorune.compiler.turn_cast_grants import turn_cast_grant_handler
        from quorune.turn_history import reset_turn_history,roll_turn_history
        old=TurnHistory().to_dict();self.assertNotIn('mill_history_version',old)
        self.assertEqual(old,TurnHistory.from_dict(old).to_dict())
        history=TurnHistory(turn_sequence=1,mill_history_version=1)
        self.assertEqual(1,reset_turn_history(history,2).mill_history_version)
        self.assertEqual(1,roll_turn_history(history,next_turn_sequence=2,previous_active_player='A').mill_history_version)
        for value in (True,0,2):
            with self.assertRaises(ValueError):TurnHistory(mill_history_version=value)
        plain='Once during each of your turns, you may cast a creature spell from your graveyard.'
        descriptor=turn_cast_grant_handler(plain)[1]['grant'];self.assertEqual(1,descriptor['schema_version'])
        self.assertNotIn('qualification',descriptor);self.assertEqual(descriptor,TurnCastGrantSpec.from_dict(descriptor).to_dict())
        qualified=turn_cast_grant_handler('Once during each of your turns, you may cast a spell from among cards in your graveyard that were milled this turn.')[1]['grant']
        self.assertEqual(3,qualified['schema_version'])
        for key,value in (('qualification','unknown'),('allow_land_play',True),('without_mana_cost',True),('schema_version',False)):
            raw=deepcopy(qualified);raw[key]=value
            with self.assertRaises((ValueError,TypeError)):TurnCastGrantSpec.from_dict(raw)
        for text in ('Once during each of your turns, you may cast a spell from among cards in your graveyard that were milled this game.',
                'Once during each of your turns, you may play a land or cast a permanent spell from among cards in your graveyard that were put there from your hand this turn.'):
            self.assertIsNone(turn_cast_grant_handler(text))

    def test_actual_raul_each_player_mill_records_only_current_owned_arrivals(self):
        s=self.session(803010);source=self.add(s.engine,NAMES[0]);cards={}
        for seat in 'ABCD':cards[seat]=self.top(s,'Lightning Bolt' if seat=='A' else 'Sol Ring','raul-top-'+seat,seat)
        old=self.add(s.engine,'Sol Ring',zone='graveyard',ref='raul-old')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.assertEqual([],self.grants(s,old));self.activate_mill(s,source)
        events=[e for e in s.state.turn_history.events if e.kind=='card_milled_graveyard']
        self.assertEqual(4,len(events));self.assertEqual(set('ABCD'),{e.actor for e in events})
        for seat,card in cards.items():
            self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
            self.assertTrue(any(e.actor==seat and e.object_incarnation==s.state.cards[card.object_id].logical_object_id for e in events))
        own=self.grants(s,cards['A']);self.assertEqual(1,len(own));self.assertEqual([],self.grants(s,cards['B']))
        self.use(s,cards['A'],own[0],{'R':1},['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[cards['A'].object_id].zone);self.assertEqual(37,s.state.players['B'].life)
        self.assertEqual([],self.grants(s,old));self.private_replay(s,private)

    def test_actual_surveil_qualifies_for_kagha_but_is_not_a_raul_mill(self):
        s=self.session(803020);raul=self.add(s.engine,NAMES[0]);kagha=self.add(s.engine,NAMES[1])
        spell=self.add(s.engine,'Consider',zone='hand');card=self.top(s,'Sol Ring','surveil-qualifier')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':1,'C':1});self.checkpoint(s);self.cast(s,'A',spell)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        self.assertNotIn(card.ref,json.dumps(s.packet('pilot:C',full=True)))
        self.reject(s,{'action_id':'choose','cards':{'top':[],'graveyard':[card.ref]}},'pilot:B')
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','cards':{'top':[],'graveyard':[card.ref]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        current=s.state.cards[card.object_id];self.assertEqual('graveyard',current.zone)
        self.assertFalse(any(e.kind=='card_milled_graveyard' and e.object_incarnation==current.logical_object_id for e in s.state.turn_history.events))
        options=self.grants(s,card);self.assertEqual(1,len(options));self.assertEqual(kagha.ref,options[0]['permission_source'])
        self.assertNotEqual(raul.ref,options[0]['permission_source']);self.use(s,card,options[0],{'C':1})
        self.assertIsNone(self.resolve(s));self.private_replay(s,private)

    def test_departure_and_discard_return_do_not_keep_the_old_mill_incarnation(self):
        s=self.session(803030);source=self.add(s.engine,NAMES[0]);card=self.top(s,'Sol Ring','mill-return-card')
        regrowth=self.add(s.engine,'Regrowth',zone='hand');discard=self.add(s.engine,'Thrill of Possibility',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':1,'R':1});self.checkpoint(s);self.activate_mill(s,source)
        old=s.state.cards[card.object_id].logical_object_id;self.assertTrue(self.grants(s,card))
        regrowth_option=next(o for o in self.options(s,regrowth) if o['id']=='normal')
        self.use(s,regrowth,regrowth_option,{'C':1,'G':1},[card.ref]);self.assertIsNone(self.resolve(s))
        option=next(o for o in self.options(s,discard) if o['id']=='normal')
        self.use(s,discard,option,{'C':1,'R':1},discard_cards=[s.state.cards[card.object_id].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[card.object_id];self.assertEqual('graveyard',current.zone);self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual([],self.grants(s,current));self.private_replay(s,private)

    def kagha_attack_arrivals(self,seed,*,extra_land=False,crucible=False):
        s=self.session(seed);source=self.add(s.engine,NAMES[1]);spell=self.top(s,'Sol Ring','kagha-mill-spell')
        land=self.top(s,'Forest','kagha-mill-land');self.add(s.engine,'Generic Bound Plains',ref='kagha-real-mana')
        hand=self.add(s.engine,'Forest',zone='hand',ref='kagha-hand-land');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        if extra_land:self.add(s.engine,'Exploration')
        if crucible:self.add(s.engine,'Crucible of Worlds')
        self.prepare(s);self.checkpoint(s)
        self.advance(s,lambda q:q.state.pending_decision is not None and q.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertIn('deathtouch',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.advance(s,lambda q:q.state.phase=='postcombat_main')
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual('graveyard',s.state.cards[land.object_id].zone)
        return s,source,spell,land,hand,private

    def test_actual_kagha_land_first_consumes_shared_use_and_numeric_land_play(self):
        s,source,spell,land,hand,private=self.kagha_attack_arrivals(803040)
        offers=self.land_offers(s,land);self.assertEqual(1,len(offers));self.assertEqual(source.ref,offers[0]['permission_source'])
        self.play(s,offers[0]);s=self.replay(s,load=True)
        self.assertEqual('battlefield',s.state.cards[land.object_id].zone);self.assertEqual([],self.grants(s,spell))
        self.assertEqual([],self.land_offers(s,hand));self.private_replay(s,private)

    def test_actual_kagha_spell_first_preserves_only_the_ordinary_land_allowance(self):
        s,source,spell,land,hand,private=self.kagha_attack_arrivals(803050)
        options=self.grants(s,spell);self.assertEqual(1,len(options))
        command={'action_id':'cast:'+spell.ref,'cost_option':options[0]['id'],'pay':'auto'}
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual([],self.land_offers(s,land));ordinary=self.land_offers(s,hand);self.assertEqual(1,len(ordinary))
        self.assertNotIn('permission_source',ordinary[0]);self.play(s,ordinary[0]);s=self.replay(s,load=True)
        self.assertEqual('battlefield',s.state.cards[hand.object_id].zone);self.private_replay(s,private)

    def test_history_version_absence_does_not_invent_legacy_mill_facts(self):
        s=self.session(803060);source=self.add(s.engine,NAMES[0]);card=self.top(s,'Sol Ring','legacy-mill-card')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.turn_history.mill_history_version=None;self.prepare(s,mana={'C':1});self.checkpoint(s);self.activate_mill(s,source)
        self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertFalse(any(e.kind=='card_milled_graveyard' for e in s.state.turn_history.events))
        self.assertNotIn('mill_history_version',s.state.turn_history.to_dict());self.assertEqual([],self.grants(s,card))
        self.private_replay(s,private)

    def test_independent_crucible_land_play_preserves_the_shared_kagha_allowance(self):
        s,source,spell,land,hand,private=self.kagha_attack_arrivals(803070,crucible=True)
        offers=self.land_offers(s,land);self.assertEqual(2,len(offers));self.assertEqual(2,len({o['id'] for o in offers}))
        ordinary=next((o for o in offers if not o.get('permission_source')),None);self.assertIsNotNone(ordinary)
        self.play(s,ordinary);self.assertEqual([],self.land_offers(s,hand));options=self.grants(s,spell);self.assertEqual(1,len(options))
        accepted=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'cost_option':options[0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_source_zone_permission_needs_a_numeric_land_play_and_composes_with_exploration(self):
        for extra in (False,True):
            s,source,spell,land,hand,private=self.kagha_attack_arrivals(803080+extra,extra_land=extra)
            ordinary=self.land_offers(s,hand);self.assertEqual(1,len(ordinary));self.play(s,ordinary[0])
            granted=self.land_offers(s,land)
            if extra:
                self.assertEqual(1,len(granted));self.assertEqual(source.ref,granted[0]['permission_source'])
                self.play(s,granted[0]);self.assertEqual([],self.grants(s,spell))
            else:
                self.assertEqual([],granted);options=self.grants(s,spell);self.assertEqual(1,len(options))
                result=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'cost_option':options[0]['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
            s=self.replay(s,load=True);self.private_replay(s,private)

    def test_real_extra_turn_expires_old_provenance_and_new_mill_qualifies(self):
        s=self.session(803090);source=self.add(s.engine,NAMES[0])
        second=self.top(s,'Sol Ring','new-turn-mill');self.top(s,'Generic Bound Plains','new-turn-draw-buffer')
        first=self.top(s,'Sol Ring','old-turn-mill');warp=self.add(s.engine,'Time Warp',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'U':2});self.checkpoint(s);turn=s.state.turn_sequence
        self.activate_mill(s,source);self.assertTrue(self.grants(s,first))
        normal=next(o for o in self.options(s,warp) if o['id']=='normal');self.use(s,warp,normal,{'C':3,'U':2},['A'])
        self.assertIsNone(self.resolve(s));self.advance(s,lambda q:q.state.turn_sequence>turn and q.state.active_player=='A' and q.state.phase=='precombat_main')
        self.assertEqual(1,s.state.turn_history.mill_history_version);self.assertEqual([],self.grants(s,first))
        self.activate_mill(s,s.state.cards[source.object_id]);self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
        self.assertTrue(s.engine._compiled_zone_cast_permission('A',s.state.cards[second.object_id]))
        self.assertFalse(s.engine._compiled_zone_cast_permission('A',s.state.cards[first.object_id]));self.private_replay(s,private)

    def test_fresh_source_land_commit_omission_is_killed_by_shared_cast_availability(self):
        from quorune.rules import source_land_plays as owner
        method='test_actual_kagha_land_first_consumes_shared_use_and_numeric_land_play'
        with patch.object(owner,'commit_source_land_choice',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_verified_history_and_grant_withdrawal_and_fresh_mill_record_omission_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for name,capability in ((NAMES[0],HISTORY),(NAMES[0],MILL),(NAMES[1],SHARED)):
            raw=deepcopy(baseline);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['withdrawn current provenance'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune import turn_history as owner
        method='test_actual_raul_each_player_mill_records_only_current_owned_arrivals'
        with patch.object(owner,'record_milled_graveyard_arrivals',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

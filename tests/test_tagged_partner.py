from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT,keep_all
from quorune.carddb import CardDatabase
from quorune.commander import initial_commander_state
from quorune.commander_pairing import (COMMANDER_PAIRING_TEMPLATE_ID,CommanderPairingError,
    CommanderPairingKind,commander_pairing_declaration,validate_commander_pair)
from quorune.deck import DeckDefinition,DeckEntry,DeckLoader
from quorune.model import GameConfig,GameState
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='format.commander.pairing.tagged'
NAMES=("April O'Neil, Live on the Scene",'Elmar, Ulvenwald Informant','Hargilde, Kindly Runechanter',
       'Bjorna, Nightfall Alchemist','Michelangelo, the Heart','Splinter, the Mentor')
TAGS=('Character select','Father & son','Friends forever','Survivors')
PAIRS=((NAMES[1],NAMES[2]),(NAMES[1],NAMES[3]),(NAMES[2],NAMES[3]),
       (NAMES[0],NAMES[4]),(NAMES[0],NAMES[5]),(NAMES[4],NAMES[5]))


class TaggedPartnerTests(unittest.TestCase):
    prepare=controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint=controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    priority=controls.StaticPlayerRestrictionRuntimeTests.priority
    actions=controls.StaticPlayerRestrictionRuntimeTests.actions
    cast=controls.StaticPlayerRestrictionRuntimeTests.cast
    activate=controls.StaticPlayerRestrictionRuntimeTests.activate
    reject=controls.StaticPlayerRestrictionRuntimeTests.reject
    replay=controls.StaticPlayerRestrictionRuntimeTests.replay
    private_replay=controls.StaticPlayerRestrictionRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'tagged-partner.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/commander-pairing-cards.json',ROOT/'tests/fixtures/tagged-partner-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def registry_for(self,*names):
        registry=SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(self.db,registry,[self.db.lookup(n) for n in names],trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_capability_declarations=True)
        return registry

    def deck(self,pair,controls=('Sol Ring',)):
        entries=[DeckEntry(n,1,'commander') for n in pair]+[DeckEntry(n) for n in controls]
        return DeckDefinition('Typed tagged partner',entries+[DeckEntry('Wastes',100-len(entries))],list(pair))

    def paired_session(self,pair,seed,controls=('Sol Ring',)):
        deck=self.deck(pair,controls)
        self.assertEqual([],DeckLoader(self.db).validate_commander_deck(deck))
        try:s=CommanderSession.create(self.db,{seat:deepcopy(deck) for seat in 'ABCD'},first_player='A',seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        except CommanderPairingError as exc:self.fail('An independently valid matching tag was rejected: '+str(exc))
        keep_all(s)
        s.engine.permissions.invalidate_current();s.state.pending_decision=None;s.state.priority_player=None;s.state.priority_passes=[]
        return s

    def card(self,s,name,seat='A'):
        return next(c for c in s.state.cards.values() if c.owner==seat and c.printed_name==name)

    def resolve(self,s):
        for _ in range(100):
            d=s.state.pending_decision
            if d is not None and d.kind=='state.commander_zone':
                result=s.act(s.pending_principals()[0],{'a':'choose','choice':'command'})
            elif d is not None and d.kind=='replacement.order':
                principal=s.pending_principals()[0]
                options=s.packet(principal,full=True)['decision']['ctx']['options']
                chosen=[r for r in options if r['id'].startswith('rule.commander-zone-replacement:')]
                self.assertEqual(1,len(chosen),options)
                command={'action_id':'choose','replacement':chosen[0]['id']}
                self.reject(s,command,seat='D');result=s.act(principal,command)
            elif d is not None and d.kind!='priority':
                self.fail('Unexpected native decision: '+d.kind+' '+str(s.packet(s.pending_principals()[0],full=True)['decision']['ctx'])[:650])
            elif not s.state.stack:return None
            else:result=s.act(s.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Native paired-commander resolution did not finish')

    def test_original_six_have_complete_normal_admission_and_exact_tag_spans(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        for name in NAMES:
            record=self.db.lookup(name);ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            node=next(n for f in ir.faces for n in f.nodes if n.template_id==COMMANDER_PAIRING_TEMPLATE_ID)
            self.assertEqual((CAP,),node.capability_dependencies);self.assertIn(node.mechanics[0],[k.value for k in CommanderPairingKind if '—' in k.value])
            self.assertEqual(node.text,record.oracle_text[node.span.start:node.span.end])
            program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))

    def test_four_closed_tags_form_only_identical_distinct_legendary_pairs(self):
        names=[f'Generic {tag} Commander {i}' for tag in TAGS for i in (1,2)]
        registry=self.registry_for(*names)
        for first in TAGS:
            for second in TAGS:
                pair=(self.db.lookup(f'Generic {first} Commander 1'),self.db.lookup(f'Generic {second} Commander 2'))
                if first==second:
                    try:result=validate_commander_pair(self.db,registry,pair)
                    except CommanderPairingError as exc:self.fail('Matching closed tag rejected: '+str(exc))
                    self.assertEqual(result[0].kind,result[1].kind)
                else:
                    with self.assertRaises(CommanderPairingError):validate_commander_pair(self.db,registry,pair)
        first,second=(self.db.lookup(n) for n in names[:2])
        for pair in ((first,first),(replace(first,type_line='Creature — Human'),second)):
            with self.assertRaises(CommanderPairingError):validate_commander_pair(self.db,registry,pair)
        for text in ('Partner—Unknown','Partner—Friends forever and Survivors','Partner—','Friends forever','Partner—Friends forever, flying'):
            record=replace(self.db.lookup(names[0]),oracle_text=text,keywords=('Partner',))
            ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertTrue(ir.material_residuals,text)

    def test_real_six_form_native_hundred_card_pairs_and_reject_mixed_variants(self):
        for i,pair in enumerate(PAIRS):
            s=self.paired_session(pair,820100+i)
            for seat in 'ABCD':
                command=[s.state.cards[oid] for oid in s.state.players[seat].zones['command']]
                self.assertEqual(set(pair),{c.printed_name for c in command});self.assertEqual(2,len({c.commander_designation_id for c in command}))
                self.assertTrue(all(c.is_commander for c in command))
                self.assertEqual(set(pair),{r['n'] for r in s.packet('pilot:A',full=True)['state']['players'][seat]['cmd']})
            self.assertEqual(s.state.to_dict(),GameState.from_dict(s.state.to_dict()).to_dict())
        pairs=((NAMES[1],NAMES[0]),(NAMES[1],'Thrasios, Triton Hero'),(NAMES[0],'Wilson, Refined Grizzly'),(NAMES[0],'Rose Tyler'))
        for pair in pairs:
            registry=self.registry_for(*pair)
            with self.assertRaises(CommanderPairingError):validate_commander_pair(self.db,registry,tuple(self.db.lookup(n) for n in pair))

    def test_current_canonical_tag_declaration_rejects_missing_ambiguous_and_stale_authority(self):
        names=PAIRS[0];registry=self.registry_for(*names);record=self.db.lookup(names[0])
        declaration=commander_pairing_declaration(self.db,registry,record);self.assertIsNotNone(declaration)
        original=next(p for p in registry.programs_for_oracle(record.oracle_id) if p.key==declaration.program_key)
        from quorune.commander_pairing import _program_pairing_kind
        self.assertIsNone(_program_pairing_kind(replace(original,capability_dependencies=[],capability_closure=None)))
        self.assertIsNone(_program_pairing_kind(replace(original,coverage=[*original.coverage,'partner—survivors'])))
        wrong=['format.commander.pairing.partner']
        self.assertIsNone(_program_pairing_kind(replace(original,capability_dependencies=wrong,capability_closure=self.registry.closure(wrong,profile='commander_review').to_dict())))
        registry.remove(declaration.program_key);self.assertIsNone(commander_pairing_declaration(self.db,registry,record))
        with self.assertRaises(CommanderPairingError):validate_commander_pair(self.db,registry,tuple(self.db.lookup(n) for n in names))
        stale=SimpleNamespace(by_oracle_id=lambda oid:replace(record,oracle_text='Partner'),rulings=self.db.rulings)
        self.assertIsNone(commander_pairing_declaration(stale,self.registry_for(*names),record))

    def test_native_command_zone_casts_keep_independent_tax_mana_purpose_and_private_replay(self):
        names=('Hargilde, Kindly Runechanter','Elmar, Ulvenwald Informant')
        extras=('Unsummon','Disperse','Boomerang','Expedite','Sol Ring','Divination')
        s=self.paired_session(names,820120,extras);h=self.card(s,names[0]);e=self.card(s,names[1])
        hand={n:self.card(s,n) for n in extras}
        for card in hand.values():
            if card.zone!='hand':s.engine.move_card(card.object_id,'hand',log=False,semantic_events=False)
        private=self.card(s,'Sol Ring','C')
        if private.zone!='hand':s.engine.move_card(private.object_id,'hand',log=False,semantic_events=False)
        identity=(h.commander_designation_id,e.commander_designation_id)
        self.prepare(s,mana={'C':17,'W':3,'U':9,'R':3,'G':2});self.checkpoint(s)
        self.cast(s,h,payment={'C':2,'W':1,'U':1});self.resolve(s)
        self.cast(s,e,payment={'C':1,'R':1,'G':1});self.resolve(s)
        self.cast(s,hand['Expedite'],[h.ref],payment={'R':1});self.resolve(s);self.activate(s,h)
        for bounce,payment,price in ((hand['Unsummon'],{'U':1},{'C':4,'W':1,'U':1}),(hand['Disperse'],{'C':1,'U':1},{'C':6,'W':1,'U':1})):
            self.cast(s,bounce,[h.ref],payment=payment);self.resolve(s);self.assertEqual('command',s.state.cards[h.object_id].zone)
            self.cast(s,h,payment=price);self.resolve(s)
        self.cast(s,hand['Boomerang'],[e.ref],payment={'U':2});self.resolve(s);self.assertEqual('command',s.state.cards[e.object_id].zone)
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id']=='cast:'+e.ref)
        command={'action_id':row['id'],'cost_option':row['cost_options'][0]['id'],'pay':'manual','payment':{'C':1,'R':1,'G':1}}
        self.reject(s,command);s=self.replay(s,load=True)
        self.cast(s,e,payment={'C':3,'R':1,'G':1});self.resolve(s)
        self.assertEqual((3,2),(s.state.players['A'].commander_casts[h.oracle_id],s.state.players['A'].commander_casts[e.oracle_id]))
        self.assertEqual(identity,(s.state.cards[h.object_id].commander_designation_id,s.state.cards[e.object_id].commander_designation_id))
        self.assertEqual(2,s.state.players['A'].mana_pool['C']);self.priority(s,'A')
        self.assertNotIn('cast:'+hand['Divination'].ref,{r['id'] for r in self.actions(s)})
        self.reject(s,{'action_id':'cast:'+hand['Divination'].ref,'pay':'manual','payment':{'C':1,'U':2}})
        self.cast(s,hand['Sol Ring'],payment={'C':1});self.resolve(s);self.activate(s,hand['Sol Ring'])
        self.cast(s,hand['Divination'],payment={'C':1,'U':2});self.resolve(s);self.private_replay(s,private)

    def test_deck_size_and_combined_identity_use_both_designated_commanders(self):
        deck=self.deck(PAIRS[0]);loader=DeckLoader(self.db);self.assertEqual([],loader.validate_commander_deck(deck))
        short=deepcopy(deck);short.entries[-1]=replace(short.entries[-1],quantity=short.entries[-1].quantity-1)
        self.assertTrue(any('expected 100' in issue for issue in loader.validate_commander_deck(short)))
        outside=deepcopy(deck);outside.entries[-1]=replace(outside.entries[-1],quantity=outside.entries[-1].quantity-1);outside.entries.append(DeckEntry('Swamp'))
        self.assertTrue(any('outside commander identity' in issue for issue in loader.validate_commander_deck(outside)))

    def test_tagged_commander_draw_activation_retains_both_identities_and_private_replay(self):
        pair=('Generic Tagged Draw Commander','Hargilde, Kindly Runechanter')
        s=self.paired_session(pair,820130);source=self.card(s,pair[0]);other=self.card(s,pair[1])
        private=self.card(s,'Sol Ring','C')
        if private.zone!='hand':s.engine.move_card(private.object_id,'hand',log=False,semantic_events=False)
        identities=(source.commander_designation_id,other.commander_designation_id)
        self.prepare(s,mana={'C':2});self.checkpoint(s)
        self.cast(s,source,payment={'C':1});self.resolve(s)
        before=set(s.state.players['A'].zones['hand']);self.priority(s,'A')
        row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        self.reject(s,{'action_id':row['id'],'pay':'manual','payment':{'C':1}},seat='D')
        self.activate(s,source);s=self.replay(s,load=True);self.resolve(s)
        drawn=set(s.state.players['A'].zones['hand'])-before;self.assertEqual(1,len(drawn))
        drawn_ref=s.state.cards[next(iter(drawn))].ref
        for seat in 'BCD':self.assertNotIn(drawn_ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.assertIn(drawn_ref,json.dumps(s.packet('pilot:A',full=True)))
        self.assertEqual(identities,(s.state.cards[source.object_id].commander_designation_id,s.state.cards[other.object_id].commander_designation_id))
        self.assertEqual(('battlefield','command'),(s.state.cards[source.object_id].zone,s.state.cards[other.object_id].zone))
        self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_match_omissions_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn tagged pairing authority'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=registry,capability_profile='commander_review')
        blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        for value in (False,True):
            with patch('quorune.commander_pairing.tagged_partner_kinds_match',return_value=value):
                with self.assertRaises(AssertionError):type(self)('test_four_closed_tags_form_only_identical_distinct_legendary_pairs').test_four_closed_tags_form_only_identical_distinct_legendary_pairs()

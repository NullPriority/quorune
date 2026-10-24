from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='token.creation.additional_replacement'
NAMES=('Donatello, the Brains','Quina, Qu Gourmet','Queen Allenal of Ruadach','Stridehangar Automaton')


class AdditionalTokenDefinitionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.StaticPlayerRestrictionRuntimeTests.session
    add=controls.StaticPlayerRestrictionRuntimeTests.add
    prepare=controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint=controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    resolve=controls.StaticPlayerRestrictionRuntimeTests.resolve
    replay=controls.StaticPlayerRestrictionRuntimeTests.replay
    private_replay=controls.StaticPlayerRestrictionRuntimeTests.private_replay
    priority=controls.StaticPlayerRestrictionRuntimeTests.priority
    actions=controls.StaticPlayerRestrictionRuntimeTests.actions
    cast=controls.StaticPlayerRestrictionRuntimeTests.cast
    reject=controls.StaticPlayerRestrictionRuntimeTests.reject
    binding=controls.StaticPlayerRestrictionRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'additional-definitions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/additional-token-status-cards.json',ROOT/'tests/fixtures/additional-token-definition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional token definitions',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def tokens(self,s,name,seat='A'):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller==seat and c.printed_name==name]

    def activation(self,s,source,*,targets=(),costs=(),payment=None,seat='A',valid=True):
        self.priority(s,seat)
        row=next(r for r in self.actions(s,seat) if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto' if payment is None else 'manual'}
        if costs:command['cost_cards']=[c.ref for c in costs]
        if payment is not None:command['payment']=payment
        if not valid:self.reject(s,command,seat=seat);return
        self.reject(s,command,seat='D');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def finish_replacements(self,s,*,first_marker=None):
        for i in range(8):
            pending=self.resolve(s)
            if pending is None:return
            self.assertEqual('replacement.order',pending.kind)
            seat=s.pending_principals()[0];options=s.packet(seat,full=True)['decision']['ctx']['options']
            row=next((r for r in options if first_marker in r['label'].casefold()),None) if i==0 and first_marker else options[0]
            self.assertIsNotNone(row,options)
            result=s.act(seat,{'action_id':'choose','replacement':row['id']});self.assertTrue(result.ok,result.summary)
        self.fail('Replacement order did not complete')

    def test_complete_definition_producer_preserves_old_payloads_and_rejects_riders(self):
        from quorune.compiler.token_templates import static_additional_token_replacement_handler as lower,_TOKEN_DEFINITIONS
        from quorune.semantic_runtime.token_replacements import GenericAdditionalTokenReplacementHandler
        for definition in ('Treasure token','Food token','Map token','1/1 colorless Thopter artifact creature token with flying'):
            text='If one or more artifact tokens would be created under your control, those tokens plus an additional '+definition+' are created instead.'
            result=lower(text);self.assertIsNotNone(result);self.assertEqual(_TOKEN_DEFINITIONS[definition.casefold()],result[1]['token'])
        for definition,name in (('Mutagen token','Mutagen'),('1/1 green Frog creature token','Frog'),('1/1 white Soldier creature token','Soldier')):
            result=lower('If one or more creature tokens would be created under your control, those tokens plus a '+definition+' are created instead.')
            self.assertIsNotNone(result);node=GenericAdditionalTokenReplacementHandler().validate(result[1]);self.assertEqual(name,node.token.name);self.assertEqual(('creature',),node.created_types_all);self.assertEqual(1,node.quantity)
        for text in ('If one or more tokens would be created under your control, those tokens plus that many Food tokens are created instead.','If one or more tokens would be created under your control, you may create a Food token instead.','If one or more tokens would be created under your control, those tokens plus a tapped Food token are created instead.','If one or more tokens would be created under your control, those tokens plus a Mystery token are created instead.','If one or more tokens would be created under your control, those tokens plus a 1/1 green Frog creature token, then draw a card are created instead.'):
            self.assertIsNone(lower(text),text)

    def test_all_originals_register_normally_with_independent_replacement_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i,name in enumerate(NAMES):
            s=self.session(821000+i);source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(h.get('handler_id')=='replacement.token.additional.v2' for h in p.handlers))
            deps=[c for c in program.capability_dependencies if c!=CAP]
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            stripped=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(stripped['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,stripped['blockers'])

    def test_only_exact_current_capability_bound_views_supersede_reviewed_programs(self):
        from quorune.card_programs.additional_token_equivalence import shadowed_reviewed_additional_token_keys as shadow
        s=self.session(821010);source=self.add(s.engine,NAMES[3]);record=self.db.lookup(NAMES[3])
        current=s.engine.semantics.programs_for_oracle(source.oracle_id);old=SemanticRegistry().programs_for_oracle(source.oracle_id)
        replacement=next(p for p in old if any(h.get('handler_id')=='replacement.token.additional.v2' for h in p.handlers))
        root=next(p for p in old if p.ability_id=='spell:front')
        self.assertIn(replacement.key,shadow(record,current,old));self.assertIn(root.key,shadow(record,current,old))
        changed=deepcopy(replacement.handlers);changed[0]['quantity']=2
        self.assertFalse(shadow(record,current,[replace(replacement,handlers=changed),root]))
        stale=replace(replacement,provenance={**replacement.provenance,'source_oracle_hash':'0'*64});self.assertFalse(shadow(record,current,[stale,root]))
        provisional=[replace(p,trust_level='provisional') for p in current];self.assertFalse(shadow(record,provisional,old))
        nonempty=replace(root,effects=[{'op':'draw','player':'$controller','count':1}]);self.assertNotIn(root.key,shadow(record,current,[replacement,nonempty]))

    def test_real_donatello_both_orders_mutagen_ability_and_source_departure_replay(self):
        for add_first in (True,False):
            s=self.session(821020+add_first);source=self.add(s.engine,NAMES[0],zone='hand');self.add(s.engine,'Parallel Lives')
            spell=self.add(s.engine,'Chatter of the Squirrel',zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':5,'U':2,'G':2});self.checkpoint(s);self.cast(s,source,payment={'C':2,'U':1});self.assertIsNone(self.resolve(s))
            self.cast(s,spell,payment={'G':1});pending=self.resolve(s);self.assertEqual('replacement.order',pending.kind);s=self.replay(s,load=True)
            options=s.packet(s.pending_principals()[0],full=True)['decision']['ctx']['options']
            row=next(r for r in options if ('mutagen' in r['label'].casefold())==add_first)
            result=s.act('pilot:A',{'action_id':'choose','replacement':row['id']});self.assertTrue(result.ok,result.summary);self.finish_replacements(s)
            squirrels=self.tokens(s,'Squirrel');mutagens=self.tokens(s,'Mutagen')
            self.assertEqual(2,len(squirrels));self.assertEqual(2 if add_first else 1,len(mutagens))
            self.assertEqual({'G'},set(s.engine._effective_card_data(squirrels[0])['colors']));self.assertFalse(s.engine._effective_card_data(mutagens[0])['colors'])
            self.activation(s,mutagens[0],targets=[source.ref],payment={'C':1});self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
            remaining=len(self.tokens(s,'Mutagen'));self.cast(s,bounce,[source.ref],payment={'U':1});self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
            self.cast(s,spell,payment={'C':1,'G':1});self.finish_replacements(s)
            self.assertEqual(remaining,len(self.tokens(s,'Mutagen')));self.assertEqual(4,len(self.tokens(s,'Squirrel')));self.private_replay(s,private)

    def test_real_quina_adds_one_frog_per_event_and_pays_its_frog_price(self):
        s=self.session(821030);source=self.add(s.engine,NAMES[1],zone='hand');spell=self.add(s.engine,'Raise the Alarm',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'G':1,'W':1});self.checkpoint(s);self.cast(s,source,payment={'C':2,'G':1});self.assertIsNone(self.resolve(s))
        self.cast(s,spell,payment={'C':1,'W':1});self.finish_replacements(s)
        self.assertEqual(2,len(self.tokens(s,'Soldier')));frogs=self.tokens(s,'Frog');self.assertEqual(1,len(frogs));self.assertEqual(1,s.engine._numeric_stat(frogs[0].object_id,'power'))
        self.activation(s,source,costs=[self.tokens(s,'Soldier')[0]],payment={'C':2},valid=False)
        self.activation(s,source,costs=[frogs[0]],payment={'C':2});self.assertNotEqual('battlefield',s.state.cards[frogs[0].object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0));self.private_replay(s,private)

    def test_real_queen_filters_creature_tokens_and_updates_its_live_definition(self):
        s=self.session(821040);source=self.add(s.engine,NAMES[2],zone='hand');spell=self.add(s.engine,'Raise the Alarm',zone='hand');inspector=self.add(s.engine,'Thraben Inspector',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'G':1,'W':4});self.checkpoint(s);self.cast(s,source,payment={'G':1,'W':2});self.assertIsNone(self.resolve(s));self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
        self.cast(s,spell,payment={'C':1,'W':1});self.finish_replacements(s);self.assertEqual(3,len(self.tokens(s,'Soldier')));self.assertEqual(4,s.engine._numeric_stat(source.object_id,'power'))
        self.cast(s,inspector,payment={'W':1});self.finish_replacements(s);self.assertEqual(1,len(self.tokens(s,'Clue')));self.assertEqual(3,len(self.tokens(s,'Soldier')));self.assertEqual(5,s.engine._numeric_stat(source.object_id,'power'));self.private_replay(s,private)

    def test_real_stridehangar_uses_generated_authority_and_anthem_without_double_replacement(self):
        s=self.session(821050);source=self.add(s.engine,NAMES[3],zone='hand');inspector=self.add(s.engine,'Thraben Inspector',zone='hand');spell=self.add(s.engine,'Raise the Alarm',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,source)['strict_capability_ready']);self.prepare(s,mana={'C':6,'W':2});self.checkpoint(s)
        self.cast(s,source,payment={'C':3});self.assertIsNone(self.resolve(s));self.cast(s,inspector,payment={'W':1});self.finish_replacements(s)
        self.assertEqual(1,len(self.tokens(s,'Clue')));thopters=self.tokens(s,'Thopter');self.assertEqual(1,len(thopters));self.assertEqual(2,s.engine._numeric_stat(thopters[0].object_id,'power'));self.assertIn('flying',s.engine._combat_keywords(thopters[0]))
        self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'));self.cast(s,spell,payment={'C':1,'W':1});self.finish_replacements(s)
        self.assertEqual(1,len(self.tokens(s,'Thopter')));self.assertEqual(2,len(self.tokens(s,'Soldier')));self.assertEqual(1,s.engine._numeric_stat(self.tokens(s,'Soldier')[0].object_id,'power'))
        before=len(s.state.players['A'].zones['hand']);self.activation(s,self.tokens(s,'Clue')[0],payment={'C':2});self.assertIsNone(self.resolve(s));self.assertEqual(before+1,len(s.state.players['A'].zones['hand']));self.private_replay(s,private)

    def test_real_control_change_uses_controller_instead_of_owner(self):
        s=self.session(821060);source=self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Chatter of the Squirrel',seat='B',zone='hand');steal=self.add(s.engine,'Control Magic',seat='B',zone='hand');own=self.add(s.engine,'Raise the Alarm',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['A'].mana_pool.update({'C':1,'W':1});self.prepare(s,actor='B',active='B',mana={'C':3,'U':2,'G':2});self.checkpoint(s)
        self.cast(s,spell,seat='B',payment={'G':1});self.finish_replacements(s);self.assertEqual(1,len(self.tokens(s,'Squirrel','B')));self.assertFalse(self.tokens(s,'Mutagen','B'))
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2});self.assertIsNone(self.resolve(s));self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.cast(s,own,payment={'C':1,'W':1});self.finish_replacements(s);self.assertEqual(2,len(self.tokens(s,'Soldier')));self.assertFalse(self.tokens(s,'Mutagen'))
        self.cast(s,spell,seat='B',payment={'C':1,'G':1});self.finish_replacements(s);self.assertEqual(2,len(self.tokens(s,'Squirrel','B')));self.assertEqual(1,len(self.tokens(s,'Mutagen','B')));self.private_replay(s,private)

    def test_added_mutagen_inherits_creation_tap_and_cannot_pay_tap_while_tapped(self):
        s=self.session(821070);source=self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Generic Tapped Creature Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s);self.cast(s,spell,payment={'G':1});self.finish_replacements(s)
        tokens=self.tokens(s,'Mutagen');self.assertEqual(1,len(tokens));self.assertTrue(tokens[0].tapped);self.assertTrue(self.tokens(s,'Soldier')[0].tapped)
        self.priority(s,'A');self.assertFalse(any(r['id'].startswith('activate:'+tokens[0].ref+':') for r in self.actions(s)));self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_runtime_or_retirement_omissions_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.semantic_runtime.token_replacements import GenericAdditionalTokenReplacementHandler
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn fixed additional token authority'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        original=GenericAdditionalTokenReplacementHandler.replacement_effect
        def wrong_controller(handler,descriptor,context):return original(handler,descriptor,replace(context,source_controller='D'))
        with patch.object(GenericAdditionalTokenReplacementHandler,'replacement_effect',wrong_controller):
            with self.assertRaises(AssertionError):type(self)('test_real_quina_adds_one_frog_per_event_and_pays_its_frog_price').test_real_quina_adds_one_frog_per_event_and_pays_its_frog_price()
        with patch('quorune.card_programs.additional_token_equivalence.shadowed_reviewed_additional_token_keys',return_value=set()):
            with self.assertRaises(AssertionError):type(self)('test_real_stridehangar_uses_generated_authority_and_anthem_without_double_replacement').test_real_stridehangar_uses_generated_authority_and_anthem_without_double_replacement()

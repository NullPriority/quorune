"""Pinned CR401/701.24: one target's current library, not the source controller."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.compiler.fixed_top_order_templates import fixed_top_order_template
from quorune.deck import DeckDefinition,DeckEntry
from quorune.library_order_model import fixed_shuffle_effect,SHUFFLE_OPERATION,SHUFFLE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.rules.library_order_shapes import fixed_library_order_capabilities,library_order_payload_dependencies
from quorune.rules.target_library_shuffle import (
 TARGET_SHUFFLE_FIELD as FIELD,TARGET_SHUFFLE_CAPABILITY as CAPABILITY,TARGET_SHUFFLE_MECHANIC as MECHANIC,
 validate_target_shuffle_effect,target_shuffle_schema,target_shuffle_runtime_dependencies,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticNodeError
from quorune.semantic_runtime.intents import ShuffleLibraryIntent
from quorune.semantic_runtime.library_shuffle import FixedLibraryShuffleHandler
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls
import test_fixed_top_order as old
import test_bound_effect_programs as compiler_controls

FIXTURE=ROOT/'tests/fixtures/target-library-shuffle-cards.json'
CANDIDATES=('Soldier of Fortune','Boggart Forager','Lantern of Insight')


class TargetShuffleModelTests(unittest.TestCase):
 def effect(self,player='$target.0'):return {'op':SHUFFLE_OPERATION,'player':player,FIELD:1}

 def test_exact_target_grammar_has_independent_scope_and_legacy_bytes(self):
  for text in ('Target player shuffles their library.','Target player shuffles.'):
   name,effects,schema,mechanics=fixed_top_order_template(text)
   self.assertEqual((self.effect(),),effects);self.assertEqual(target_shuffle_schema(),schema)
   self.assertEqual({CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution'},set(fixed_library_order_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)))
   self.assertEqual({CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution'},set(library_order_payload_dependencies(effects[0])))
  self.assertEqual({'op':SHUFFLE_OPERATION,'player':'$controller'},fixed_top_order_template('Shuffle your library.')[1][0])
  for text in ('Each player shuffles.','Target opponent shuffles.','Target player shuffles their hand into their library.','Target player shuffles their library. Draw a card.'):
   self.assertIsNone(fixed_top_order_template(text),text)

 def test_typed_target_intent_separates_actor_and_player_without_broadening_legacy(self):
  context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Target shuffle',seats=('A','B','C'),active_seats=('A','B'),apnap_order=('A','B'))
  handler=FixedLibraryShuffleHandler();plan=handler.lower(self.effect('B'),context)
  self.assertEqual((ShuffleLibraryIntent(actor='A',player='B',reason='Target shuffle'),),plan.intents)
  for effect in (fixed_shuffle_effect('B'),self.effect('C'),self.effect('foreign'),self.effect('$target.0')):
   with self.assertRaises(ValueError):handler.lower(effect,context)
  old.FixedTopOrderModelTests('test_shuffle_lowering_has_one_owner_and_rejects_other_seat').test_shuffle_lowering_has_one_owner_and_rejects_other_seat()

 def test_marker_target_schema_and_stripped_coverage_cannot_borrow_controller_scope(self):
  effect=self.effect()
  for change in ({FIELD:True},{FIELD:2},{FIELD:None},{'player':'$controller'},{'player':True},{'extra':1}):
   with self.assertRaises(ValueError):validate_target_shuffle_effect({**effect,**change})
  for schema in (None,{**target_shuffle_schema(),'count':True},{**target_shuffle_schema(),'count':2},{**target_shuffle_schema(),'zones':['battlefield']}):
   self.assertFalse(fixed_library_order_capabilities(effects=(effect,),target_schema=schema,mechanic_ids=(MECHANIC,'cr-115-targets')))
   with self.assertRaises(ValueError):target_shuffle_runtime_dependencies(SimpleNamespace(effects=(effect,),target_schema=schema))
  self.assertIn(CAPABILITY,target_shuffle_runtime_dependencies(SimpleNamespace(effects=(effect,),target_schema=target_shuffle_schema())))


class TargetShuffleCompilerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.registry=load_default_capability_registry()

 def test_full_originals_bind_and_existing_recycling_controls_are_not_new_candidates(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'shuffle.sqlite3';build_fixture_database([FIXTURE],path)
   with CardDatabase(path) as db:
    for name in (*CANDIDATES,'Thran Foundry','Clear the Mind','Cranial Archive','Elixir of Immortality'):
     card=db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
     register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
     program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
     bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
     self.assertTrue(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed',(name,bound['blockers']))

 def test_fixed_target_shuffle_requires_both_shuffle_authorities_and_target_revalidation(self):
  card=compiler_controls.record('Target player shuffles their library.','Sorcery')
  ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status)
  node,=ir.faces[0].nodes;self.assertEqual(card.oracle_text,card.oracle_text[node.span.start:node.span.end])
  for missing in (CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution'):
   raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
   next(r for r in raw['capabilities'] if r['id']==missing).update(status='blocked',blockers=['Target shuffle authority withdrawn'])
   registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
   self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review').status)


class TargetShuffleRuntimeTests(unittest.TestCase):
 register_fixture_cards_on_add=True
 session=controls.PublicConditionSelfReplacementTests.session
 start=controls.PublicConditionSelfReplacementTests.start
 add=controls.PublicConditionSelfReplacementTests.add
 prepare=controls.PublicConditionSelfReplacementTests.prepare
 ready=controls.PublicConditionSelfReplacementTests.ready
 checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
 resolve=controls.PublicConditionSelfReplacementTests.resolve
 replay=controls.PublicConditionSelfReplacementTests.replay
 binding=controls.PublicConditionSelfReplacementTests.binding
 priority=controls.PublicConditionSelfReplacementTests.priority
 private_replay=controls.PublicConditionSelfReplacementTests.private_replay
 private=controls.PublicConditionSelfReplacementTests.private
 cast=controls.PublicConditionSelfReplacementTests.cast
 reject=controls.PublicConditionSelfReplacementTests.reject

 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'target-shuffle.sqlite3'
  build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
  cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
  cls.deck=DeckDefinition('Target shuffle',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
 @classmethod
 def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

 def world(self,seed,name):
  s=self.start(seed);source=self.add(s.engine,name,zone='hand');self.add(s.engine,'Fervor')
  self.assertTrue(self.binding(s,source)['strict_capability_ready'])
  private=self.private(s)
  return s,source,private

 def action(self,s,source):
  return next((a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':')),None)

 def activate(self,s,source,target='B'):
  self.priority(s,'A');row=self.action(s,source);self.assertIsNotNone(row)
  self.assertIn(target,row['target_schema']['legal_refs'])
  command={'action_id':row['id'],'targets':[target],'pay':'auto'}
  self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

 def shuffle_count(self,s,seat):return s.state.players[seat].stats.get('shuffle_count',0)

 def test_each_original_casts_and_only_the_announced_players_library_shuffles(self):
  for i,name in enumerate(CANDIDATES):
   s,source,private=self.world(788300+i,name)
   self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
   before={p:list(s.state.players[p].zones['library']) for p in 'ABCD'}
   counts={p:self.shuffle_count(s,p) for p in 'ABCD'}
   self.activate(s,source)
   self.assertEqual('battlefield' if name=='Soldier of Fortune' else 'graveyard',s.state.cards[source.object_id].zone)
   s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
   self.assertEqual(counts['B']+1,self.shuffle_count(s,'B'))
   self.assertCountEqual(before['B'],s.state.players['B'].zones['library'])
   for p in 'ACD':self.assertEqual(before[p],s.state.players[p].zones['library']);self.assertEqual(counts[p],self.shuffle_count(s,p))
   self.private_replay(s,private)

 def test_empty_and_singleton_library_are_legal_and_still_record_shuffle(self):
  for amount in (0,1):
   s,source,private=self.world(788310+amount,'Boggart Forager')
   player=s.state.players['B'];moved=player.zones['library'][:-1] if amount else list(player.zones['library'])
   for oid in moved:player.zones['library'].remove(oid);player.zones['graveyard'].append(oid);s.state.cards[oid].zone='graveyard'
   self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s));before=self.shuffle_count(s,'B')
   self.activate(s,source);self.assertIsNone(self.resolve(s))
   self.assertEqual(amount,len(s.state.players['B'].zones['library']));self.assertEqual(before+1,self.shuffle_count(s,'B'))
   self.private_replay(s,private)

 def test_actual_self_target_retains_same_new_scope_and_foreign_libraries(self):
  s,source,private=self.world(788320,'Soldier of Fortune')
  self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
  before={p:list(s.state.players[p].zones['library']) for p in 'BCD'};count=self.shuffle_count(s,'A')
  self.activate(s,source,'A');self.assertIsNone(self.resolve(s));self.assertEqual(count+1,self.shuffle_count(s,'A'))
  for p in 'BCD':self.assertEqual(before[p],s.state.players[p].zones['library'])
  self.private_replay(s,private)

 def test_player_shroud_hides_unpayable_offer_and_rechecks_accepted_target(self):
  s,source,private=self.world(788330,'Soldier of Fortune')
  protection={p:self.add(s.engine,'True Believer',seat=p,ref='shuffle-shroud-'+p) for p in 'ABCD'}
  bounce=self.add(s.engine,'Unsummon',zone='hand')
  self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
  self.assertIsNone(self.action(s,source))
  self.reject(s,{'action_id':'activate:'+source.ref+':ab1','targets':['B'],'pay':'auto'},'A')
  self.assertFalse(s.state.cards[source.object_id].tapped)
  self.cast(s,bounce,[protection['B'].ref]);self.assertIsNone(self.resolve(s))
  row=self.action(s,source);self.assertIsNotNone(row);self.assertEqual({'B'},set(row['target_schema']['legal_refs']))
  self.activate(s,source);self.assertIsNone(self.resolve(s));self.private_replay(s,private)

 def test_lantern_reveal_sibling_and_sacrificed_source_do_not_expose_shuffled_library(self):
  s,source,private=self.world(788340,'Lantern of Insight')
  self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
  tops={p:s.state.cards[s.state.players[p].zones['library'][-1]].ref for p in 'ABCD'}
  for viewer in 'ABCD':
   projected=str(s.packet('pilot:'+viewer,full=True))
   for ref in tops.values():self.assertIn(ref,projected)
  self.activate(s,source);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
  self.assertIsNone(self.resolve(s))
  for viewer in 'ABCD':self.assertNotIn(tops['B'],str(s.packet('pilot:'+viewer,full=True)))
  self.private_replay(s,private)

 def test_queued_target_survives_actual_source_bounce_or_control_change(self):
  for control in (False,True):
   s,source,private=self.world(788350+control,'Soldier of Fortune')
   response=self.add(s.engine,'Blind with Anger' if control else 'Unsummon',seat='B',zone='hand')
   s.state.players['B'].mana_pool.update({'C':3,'R':1} if control else {'U':1})
   self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s));count=self.shuffle_count(s,'B')
   self.activate(s,source);self.cast(s,response,[source.ref],seat='B')
   s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
   self.assertEqual(count+1,self.shuffle_count(s,'B'))
   self.assertEqual('battlefield' if control else 'hand',s.state.cards[source.object_id].zone)
   if control:self.assertEqual('B',s.state.cards[source.object_id].controller)
   self.private_replay(s,private)

 def test_shuffle_fault_restores_order_knowledge_and_shuffle_counter_before_retry(self):
  from quorune.zone_transitions import ZoneTransitionOwner
  from quorune.errors import GameRuleError
  s,source,private=self.world(788360,'Boggart Forager')
  self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s));self.activate(s,source)
  count=self.shuffle_count(s,'B');original=ZoneTransitionOwner.shuffle_library;partial=[];failed=None
  def fault(owner,*args,**kwargs):
   original(owner,*args,**kwargs);partial.append(True)
   raise GameRuleError('Injected targeted shuffle fault')
  with patch.object(ZoneTransitionOwner,'shuffle_library',fault):
   for _ in range(8):
    principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
    result=s.act(principal,{'action_id':'pass'})
    if not result.ok:
     self.assertIn('Injected targeted shuffle fault',result.summary)
     self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
  self.assertIsNotNone(failed);self.assertEqual([True],partial);self.assertEqual(count,self.shuffle_count(s,'B'))
  self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
  result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
  self.assertIsNone(self.resolve(s));self.assertEqual(count+1,self.shuffle_count(s,'B'));self.private_replay(s,private)

 def test_executable_target_scope_minimum_survives_stripped_coverage_and_schema_forgery(self):
  s,source,_=self.world(788370,'Soldier of Fortune')
  program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
  deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
  changed=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
  bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
  self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
  for schema in (None,{**target_shuffle_schema(),'count':2}):
   changed=replace(program,coverage=(),target_schema=schema)
   self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])

 def test_controller_rebinding_omission_is_killed_by_fresh_actual_originals(self):
  from quorune.rules import target_library_shuffle as scope
  original=scope.validate_target_shuffle_effect
  def rebind(effect,*,resolved=False):
   value=original(effect,resolved=resolved)
   return 'A' if resolved else value
  with patch.object(scope,'validate_target_shuffle_effect',side_effect=rebind):
   with self.assertRaises(AssertionError):self.test_each_original_casts_and_only_the_announced_players_library_shuffles()

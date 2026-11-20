"""CR110.4/205.3m: subtype permanents include kindred noncreatures, not instants."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.compiler.hand_entry_templates import fixed_private_hand_entry_effect_template
from quorune.hand_entry_queries import decode_hand_entry_queries,hand_entry_matches,hand_entry_query_capabilities
from quorune.rules.permanent_subtype_hand_queries import (
 PERMANENT_SUBTYPE_HAND_KIND as KIND,PERMANENT_SUBTYPE_HAND_CAPABILITY as CAPABILITY,
 permanent_subtype_hand_descriptor,decode_permanent_subtype_hand_query,permanent_subtype_hand_runtime_dependencies,
)
from quorune.object_query import ObjectQueryResult
from quorune.rules.public_zone_move_capability_shapes import fixed_private_hand_entry_node_capabilities
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls
import test_hand_entry_expansion as legacy

FIXTURE=ROOT/'tests/fixtures/permanent-subtype-hand-entry-cards.json'

class PermanentSubtypeHandModelTests(unittest.TestCase):
 def descriptor(self):return permanent_subtype_hand_descriptor('Goblin permanent')
 def test_one_canonical_subtype_permanent_has_explicit_query_and_independent_capability(self):
  template=fixed_private_hand_entry_effect_template('You may put a Goblin permanent card from your hand onto the battlefield.')
  self.assertEqual(self.descriptor(),dict(template.query))
  self.assertIn(CAPABILITY,fixed_private_hand_entry_node_capabilities(effects=template.effects,target_schema=None,mechanic_ids=template.mechanics))
  self.assertEqual((CAPABILITY,),hand_entry_query_capabilities(self.descriptor()))
  self.assertIn(CAPABILITY,permanent_subtype_hand_runtime_dependencies(SimpleNamespace(effects=template.effects)))
  for subject in ('Wizard permanent','Minotaur permanent'):self.assertIsNotNone(permanent_subtype_hand_descriptor(subject))
  for subject in ('unknown-kind permanent','Goblin or Zombie permanent','Goblin creature'):self.assertIsNone(permanent_subtype_hand_descriptor(subject))

 def test_permanent_type_and_subtype_are_independent_requirements(self):
  queries=decode_hand_entry_queries(self.descriptor())
  for kind in ('artifact','battle','creature','enchantment','land','planeswalker'):
   row=ObjectQueryResult('id','ref','Generic','A','A','hand',types=(kind,'kindred'),subtypes=('goblin',))
   self.assertTrue(hand_entry_matches(row,queries),kind)
  for kind in ('instant','sorcery'):
   row=ObjectQueryResult('id','ref','Generic','A','A','hand',types=(kind,'kindred'),subtypes=('goblin',))
   self.assertFalse(hand_entry_matches(row,queries),kind)
  row=ObjectQueryResult('id','ref','Generic','A','A','hand',types=('creature',),subtypes=('minotaur',))
  self.assertFalse(hand_entry_matches(row,queries))

 def test_legacy_minotaur_and_creature_encodings_and_rejection_boundary_are_unchanged(self):
  old=fixed_private_hand_entry_effect_template('You may put a Minotaur permanent card from your hand onto the battlefield.')
  self.assertNotIn('kind',old.effects[0]['query'])
  query,=decode_hand_entry_queries(old.effects[0]['query']);self.assertEqual(('minotaur',),query.subtypes_all)
  raw=replace(query,subtypes_all=('goblin',)).canonical_dict()
  with self.assertRaises(ValueError):decode_hand_entry_queries(raw)
  old=fixed_private_hand_entry_effect_template('You may put a Goblin creature card from your hand onto the battlefield.')
  self.assertNotIn('kind',old.effects[0]['query']);self.assertFalse(hand_entry_query_capabilities(old.effects[0]['query']))
  self.assertEqual(('creature',),decode_hand_entry_queries(old.effects[0]['query'])[0].types_all)
  for change in ({'schema_version':True},{'schema_version':2},{'subtype':'Goblin'},{'subtype':'unknown-kind'},{'query':{}},{'extra':0}):
   with self.assertRaises(ValueError):decode_hand_entry_queries({**self.descriptor(),**change})
  for text in ('You may put a Goblin permanent card from your hand onto the battlefield. Draw a card.',
    'You may put a Goblin permanent card from your graveyard onto the battlefield.'):
   self.assertIsNone(fixed_private_hand_entry_effect_template(text))

 def test_new_query_requires_sealed_current_hand_incarnation_but_legacy_payload_remains_compatible(self):
  from quorune.semantic_choices.object_selection import PutTypedCardFromHandHandler
  from quorune.semantic_choices.model import SemanticChoiceError
  from quorune.replacement.immutable import FrozenMap
  handler=PutTypedCardFromHandHandler()
  row=ObjectQueryResult('id','card','Generic','A','A','hand',types=('enchantment','kindred'),subtypes=('goblin',),logical_object_id='card@1')
  rows={'card':row};query=SimpleNamespace(objects=lambda **kw:tuple(rows.values()),object=lambda ref,**kw:rows.get(ref))
  effect={'op':'put_card_from_hand','player':'A','query':self.descriptor(),'tapped':False}
  context=SimpleNamespace(actor='A',query=query,stack_ref='S1',stack_label='Hand entry')
  prepared=handler.prepare(effect,context);continuation=SimpleNamespace(effect=prepared.continuation_effect)
  self.assertEqual(1,len(handler.complete(continuation,{'card':'card'},query).prepend_effects))
  without=dict(prepared.continuation_effect);without.pop('_legal_logical_ids')
  with self.assertRaises(SemanticChoiceError):handler.complete(SimpleNamespace(effect=FrozenMap(without)),{'card':'card'},query)
  rows['card']=replace(row,logical_object_id='card@2')
  with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'card':'card'},query)

class PermanentSubtypeHandCompilerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.registry=load_default_capability_registry()
 def test_complete_lackey_and_legacy_controls_admit_without_dynamic_query_sibling_loss(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'permanent-hand.sqlite3';build_fixture_database([FIXTURE],path)
   with CardDatabase(path) as db:
    for name in ('Goblin Lackey','Didgeridoo','Warren Instigator',"Belbe's Portal",'Aether Vial'):
     card=db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
     register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
     program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
     bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
     self.assertEqual(name not in ("Belbe's Portal",'Aether Vial'),bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),(name,bound['blockers']))

 def test_query_capability_withdrawal_leaves_legacy_creature_entry_unchanged(self):
  from test_qualified_zone_event_queries import query_record
  raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
  next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['Permanent subtype query withdrawn'])
  registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
  for subject,exact in (('Goblin permanent',False),('Goblin creature',True),('Minotaur permanent',True)):
   card=replace(query_record('You may put a '+subject+' card from your hand onto the battlefield.'),type_line='Sorcery',power=None,toughness=None)
   self.assertEqual(exact,compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review').status=='exact')

class PermanentSubtypeHandRuntimeTests(unittest.TestCase):
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
  cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'permanent-hand.sqlite3'
  build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
  cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
  cls.deck=DeckDefinition('Permanent subtype hand entry',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
 @classmethod
 def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
 def actions(self,s):return s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
 def activate(self,s,source,targets):
  self.priority(s,'A');row=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
  command={'action_id':row['id'],'targets':targets,'pay':'auto'};self.reject(s,command,'D')
  result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
 def world(self,seed,*,eligible=True,response=False):
  s=self.start(seed);source=self.add(s.engine,'Goblin Lackey',zone='hand')
  self.assertTrue(self.binding(s,source)['strict_capability_ready'])
  self.add(s.engine,'Fervor');bow=self.add(s.engine,'Viridian Longbow')
  cards={name:self.add(s.engine,name,zone='hand',ref='permanent-hand-'+str(i)) for i,name in enumerate(('Goblin Piker','Boggart Shenanigans','Changeling Outcast') if eligible else ())}
  bad=self.add(s.engine,'Tarfire',zone='hand');foreign=self.add(s.engine,'Goblin Piker',seat='B',zone='hand',ref='permanent-hand-foreign')
  private=self.private(s)
  if response:
   self.response=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=1
  self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
  self.activate(s,bow,[source.ref]);self.assertIsNone(self.resolve(s))
  return s,source,bow,cards,bad,foreign,private
 def ping(self,s,source,target='B'):
  self.activate(s,source,[target])
  return self.resolve(s)
 def offered(self,s):return {r['id'] for r in s.packet('pilot:A',full=True)['decision']['ctx']['objects']}
 def choose(self,s,card=None):
  command={'action_id':'choose',**({'card':card.ref} if card is not None else {})}
  self.reject(s,command,'B');s=self.replay(s,load=True)
  result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s));return s
 def test_actual_noncombat_lackey_puts_creature_kindred_or_changeling_permanent_from_private_hand(self):
  for i,name in enumerate(('Goblin Piker','Boggart Shenanigans','Changeling Outcast')):
   s,source,bow,cards,bad,foreign,private=self.world(788400+i)
   self.assertEqual('semantic.choice',self.ping(s,source).kind)
   self.assertEqual(['pilot:A'],list(s.pending_principals()))
   self.assertEqual({c.ref for c in cards.values()},self.offered(s))
   for card in (bad,foreign):self.reject(s,{'action_id':'choose','card':card.ref},'A')
   for seat in 'BCD':
    self.assertIsNone(s.packet('pilot:'+seat,full=True)['decision'])
    for card in cards.values():self.assertNotIn(card.ref,str(s.packet('pilot:'+seat,full=True)))
   s=self.choose(s,cards[name]);self.assertEqual('battlefield',s.state.cards[cards[name].object_id].zone)
   self.assertEqual('hand',s.state.cards[bad.object_id].zone)
   self.assertEqual(39,s.state.players['B'].life);self.private_replay(s,private)
 def test_any_player_damage_includes_self_and_decline_or_empty_eligible_hand_completes(self):
  for i,eligible in enumerate((False,True)):
   s,source,bow,cards,bad,foreign,private=self.world(788410+i,eligible=eligible)
   decision=self.ping(s,source,'A');self.assertEqual(39,s.state.players['A'].life)
   if eligible:
    self.assertEqual('semantic.choice',decision.kind);s=self.choose(s)
    self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for c in cards.values()))
   else:self.assertIsNone(decision)
   self.assertEqual('hand',s.state.cards[bad.object_id].zone);self.private_replay(s,private)
 def test_executable_query_minimum_is_required_without_coverage(self):
  s,source,*_=self.world(788420)
  program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
  deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
  changed=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
  bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
  self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

 def test_queued_trigger_can_put_returned_source_as_a_new_hand_incarnation(self):
  s,source,bow,cards,bad,foreign,private=self.world(788430,response=True)
  old_identity=s.state.cards[source.object_id].logical_object_id
  self.activate(s,source,['B'])
  for _ in range(8):
   if any(item.kind=='triggered_ability' and item.source_object_id==source.object_id for item in s.state.stack):break
   result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
  self.assertTrue(any(item.kind=='triggered_ability' and item.source_object_id==source.object_id for item in s.state.stack))
  self.cast(s,self.response,[source.ref],seat='B')
  self.assertEqual('semantic.choice',self.resolve(s).kind)
  self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertIn(source.ref,self.offered(s))
  self.assertNotEqual(old_identity,s.state.cards[source.object_id].logical_object_id)
  s=self.choose(s,s.state.cards[source.object_id]);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
  self.assertNotEqual(old_identity,s.state.cards[source.object_id].logical_object_id)
  self.assertIsNone(s.state.cards[bow.object_id].attached_to);self.private_replay(s,private)

 def test_permanent_type_filter_omission_is_killed_by_fresh_actual_original(self):
  from quorune.rules import permanent_subtype_hand_queries as scope
  original=scope.decode_permanent_subtype_hand_query
  def omit(value):return replace(original(value),types_any=())
  with patch.object(scope,'decode_permanent_subtype_hand_query',side_effect=omit):
   with self.assertRaises(AssertionError):self.test_actual_noncombat_lackey_puts_creature_kindred_or_changeling_permanent_from_private_hand()

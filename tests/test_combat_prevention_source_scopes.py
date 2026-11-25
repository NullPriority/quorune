"""CR615.1/113.7: live attacking sources and original self block shields."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.compiler.combat_prevention_source_results import bound_source_combat_prevention
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.compiler.prevention_templates import fixed_prevention_effect_template
from quorune.source_prevention import (
 SOURCE_SUBJECT_VERSION as VERSION,SOURCE_COMBAT_DIRECTION as DIRECTION,
 SOURCE_COMBAT_PREVENTION_CAPABILITY as CAPABILITY,source_prevention_dependencies,
 source_prevention_subject_unavailable,source_block_combat_runtime_dependencies,materialize_source_combat_prevention,
)
from quorune.rules.prevention_component_shapes import fixed_prevention_component_capabilities
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
import test_scoped_damage_prevention as old
import test_bound_effect_programs as compiler_controls

FIXTURE=ROOT/'tests/fixtures/combat-prevention-source-scopes-cards.json'
CANDIDATES=('Harmless Assault','Goblin Snowman','Ignoble Soldier')
TO_FROM='Prevent all combat damage that would be dealt to and dealt by it this turn.'
BY='Prevent all combat damage that would be dealt by it this turn.'
def bound(body=TO_FROM,event='blocks'):
 b=fixed_counter_trigger_binding('Whenever this creature '+event+', '+body,card_name='Anonymous Source')
 return b,bound_source_combat_prevention(b,body)

class CombatPreventionSourceModelTests(unittest.TestCase):
 def test_only_proved_source_block_occurrences_bind_pronoun_and_two_directions(self):
  for event,body,count in (('blocks',TO_FROM,2),('becomes blocked',BY,1)):
   binding,result=bound(body,event);self.assertEqual(count,len(result[1]));self.assertIsNone(result[2])
   self.assertIn(CAPABILITY,fixed_prevention_component_capabilities(effects=result[1],target_schema=None,mechanic_ids=result[3]))
   self.assertIsNone(fixed_prevention_effect_template(body,card_name='Anonymous Source'))
   for bad in (replace(binding,public_condition={'field':'controller','op':'eq','value':'$source.controller'}),replace(binding,public_active_zone='graveyard')):self.assertIsNone(bound_source_combat_prevention(bad,body))
  for body in (BY+' Draw a card.',BY.replace('it','that creature'),BY.replace('combat ',''),TO_FROM.replace('all','the next 1')):self.assertIsNone(bound(body)[1])
 def test_new_direction_marker_is_closed_and_legacy_subject_versions_keep_bytes(self):
  _,result=bound();effect=result[1][0]
  for change in ({VERSION:True},{VERSION:2},{DIRECTION:'both'},{'damage_kind':'any'},{'subject':'$source'},{'application_group_id':'foreign'},{'extra':1}):
   with self.assertRaises(ValueError):source_prevention_dependencies({**effect,**change})
  for version in (1,2):
   old={'op':'create_damage_prevention_shield','source':'$source','subject':'$source.zone_object','mode':'all','duration':'until_end_of_turn',VERSION:version,**({'damage_kind':'combat'} if version==2 else {})}
   self.assertEqual(('damage.prevention.persistent_amount',),source_prevention_dependencies(old));self.assertNotIn(DIRECTION,old)
  resolved={**effect,'source':'source','subject':None};self.assertTrue(source_prevention_subject_unavailable(None,resolved,actor='A'))
  with self.assertRaises(Exception):source_prevention_subject_unavailable(None,{**resolved,DIRECTION:True},actor='A')
  outgoing={**effect,'source':'creator','subject':'original',DIRECTION:'from'}
  material=materialize_source_combat_prevention(outgoing);self.assertEqual('*',material['subject']);self.assertEqual('original',material['chosen_source']);self.assertNotIn('chosen_source',outgoing)
  from quorune.object_query import ObjectQuerySpec,validate_chosen_damage_source_predicate
  validate_chosen_damage_source_predicate(ObjectQuerySpec.from_dict(material['source_predicate']))
 def test_payload_minimum_requires_original_event_even_without_coverage(self):
  binding,result=bound();program=SimpleNamespace(effects=result[1],event='creature.blocks',active_zone='battlefield',target_schema=None,event_condition=binding.event_condition)
  self.assertIn(CAPABILITY,source_block_combat_runtime_dependencies(program))
  for change in ({'event':'creature.attacks'},{'active_zone':'graveyard'},{'target_schema':{'count':1}},{'event_condition':{'field':'controller','op':'eq','value':'$source.controller'}}):
   with self.assertRaises(ValueError):source_block_combat_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))
 def test_attacking_source_prevention_is_a_live_predicate_not_a_frozen_object_set(self):
  result=fixed_prevention_effect_template('Prevent all combat damage that would be dealt this turn by attacking creatures.',card_name='Generic')
  self.assertEqual(('attacking','creature'),tuple(result[1][0]['scope']['source_characteristics_all']));self.assertEqual('combat',result[1][0]['damage_kind']);self.assertIsNone(result[2]);self.assertIsNone(result[1][0]['scope']['source_ref'])
  for body in ('Prevent all combat damage that would be dealt this turn by creatures other than target creature.',BY):self.assertIsNone(fixed_prevention_effect_template(body,card_name='Generic'))

class CombatPreventionSourceCompilerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.registry=load_default_capability_registry()
 def test_all_originals_bind_normally_and_material_neighbors_keep_complete_siblings(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'combat-prevention.sqlite3';build_fixture_database([FIXTURE],path)
   with CardDatabase(path) as db:
    for name in (*CANDIDATES,'Moonlight Geist','Urborg Phantom','Deftblade Elite','Terrifying Presence','Chant of Vitu-Ghazi'):
     card=db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
     register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
     program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
     self.assertEqual(name != 'Chant of Vitu-Ghazi',bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),(name,bound['blockers']))
 def test_source_owner_withdrawal_preserves_plain_prevention_and_attacking_scope(self):
  raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Source combat shield owner withdrawn'])
  registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
  for text,kind,exact in (('Whenever this creature blocks, '+TO_FROM,'Creature — Goblin',False),('Prevent all combat damage that would be dealt this turn by attacking creatures.','Instant',True),('Prevent all combat damage that would be dealt this turn.','Instant',True)):
   card=compiler_controls.record(text,kind);self.assertEqual(exact,compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review').status=='exact')

class CombatPreventionSourceRuntimeTests(unittest.TestCase):
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
 advance=controls.PublicConditionSelfReplacementTests.advance
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'combat-prevention.sqlite3'
  build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
  cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
  cls.deck=DeckDefinition('Combat prevention source scopes',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
 @classmethod
 def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
 def reach(self,s,kind,seat=None):
  self.advance(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind==kind and (seat is None or game.state.pending_decision.actors==[seat]))
 def attack(self,s,seat,assignments):
  self.reach(s,'combat.attackers',seat)
  command={'action_id':'attack','attackers':{c.ref:p for c,p in assignments}}
  self.reject(s,command,'D' if seat!='D' else 'C');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
 def block(self,s,seat,pairs):
  self.reach(s,'combat.blockers',seat)
  command={'action_id':'block','blocks':{b.ref:a.ref for b,a in pairs}}
  self.reject(s,command,'D' if seat!='D' else 'C');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
 def activate(self,s,source,targets=()):
  self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
  row=next(a for a in actions if a['id'].startswith('activate:'+source.ref+':'))
  command={'action_id':row['id'],'targets':list(targets),'pay':'auto'};self.reject(s,command,'D')
  result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
 def cast_original(self,s,source):
  self.assertTrue(self.binding(s,source)['strict_capability_ready']);self.cast(s,source);self.assertIsNone(self.resolve(s))
 def harmless(self,seed,*,unpreventable=False):
  s=self.start(seed);spell=self.add(s.engine,'Harmless Assault',zone='hand');self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
  attacker=self.add(s.engine,'Grizzly Bears');unblocked=self.add(s.engine,'Grizzly Bears',ref='prevention-unblocked')
  pinger=self.add(s.engine,'Prodigal Pyromancer');self.add(s.engine,"Serra's Blessing")
  blocker=self.add(s.engine,'Hill Giant',seat='B');self.add(s.engine,'Fervor')
  permission=self.add(s.engine,'Questing Beast',zone='hand') if unpreventable else None
  private=self.private(s);self.ready(s);self.cast(s,spell);self.assertIsNone(self.resolve(s))
  if permission:self.cast_original(s,permission)
  self.attack(s,'A',[(attacker,'B'),(unblocked,'B'),(pinger,'B')]);self.block(s,'B',[(blocker,attacker)])
  self.assertFalse(s.state.cards[pinger.object_id].tapped)
  self.activate(s,pinger,['B']);self.assertIsNone(self.resolve(s));self.assertEqual(39,s.state.players['B'].life)
  s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
  self.assertEqual('graveyard',s.state.cards[attacker.object_id].zone)
  self.assertEqual('battlefield',s.state.cards[blocker.object_id].zone)
  self.assertEqual(2 if unpreventable else 0,s.state.cards[blocker.object_id].marked_damage)
  self.assertEqual(36 if unpreventable else 39,s.state.players['B'].life);self.private_replay(s,private)
 def test_actual_harmless_assault_prevents_attacking_not_blocking_or_noncombat_damage(self):self.harmless(788500)
 def test_actual_cannot_prevent_damage_bypasses_attacking_source_shield(self):self.harmless(788501,unpreventable=True)
 def test_actual_ignoble_source_prevents_outgoing_but_still_takes_blocker_damage(self):
  s=self.start(788510);source=self.add(s.engine,'Ignoble Soldier',zone='hand');blocker=self.add(s.engine,'Grizzly Bears',seat='B');self.add(s.engine,'Fervor');equipment=self.add(s.engine,'Fireshrieker');private=self.private(s)
  self.ready(s);self.cast_original(s,source);self.activate(s,equipment,[source.ref]);self.assertIsNone(self.resolve(s));self.attack(s,'A',[(source,'B')]);self.block(s,'B',[(blocker,source)])
  self.assertIsNone(self.resolve(s));self.assertEqual(1,len(s.state.damage_prevention_shields))
  s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
  self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('battlefield',s.state.cards[blocker.object_id].zone)
  self.assertEqual(0,s.state.cards[blocker.object_id].marked_damage);self.private_replay(s,private)
 def test_actual_snowman_both_directions_keep_its_noncombat_blocking_activation(self):
  s=self.start(788520);source=self.add(s.engine,'Goblin Snowman',zone='hand');attacker=self.add(s.engine,'Hill Giant',seat='B');self.add(s.engine,'Fervor');private=self.private(s)
  self.ready(s);self.cast_original(s,source);self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
  self.attack(s,'B',[(attacker,'A')]);self.block(s,'A',[(source,attacker)])
  self.assertIsNone(self.resolve(s));self.assertEqual(2,len(s.state.damage_prevention_shields))
  self.assertEqual(1,len({shield.application_group_id for shield in s.state.damage_prevention_shields}))
  self.activate(s,source,[attacker.ref]);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[attacker.object_id].marked_damage)
  s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
  self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(0,s.state.cards[source.object_id].marked_damage)
  self.assertEqual(1,s.state.cards[attacker.object_id].marked_damage);self.private_replay(s,private)
 def test_source_shield_payload_cannot_borrow_other_event_or_missing_capability(self):
  s=self.start(788530);source=self.add(s.engine,'Ignoble Soldier',zone='hand');self.assertTrue(self.binding(s,source)['strict_capability_ready'])
  program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
  deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
  changed=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
  bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
  self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
  changed=replace(program,coverage=(),event='creature.attacks')
  self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])

 def test_actual_blink_does_not_rebind_queued_or_created_source_shields(self):
  for after in (False,True):
   s=self.start(788540+after);source=self.add(s.engine,'Goblin Snowman',zone='hand');attacker=self.add(s.engine,'Hill Giant',seat='B')
   blink=self.add(s.engine,'Momentary Blink',zone='hand');self.add(s.engine,'Plains',ref='source-blink-white');self.add(s.engine,'Island',ref='source-blink-generic')
   private=self.private(s);self.ready(s);self.cast_original(s,source)
   self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
   self.attack(s,'B',[(attacker,'A')]);self.block(s,'A',[(source,attacker)])
   old_id=s.state.cards[source.object_id].logical_object_id
   if after:self.assertIsNone(self.resolve(s));self.assertEqual(2,len(s.state.damage_prevention_shields))
   self.cast(s,blink,[source.ref]);self.assertIsNone(self.resolve(s))
   new_id=s.state.cards[source.object_id].logical_object_id;self.assertNotEqual(old_id,new_id)
   if after:
    self.assertEqual(2,len(s.state.damage_prevention_shields))
    for shield in s.state.damage_prevention_shields:
     if shield.subject.kind=='permanent':self.assertEqual(old_id,shield.subject.logical_object_id)
     if shield.chosen_source is not None:self.assertEqual(old_id,shield.chosen_source.logical_object_id)
   else:self.assertFalse(s.state.damage_prevention_shields)
   self.advance(s,lambda game:game.state.phase=='postcombat_main');self.assertEqual(40,s.state.players['A'].life)
   self.private_replay(s,private)
 def test_actual_cleanup_expires_attacking_source_prevention_before_next_combat(self):
  s=self.start(788550);spell=self.add(s.engine,'Harmless Assault',zone='hand');attacker=self.add(s.engine,'Grizzly Bears',seat='B');private=self.private(s)
  self.ready(s);self.cast(s,spell);self.assertIsNone(self.resolve(s));self.assertTrue(s.state.damage_prevention_shields)
  self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main');self.assertFalse(s.state.damage_prevention_shields)
  self.attack(s,'B',[(attacker,'A')])
  self.advance(s,lambda game:game.state.phase=='postcombat_main');self.assertEqual(38,s.state.players['A'].life);self.private_replay(s,private)
 def test_live_attacking_filter_omission_is_killed_by_fresh_original_combat(self):
  from quorune.effect_runtime import damage_modifiers
  original=damage_modifiers._prevention_scope
  def omit(effect):
   scope=original(effect)
   return replace(scope,source_characteristics_all=('creature',)) if scope.source_characteristics_all==('attacking','creature') else scope
  with patch.object(damage_modifiers,'_prevention_scope',side_effect=omit):
   with self.assertRaises(AssertionError):self.test_actual_harmless_assault_prevents_attacking_not_blocking_or_noncombat_damage()

 def test_source_pair_creation_fault_rolls_back_both_shields_before_retry(self):
  from quorune.effect_runtime import damage_modifiers
  from quorune.errors import GameRuleError
  s=self.start(788560);source=self.add(s.engine,'Goblin Snowman',zone='hand');attacker=self.add(s.engine,'Hill Giant',seat='B');private=self.private(s)
  self.ready(s);self.cast_original(s,source);self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
  self.attack(s,'B',[(attacker,'A')]);self.block(s,'A',[(source,attacker)])
  original=damage_modifiers.commit_prevention_shield_creation;partial=[];failed=None
  def fault(host,plan):
   result=original(host,plan);partial.append(len(host.state.damage_prevention_shields))
   if len(partial)==2:raise GameRuleError('Injected source-pair shield creation fault')
   return result
  with patch.object(damage_modifiers,'commit_prevention_shield_creation',side_effect=fault):
   for _ in range(8):
    principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
    if not result.ok:
     self.assertIn('Injected source-pair shield creation fault',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
  self.assertIsNotNone(failed);self.assertEqual([1,2],partial);self.assertFalse(s.state.damage_prevention_shields)
  result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
  self.assertEqual(2,len(s.state.damage_prevention_shields));self.private_replay(s,private)

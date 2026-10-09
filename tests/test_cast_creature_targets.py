from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry
from quorune.rules.spell_cast_events import SpellCastEvent, SpellCastEventError
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class CastCreatureTargetCompilerTests(unittest.TestCase):
    def test_version_six_cast_target_facts_are_closed_and_legacy_payloads_stay_readable(self):
        base=dict(card_ref='CARD',object_id='OBJECT',logical_object_id='LOGICAL',controller='A',origin='hand',stack_ref='STACK',
            types=('instant',),mana_value=1,owner='A',active_player='A',caster_spell_number=1,kicked=False,has_x_cost=False,
            has_adventure=False,keywords=(),phase='precombat_main',targets=('TARGET',))
        old=SpellCastEvent(schema_version=5,**base)
        self.assertEqual(old,SpellCastEvent.from_context(old.to_context()))
        event=SpellCastEvent(schema_version=6,creature_target_controllers=('B','A','B'),**base)
        self.assertEqual(('A','B'),event.creature_target_controllers)
        self.assertEqual(event,SpellCastEvent.from_context(event.to_context()))
        for value in (None,'A',{'A':1},False,(None,),('',)):
            with self.subTest(value=value),self.assertRaises(SpellCastEventError):
                SpellCastEvent(schema_version=6,creature_target_controllers=value,**base)
        with self.assertRaises(SpellCastEventError):
            SpellCastEvent.from_context({**event.to_context(),'unknown':True})
        with self.assertRaises(SpellCastEventError):
            SpellCastEvent(schema_version=5,creature_target_controllers=(),**base)

    def test_unsupported_multitarget_lowering_group_cannot_admit_an_empty_trusted_spell(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lowering.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/cast-creature-targets.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry()
                record=replace(payment_record('Put a +1/+1 counter on target creature.\nPut a +1/+1 counter on target creature.\nPut a +1/+1 counter on target creature.\nPut a +1/+1 counter on target creature.\nPut a +1/+1 counter on target creature.'),type_line='Sorcery')
                ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status)
                program=compile_best_available_card_program(db,record,semantic_registry=SemanticRegistry(),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertTrue(any(r.get('kind')=='program_lowering' for r in program.residuals))
                binding=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'])
                self.assertIn('trust_basis:unresolved',binding['blockers'])
                from quorune.compiler.unlock_frontier import analyze_card_unlocks
                frontier=analyze_card_unlocks(ir,program=program,program_error=None,capabilities=registry,profile='commander_review')
                self.assertIn('effect_clause:ordered-effect-composition',frontier['minimum_known_blocker_set'])
                self.assertEqual(5,sum(a['status']=='exact' for a in frontier['abilities']))
                self.assertTrue(any(a['kind']=='program_lowering' and a['status']=='unresolved' for a in frontier['abilities']))

    def test_multiline_spell_carrier_cannot_drop_clauses_or_alias_distinct_target_roles(self):
        from quorune.compiler.program_generation import _is_closed_effect_program
        from quorune.card_programs.model import CardProgram
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lowering.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/cast-creature-targets.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry()
                program=compile_best_available_card_program(db,db.lookup('Martial Glory'),semantic_registry=SemanticRegistry(),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual(1,len(program.abilities))
                spell=program.abilities[0];self.assertTrue(_is_closed_effect_program(spell))
                alias=replace(spell,effects=[spell.effects[0],{**spell.effects[1],'card':'$target.0'}])
                self.assertFalse(_is_closed_effect_program(alias))
                self.assertFalse(_is_closed_effect_program(replace(spell,effects=spell.effects[:1])))
                self.assertEqual(program.fingerprint,CardProgram.from_dict(program.to_dict()).fingerprint)

    def test_registered_cast_target_probe_requires_complete_actual_runtime_binding(self):
        from scripts.work_selection_cohort_measurements import _measurement
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'probe.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/cast-creature-targets.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry();record=db.lookup('Lecturing Scornmage')
                records=(record,replace(record,oracle_id='fixture:cast-target-unrepresented-sibling',oracle_text=record.oracle_text+'\nWhenever a player sneezes, draw a card.'))
                raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                row=next(r for r in raw['capabilities'] if r['id']=='trigger.event.spell_cast_creature_target')
                row.update(status='blocked',blockers=['Independent pre-support baseline'])
                baseline=[analyze_card_unlocks(compile_oracle_card(r,capability_registry=CapabilityRegistry(raw),capability_profile='commander_review'),
                    program=None,program_error=None,capabilities=registry,profile='commander_review') for r in records]
                measured=_measurement(frontier={'cards':baseline},bundle={
                    'bundle_id':'bundle:cast-creature-target','measurement_probe_id':'cast-creature-target-existing-owner-v1'},
                    cards_by_oracle_id={r.oracle_id:r for r in records},coverage={
                        'minimum_complete_card_gain':50,'minimum_exact_ability_gain':100,'minimum_material_residual_reduction':100},
                    cohort_fingerprint='original-cast-target-regression',database=db)
                self.assertEqual(1,measured['complete_card_gain']);self.assertEqual(2,measured['exact_ability_gain'])
                self.assertFalse(measured['grants_gameplay_trust'])

    def test_targeted_cast_grammar_requires_new_capability_and_keeps_independent_siblings(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry=load_default_capability_registry()
        record=payment_record('Whenever you cast an instant or sorcery spell that targets a creature, put a +1/+1 counter on this creature.')
        ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status)
        self.assertIn('trigger.event.spell_cast_creature_target',ir.faces[0].nodes[0].capability_dependencies)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('trigger.event.spell_cast_creature_target','trigger.event.normalized_spell_cast'):
            value=deepcopy(raw);row=next(r for r in value['capabilities'] if r['id']==cap)
            row.update(status='blocked',blockers=['Independent cast-target dependency omission'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(value),capability_profile='commander_review').status)
        for text in ('Whenever you cast a spell that targets only a creature, draw a card.',
            'Whenever you cast a spell that targets two creatures, draw a card.',
            'Whenever you cast a spell that targets a creature for the first time each turn, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(payment_record(text),capability_registry=registry,capability_profile='commander_review').status)
        self.assertNotEqual('exact',compile_oracle_card(replace(record,oracle_text=record.oracle_text+'\nWhenever a player sneezes, draw a card.'),capability_registry=registry,capability_profile='commander_review').status)


class CastCreatureTargetActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/fixed-resolution-payment-cards.json',ROOT/'tests/fixtures/cast-creature-targets.json',ROOT/'tests/fixtures/event-card-return.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Cast qualifier deck',[DeckEntry('Generic Payment Commander',1,'commander'),DeckEntry('Generic Payment Plains',30)],['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        s=CommanderSession.create(self.db,{seat:deepcopy(self.deck) for seat in 'ABCD'},first_player='A',seed=seed,
            config=GameConfig(seed=seed,auto_pass_empty_priority=False));keep_all(s);e=s.engine
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        names={'Martial Glory','Common Bond','Seeds of Strength','Lead by Example','Generic Sacrifice Target Cast','Lecturing Scornmage','Melancholic Poet','Season of Growth','Mockingbird, Ace Agent','Giant Growth',
            "Altar's Reap",'Demonic Vigor','Blessed Defiance','Cackling Counterpart','Generic Payment Commander','Generic Payment Plains'}
        records=tuple(r for r in self.db.iter_cards() if r.name in names and compile_oracle_card(r,capability_registry=self.registry,capability_profile='commander_review').status=='exact')
        register_generated_programs(self.db,e.semantics,records,trust_level='trusted',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_effect_programs=True,
            promote_exact_trigger_programs=True,promote_exact_capability_declarations=True)
        return s

    def add(self,e,name,ref,*,owner='A',controller=None,zone='battlefield'):
        r=self.db.lookup(name);controller=controller or owner
        card=CardInstance(object_id='cast-qualifier:'+ref,ref=ref,oracle_id=r.oracle_id,printed_name=r.name,owner=owner,
            controller=controller,zone=zone,zone_timestamp=e._next_zone_timestamp(),known_to=[owner] if zone in {'hand','library'} else list(e.seats),
            revealed_to=[] if zone in {'hand','library'} else list(e.seats))
        e.state.cards[card.object_id]=card;e.state.players[controller if zone=='battlefield' else owner].zones[zone].append(card.object_id);return card

    def ready(self,s,spell,mana):
        e=s.engine;e.state.started=True;e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main'
        e.state.players['A'].mana_pool.update(mana);e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('A');e.pump()
        return next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+spell.ref)

    def checkpoint(self,s):s.initial_checkpoint=checkpoint_envelope(s.state);s.commands.clear();s.decisions.clear()

    def resolve(self,s):
        for _ in range(32):
            if not s.state.stack:return
            if s.state.pending_decision and s.state.pending_decision.kind!='priority':return
            r=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(r.ok,r.summary)
        self.fail('Cast qualifier resolution did not finish')

    def replay(self,s):
        expected=authoritative_state_hash(s.state)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'record';s.save(path);r=replay_record(path,self.db,verify=True)
        self.assertTrue(r['ok']);self.assertEqual(expected,r['final_state_hash'])

    def test_actual_repartee_cast_checks_target_type_controller_and_invalid_command_rollback(self):
        s=self.session(1150901);e=s.engine;source=self.add(e,'Lecturing Scornmage','SCORNMAGE')
        self.add(e,'Lecturing Scornmage','OPPONENT-SOURCE',owner='B')
        target=self.add(e,'Generic Payment Commander','CREATURE',owner='B')
        land=self.add(e,'Generic Payment Plains','LAND')
        spell=self.add(e,'Giant Growth','GROWTH',zone='hand');action=self.ready(s,spell,{'G':1});self.checkpoint(s)
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:A',{'action_id':action['id'],'targets':[land.ref],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,len(s.state.stack));self.resolve(s)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,s.state.cards['cast-qualifier:OPPONENT-SOURCE'].counters.get('+1/+1',0));self.replay(s)

    def test_cast_target_fact_omission_mutant_is_killed_by_actual_trigger_outcome(self):
        from quorune.rules.casting import commit as owner
        with mock.patch.object(owner,'_cast_creature_target_controllers',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_repartee_cast_checks_target_type_controller_and_invalid_command_rollback()

    def test_aura_cast_qualifies_controlled_creature_draw_but_not_repartee_with_private_replay(self):
        s=self.session(1150902);e=s.engine;growth=self.add(e,'Season of Growth','SEASON')
        scorn=self.add(e,'Lecturing Scornmage','SCORNMAGE')
        target=self.add(e,'Generic Payment Commander','CREATURE',owner='B',controller='A')
        aura=self.add(e,'Demonic Vigor','VIGOR',zone='hand')
        action=self.ready(s,aura,{'B':1});self.checkpoint(s)
        before=len(s.state.players['A'].zones['hand'])
        hidden=e.state.cards[s.state.players['A'].zones['library'][0]].ref
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(2,len(s.state.stack));self.resolve(s)
        self.assertEqual(before,len(s.state.players['A'].zones['hand']))
        self.assertEqual(0,s.state.cards[scorn.object_id].counters.get('+1/+1',0))
        self.assertEqual(target.object_id,s.state.cards[aura.object_id].attached_to)
        for seat in 'BCD':self.assertNotIn(hidden,str(s.packet('pilot:'+seat,full=True)))
        self.replay(s)

    def test_creature_target_controller_and_caster_are_independent_qualifiers(self):
        s=self.session(1150903);e=s.engine;self.add(e,'Season of Growth','OWN-SEASON')
        self.add(e,'Season of Growth','OTHER-SEASON',owner='B')
        target=self.add(e,'Generic Payment Commander','OTHER-CREATURE',owner='B')
        spell=self.add(e,'Giant Growth','GROWTH',zone='hand')
        action=self.ready(s,spell,{'G':1});self.checkpoint(s)
        before={seat:len(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(1,len(s.state.stack));self.resolve(s)
        self.assertEqual(before['A']-1,len(s.state.players['A'].zones['hand']))
        self.assertEqual(before['B'],len(s.state.players['B'].zones['hand']));self.replay(s)

    def test_target_sacrificed_as_casting_cost_is_ignored_without_last_known_target_information(self):
        s=self.session(1150904);e=s.engine;scorn=self.add(e,'Lecturing Scornmage','SCORNMAGE')
        season=self.add(e,'Season of Growth','SEASON')
        target=self.add(e,'Generic Payment Commander','PRICE-AND-TARGET')
        spell=self.add(e,'Generic Sacrifice Target Cast','SPELL',zone='hand')
        action=self.ready(s,spell,{'G':1});self.checkpoint(s)
        before=len(s.state.players['A'].zones['hand'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'sacrifice_cards':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual(1,len(s.state.stack));self.resolve(s)
        self.assertEqual(0,s.state.cards[scorn.object_id].counters.get('+1/+1',0))
        self.assertEqual(before-1,len(s.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.replay(s)

    def test_support_cast_with_two_targets_queues_one_trigger_and_zero_targets_queue_none(self):
        for seed,targets in ((1150905,True),(1150906,False)):
            with self.subTest(targets=targets):
                s=self.session(seed);e=s.engine;source=self.add(e,'Lecturing Scornmage','SCORNMAGE')
                first=self.add(e,'Generic Payment Commander','FIRST',owner='B')
                second=self.add(e,'Generic Payment Commander','SECOND',owner='C')
                spell=self.add(e,'Lead by Example','SUPPORT',zone='hand')
                action=self.ready(s,spell,{'G':1,'C':1});self.checkpoint(s)
                accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[first.ref,second.ref] if targets else [],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(2 if targets else 1,len(s.state.stack));self.resolve(s)
                self.assertEqual(1 if targets else 0,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s)

    def test_copy_owner_does_not_publish_another_cast_occurrence(self):
        s=self.session(1150907);e=s.engine;source=self.add(e,'Lecturing Scornmage','SCORNMAGE')
        first=self.add(e,'Generic Payment Commander','FIRST',owner='B')
        second=self.add(e,'Generic Payment Commander','SECOND',owner='C')
        spell=self.add(e,'Giant Growth','GROWTH',zone='hand');action=self.ready(s,spell,{'G':1})
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        original=next(item for item in s.state.stack if item.kind=='spell')
        copied=e._copy_stack_item(controller='A',target=original,targets=[second.ref],target_groups={},reason='Independent cast-only copy-owner witness')
        self.assertEqual('spell_copy',copied.kind)
        self.assertEqual(3,len(s.state.stack))
        # This diagnostic checkpoints after the copy owner; actual cast command
        # outcomes are covered separately from before casting.
        self.checkpoint(s);self.resolve(s)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s)

    def test_multiline_targets_keep_independent_roles_and_allow_repeated_target_instances(self):
        for seed,repeated in ((1150908,False),(1150909,True)):
            with self.subTest(repeated=repeated):
                s=self.session(seed);e=s.engine;source=self.add(e,'Lecturing Scornmage','SCORNMAGE')
                first=self.add(e,'Generic Payment Commander','FIRST',owner='B')
                second=first if repeated else self.add(e,'Generic Payment Commander','SECOND',owner='C')
                spell=self.add(e,'Martial Glory','GLORY',zone='hand')
                before_first=e._effective_card_data(first);before_second=e._effective_card_data(second)
                action=self.ready(s,spell,{'R':1,'W':1});self.checkpoint(s)
                accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[first.ref,second.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(2,len(s.state.stack));self.resolve(s)
                after_first=e._effective_card_data(s.state.cards[first.object_id]);after_second=e._effective_card_data(s.state.cards[second.object_id])
                self.assertEqual(int(before_first['power'])+3,int(after_first['power']))
                self.assertEqual(int(before_first['toughness'])+(3 if repeated else 0),int(after_first['toughness']))
                self.assertEqual(int(before_second['toughness'])+3,int(after_second['toughness']))
                self.assertEqual(int(before_second['power'])+(3 if repeated else 0),int(after_second['power']))
                self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s)

    def test_partial_invalid_multiline_target_keeps_only_its_legal_clause_and_replays(self):
        s=self.session(1150910);e=s.engine
        first=self.add(e,'Generic Payment Commander','FIRST')
        second=self.add(e,'Generic Payment Commander','SECOND',owner='B')
        spell=self.add(e,'Martial Glory','GLORY',zone='hand')
        # Reuse an actual sacrifice spell from the existing fixture owner.
        reap=self.add(e,"Altar's Reap",'REAP',zone='hand')
        before=e._effective_card_data(second)
        action=self.ready(s,spell,{'R':1,'W':1,'B':1,'C':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[first.ref,second.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+reap.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'sacrifice_cards':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        after=e._effective_card_data(s.state.cards[second.object_id])
        self.assertEqual(int(before['power']),int(after['power']))
        self.assertEqual(int(before['toughness'])+3,int(after['toughness']))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone);self.replay(s)

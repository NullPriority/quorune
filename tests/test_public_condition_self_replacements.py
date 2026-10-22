from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.resolution_conditions import validate_resolution_condition_instruction
from quorune.rules.resolution_condition_shapes import resolution_condition_node_capabilities
from scripts.build_test_database import build_fixture_database
import test_banding as controls
from quorune.rules.evidence_storage import read_evidence_document

TARGET='resolution.effect.public_same_target_self_replacement'
SOURCE='resolution.effect.public_source_self_replacement'
LAND='condition.land_entered_this_turn'
NAMES=('Brimstone Volley','Cabal Ritual','Cackling Flames',"Chandra's Triumph",'Far Wanderings','Feed the Clan',
    'Fiery Impulse','Firecannon Blast','Galvanic Blast','Gather the Townsfolk','Grizzly Fate','Groundswell',
    'Hunger of the Howlpack','Join the Dead','Might Beyond Reason','Mirran Mettle','Mysteries of the Deep',
    "Predator's Howl",'Rest for the Weary','Stitch Together','Stubborn Denial','Thermal Blast','Tomb Hex',
    'Tragic Fall','Tragic Slip','Traverse the Ulvenwald','Unholy Heat')


class PublicConditionSelfReplacementTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.BandingRuntimeTests.session
    add=controls.BandingRuntimeTests.add
    prepare=controls.BandingRuntimeTests.prepare
    checkpoint=controls.BandingRuntimeTests.checkpoint
    resolve=controls.BandingRuntimeTests.resolve
    replay=controls.BandingRuntimeTests.replay
    binding=controls.BandingRuntimeTests.binding
    priority=controls.BandingRuntimeTests.priority
    private_replay=controls.BandingRuntimeTests.private_replay
    advance=controls.BandingRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'public-condition-self-replacements.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-condition-self-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public self replacements',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def start(self,seed):
        s=self.session(seed)
        # Keep the opening fixture cards accounted for without imposing a hand condition.
        for seat in 'ABCD':
            p=s.state.players[seat]
            for oid in tuple(p.zones['hand']):
                p.zones['hand'].remove(oid);p.zones['library'].append(oid);s.state.cards[oid].zone='library'
        return s

    def reject(self,s,command,seat):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def cast(self,s,card,targets=(),*,seat='A',payment=None):
        self.priority(s,seat)
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        action=next((r for r in rows if r['id']=='cast:'+card.ref),None);self.assertIsNotNone(action,card.printed_name)
        command={'action_id':action['id'],'targets':list(targets),'pay':'auto'}
        if payment is not None:command.update(pay='manual',payment=payment)
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def ready(self,s,*,actor='A',active='A'):
        self.prepare(s,actor=actor,active=active,mana={k:200 for k in 'CWUBRG'});self.checkpoint(s)

    def graveyard(self,s,names):
        return [self.add(s.engine,name,zone='graveyard',ref='public-gy-'+str(i)) for i,name in enumerate(names)]

    def artifacts(self,s):
        return [self.add(s.engine,'Memnite',ref='public-artifact-'+str(i)) for i in range(3)]

    def play_land(self,s,land,seat='A'):
        self.priority(s,seat)
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r.get('card')==land.ref and r.get('kind')!='cast')
        command={'action_id':row['id']};self.reject(s,command,'D')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def search(self,s,count,*,ref=None):
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.search',decision.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];options=ctx['search_cards']
        refs=[ref] if ref is not None else [row['id'] for row in options[:count]]
        self.assertEqual(count,len(refs))
        command={'action_id':'choose','search_cards':refs};self.reject(s,command,'C')
        s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));return s,refs

    def private(self,s):return self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='public-private-c')

    def wrapper(self,name):
        ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
        node=next((n for n in ir.faces[0].nodes if any(e.get('op')=='apply_if_public_condition' for e in n.effects)),None)
        self.assertIsNotNone(node,(name,ir.material_residuals))
        return ir,node

    def test_complete_originals_preserve_spans_siblings_and_independent_runtime_minima(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        s=self.start(787000)
        for name in NAMES:
            card=self.db.lookup(name);ir,node=self.wrapper(name)
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end],name)
            if name=='Grizzly Fate':self.assertTrue(any('flashback' in n.mechanics for n in ir.faces[0].nodes))
            registry=SemanticRegistry()
            program=compile_best_available_card_program(self.db,card,semantic_registry=registry,
                capability_registry=self.registry,capability_profile='commander_review')
            bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            source=self.add(s.engine,name,zone='hand')
            executable=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(e.get('op')=='apply_if_public_condition' for e in p.effects))
            for cap in (TARGET,SOURCE,LAND):
                if cap not in executable.capability_dependencies:continue
                deps=tuple(c for c in executable.capability_dependencies if c!=cap)
                changed=replace(executable,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'],(name,cap));self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_target_binding_codecs_reject_drift_unknown_versions_and_source_role_changes(self):
        for name,version in [('Thermal Blast',8),('Cabal Ritual',9)]:
            ir,node=self.wrapper(name);effect=deepcopy(node.effects[-1]);self.assertEqual(version,effect['schema_version'])
            validate_resolution_condition_instruction(effect)
            for update in ({'schema_version':True},{'schema_version':10},{'extra':1},{'otherwise_effects':[]}):
                with self.assertRaises(ValueError):validate_resolution_condition_instruction({**effect,**update})
            changed=deepcopy(effect);changed['effects'][0]['op']='unowned'
            self.assertFalse(resolution_condition_node_capabilities(effects=(changed,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        ir,node=self.wrapper('Thermal Blast');effect=deepcopy(node.effects[-1])
        changed=deepcopy(effect);changed['target_binding']['count']=2
        with self.assertRaises(ValueError):validate_resolution_condition_instruction(changed)
        changed=deepcopy(effect);changed['target_binding']['types_any']=['artifact']
        self.assertFalse(resolution_condition_node_capabilities(effects=(changed,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        changed=deepcopy(effect);changed['effects'][0]['target']='$target.1'
        self.assertFalse(resolution_condition_node_capabilities(effects=(changed,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        changed=deepcopy(effect);changed['effects'][0]['source']='$target.0'
        self.assertFalse(resolution_condition_node_capabilities(effects=(changed,),target_schema=node.target_schema,mechanic_ids=node.mechanics))

    def test_morbid_actual_token_death_selects_one_damage_counter_stat_and_token_branch(self):
        for mode in (False,True):
            s=self.start(787100+mode);target=self.add(s.engine,'Generic Public Replacement Target')
            victim_ref=s.engine.create_token('B',name='Generic Public Morbid Token',characteristics={'type_line':'Token Creature — Bear','power':'2','toughness':'2','colors':['G']})[0]
            victim=s.engine._resolve_object('B',victim_ref,zones={'battlefield'})
            originals=[self.add(s.engine,n,zone='hand') for n in ['Brimstone Volley','Hunger of the Howlpack','Tragic Slip',"Predator's Howl"]]
            bolt=self.add(s.engine,'Lightning Bolt',zone='hand') if mode else None
            private=self.private(s);self.ready(s)
            if bolt:self.cast(s,bolt,[victim.ref]);self.assertIsNone(self.resolve(s))
            before_tokens=sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values())
            for card in originals:
                refs=['B'] if card.printed_name=='Brimstone Volley' else [] if card.printed_name=="Predator's Howl" else [target.ref]
                self.cast(s,card,refs);self.assertIsNone(self.resolve(s))
            self.assertEqual(35 if mode else 37,s.state.players['B'].life)
            self.assertEqual(3 if mode else 1,s.state.cards[target.object_id].counters.get('+1/+1',0))
            self.assertEqual((20,20) if mode else (30,30),tuple(s.engine._numeric_stat(target.object_id,k) for k in ['power','toughness']))
            self.assertEqual(3 if mode else 1,sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values())-before_tokens)
            self.private_replay(s,private)

    def test_metalcraft_damage_stats_and_spell_lifelink_keep_original_source_controller(self):
        for mode in (False,True):
            s=self.start(787110+mode);target=self.add(s.engine,'Generic Public Replacement Target')
            if mode:self.artifacts(s)
            cards=[self.add(s.engine,n,zone='hand') for n in ['Galvanic Blast','Mirran Mettle','Generic Public Replacement Life Bolt']]
            private=self.private(s);self.ready(s)
            for c in cards:
                self.cast(s,c,[target.ref] if c.printed_name=='Mirran Mettle' else ['B']);self.assertIsNone(self.resolve(s))
            self.assertEqual(44 if mode else 42,s.state.players['A'].life)
            self.assertEqual(32 if mode else 36,s.state.players['B'].life)
            self.assertEqual(34 if mode else 32,s.engine._numeric_stat(target.object_id,'power'))
            self.private_replay(s,private)

    def test_hellbent_checks_resolution_hand_after_both_spells_leave_the_hand(self):
        for mode in (False,True):
            s=self.start(787120+mode);target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
            flame=self.add(s.engine,'Cackling Flames',zone='hand');fall=self.add(s.engine,'Tragic Fall',zone='hand')
            if not mode:self.add(s.engine,'Generic Bound Growth',zone='hand',ref='public-held-card')
            private=self.private(s);self.ready(s);self.cast(s,flame,['B']);self.cast(s,fall,[target.ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(35 if mode else 37,s.state.players['B'].life)
            self.assertEqual(17 if mode else 27,s.engine._numeric_stat(target.object_id,'toughness'))
            self.private_replay(s,private)

    def test_threshold_spell_is_not_a_graveyard_card_until_it_finishes_and_flashback_exiles(self):
        for mode in (False,True):
            s=self.start(787130+mode);filler=self.graveyard(s,['Generic Bound Plains']*5)
            target=self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='public-stitch-target')
            cards={n:self.add(s.engine,n,zone='hand') for n in ['Cabal Ritual','Grizzly Fate','Stitch Together']}
            private=self.private(s);self.ready(s)
            order=['Cabal Ritual','Grizzly Fate','Stitch Together'] if not mode else ['Stitch Together','Grizzly Fate','Cabal Ritual']
            for name in order:
                c=cards[name];self.cast(s,c,[target.ref] if name=='Stitch Together' else [],payment={'C':1,'B':1} if name=='Cabal Ritual' else None)
                prior=s.state.players['A'].mana_pool['B'];self.assertIsNone(self.resolve(s))
                if name=='Cabal Ritual':self.assertEqual(5 if mode else 3,s.state.players['A'].mana_pool['B']-prior)
            self.assertEqual('hand' if mode else 'battlefield',s.state.cards[target.object_id].zone)
            self.assertEqual(2 if mode else 4,sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values()))
            self.cast(s,s.state.cards[cards['Grizzly Fate'].object_id],payment={'C':5,'G':2});s=self.replay(s,load=True)
            self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[cards['Grizzly Fate'].object_id].zone)
            self.private_replay(s,private)

    def test_landfall_uses_current_controller_entry_and_resets_after_the_turn(self):
        for mode in (False,True):
            s=self.start(787140+mode);target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
            cards=[self.add(s.engine,n,zone='hand') for n in ['Groundswell','Tomb Hex','Rest for the Weary','Mysteries of the Deep']]
            land=self.add(s.engine,'Generic Bound Plains',zone='hand');private=self.private(s);self.ready(s)
            if mode:
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                action=next(r for r in rows if r.get('card')==land.ref and r.get('kind')!='cast')
                result=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
            initial=len(s.state.players['A'].zones['hand'])
            for c in cards:
                refs=[] if c.printed_name=='Mysteries of the Deep' else ['B'] if c.printed_name=='Rest for the Weary' else [target.ref]
                self.cast(s,c,refs);self.assertIsNone(self.resolve(s))
            self.assertEqual(48 if mode else 44,s.state.players['B'].life)
            self.assertEqual(initial-4+(3 if mode else 2),len(s.state.players['A'].zones['hand']))
            self.assertEqual(30,s.engine._numeric_stat(target.object_id,'power'));self.private_replay(s,private)
        s=self.start(787145);land=self.add(s.engine,'Generic Bound Plains',zone='hand')
        first=self.add(s.engine,'Rest for the Weary',zone='hand',ref='public-rest-this-turn')
        second=self.add(s.engine,'Rest for the Weary',zone='hand',ref='public-rest-next-turn')
        for i in range(2):self.add(s.engine,'Generic Bound Plains',ref='public-rest-old-land-'+str(i))
        private=self.private(s);self.ready(s);self.play_land(s,land)
        self.cast(s,first,['B'],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s));self.assertEqual(48,s.state.players['B'].life)
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.cast(s,second,['B']);self.assertIsNone(self.resolve(s));self.assertEqual(52,s.state.players['B'].life)
        self.private_replay(s,private)

    def test_chandra_current_control_spell_mastery_and_graveyard_damage_amounts(self):
        for mode in (False,True):
            s=self.start(787160+mode);target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
            walker=self.add(s.engine,'Generic Public Replacement Chandra',seat='A' if mode else 'B',ref='public-chandra')
            walker.counters['loyalty']=4
            names=['Lightning Bolt','Generic Public Replacement Sorcery']+['Generic Bound Plains']*5 if mode else ['Generic Bound Plains']*6
            self.graveyard(s,names)
            cards=[self.add(s.engine,n,zone='hand') for n in ['Thermal Blast','Fiery Impulse',"Chandra's Triumph"]]
            private=self.private(s);self.ready(s)
            for c in cards:self.cast(s,c,[target.ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual(13 if mode else 8,s.state.cards[target.object_id].marked_damage)
            self.private_replay(s,private)

    def test_delirium_and_descend_keep_card_types_card_counts_and_library_results_distinct(self):
        for mode in (False,True):
            s=self.start(787170+mode);target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
            names=['Generic Bound Body','Sol Ring','Generic Bound Plains','Parallel Lives'] if mode else ['Lightning Bolt','Generic Public Replacement Sorcery','Sol Ring']
            self.graveyard(s,names)
            cards={n:self.add(s.engine,n,zone='hand') for n in ['Unholy Heat','Join the Dead','Might Beyond Reason','Traverse the Ulvenwald']}
            found=self.add(s.engine,'Generic Bound Body',zone='library',ref='public-traverse-creature')
            private=self.private(s);self.ready(s)
            for name in ['Unholy Heat','Join the Dead','Might Beyond Reason']:
                self.cast(s,cards[name],[target.ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual(6 if mode else 2,s.state.cards[target.object_id].marked_damage)
            self.assertEqual(3 if mode else 2,s.state.cards[target.object_id].counters.get('+1/+1',0))
            self.assertEqual(23 if mode else 27,s.engine._numeric_stat(target.object_id,'toughness'))
            self.cast(s,cards['Traverse the Ulvenwald'])
            s,refs=self.search(s,1,ref=found.ref if mode else None)
            chosen=next(c for c in s.state.cards.values() if c.ref==refs[0]);self.assertEqual('hand',chosen.zone)
            if mode:self.assertEqual(found.ref,chosen.ref)
            else:self.assertIn('Basic Land',s.engine._effective_card_data(chosen)['type_line'])
            self.private_replay(s,private)

    def test_ferocious_is_controller_relative_and_raid_survives_attacker_departure(self):
        for mode in (False,True):
            s=self.start(787180+mode);target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
            attacker=self.add(s.engine,'Generic Public Replacement Ferocious' if mode else 'Grizzly Bears')
            attacker.acquired_control_turn_count=-1
            if mode:self.add(s.engine,'Generic Public Replacement Ferocious',ref='public-retained-ferocious')
            blast=self.add(s.engine,'Firecannon Blast',zone='hand');feed=self.add(s.engine,'Feed the Clan',zone='hand')
            bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand') if mode else None
            s.state.players['B'].mana_pool['U']=1
            for i in range(3):self.add(s.engine,'Mountain',ref='public-raid-mountain-'+str(i))
            for i in range(2):self.add(s.engine,'Forest',ref='public-raid-forest-'+str(i))
            self.add(s.engine,'Island',seat='B',ref='public-raid-bounce-island')
            private=self.private(s);self.ready(s)
            if mode:
                self.advance(s,lambda x:x.state.pending_decision is not None and x.state.pending_decision.kind=='combat.attackers')
                result=s.act('pilot:A',{'action_id':'attack','attacks':{attacker.ref:'B'}});self.assertTrue(result.ok,result.summary)
                self.cast(s,bounce,[attacker.ref],seat='B');self.assertIsNone(self.resolve(s))
                self.assertEqual('hand',s.state.cards[attacker.object_id].zone)
                self.advance(s,lambda x:x.state.phase=='postcombat_main')
            self.cast(s,blast,[target.ref]);self.assertIsNone(self.resolve(s))
            self.cast(s,feed);self.assertIsNone(self.resolve(s))
            self.assertEqual(6 if mode else 3,s.state.cards[target.object_id].marked_damage)
            self.assertEqual(50 if mode else 45,s.state.players['A'].life)
            self.private_replay(s,private)

    def test_permanent_graveyard_condition_uses_six_card_types_not_a_subtype(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        condition=fixed_public_state_condition('there are four or more permanent cards in your graveyard',source_name='Generic Count')
        self.assertEqual(4,condition.amount)
        self.assertEqual({'artifact','battle','creature','enchantment','land','planeswalker'},set(condition.quantity.query.types_any))
        self.assertEqual((),condition.quantity.query.subtypes_all);self.assertIs(False,condition.quantity.query.token)

    def test_fateful_hour_replaces_the_complete_token_recipe_before_parallel_lives(self):
        for mode in (False,True):
            s=self.start(787190+mode);self.add(s.engine,'Parallel Lives')
            source=self.add(s.engine,'Gather the Townsfolk',zone='hand');s.state.players['A'].life=5 if mode else 6
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
            self.assertEqual(10 if mode else 4,len(tokens))
            for token in tokens:
                data=s.engine._effective_card_data(token);self.assertIn('Human',data['type_line'])
                self.assertEqual({'W'},set(data['colors']));self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ['power','toughness']))
            self.private_replay(s,private)

    def test_threshold_search_entries_qualify_landfall_without_playing_a_land(self):
        for mode in (False,True):
            s=self.start(787200+mode);self.graveyard(s,['Generic Bound Plains']*(7 if mode else 6))
            wander=self.add(s.engine,'Far Wanderings',zone='hand');rest=self.add(s.engine,'Rest for the Weary',zone='hand')
            private=self.private(s);self.ready(s);self.cast(s,wander)
            s,refs=self.search(s,3 if mode else 1)
            for ref in refs:
                c=next(c for c in s.state.cards.values() if c.ref==ref);self.assertEqual('battlefield',c.zone);self.assertTrue(c.tapped)
            self.cast(s,rest,['B']);self.assertIsNone(self.resolve(s));self.assertEqual(48,s.state.players['B'].life)
            self.private_replay(s,private)

    def test_stubborn_denial_preserves_noncreature_target_and_the_paying_controller(self):
        for mode in (False,True):
            s=self.start(787210+mode)
            if mode:self.add(s.engine,'Generic Public Replacement Ferocious')
            counter=self.add(s.engine,'Stubborn Denial',zone='hand');spell=self.add(s.engine,'Divination',seat='B',zone='hand')
            s.state.players['A'].mana_pool['U']=1
            private=self.private(s);self.ready(s,actor='B',active='B')
            self.cast(s,spell,seat='B',payment={'C':2,'U':1});victim=s.state.stack[-1].ref
            self.cast(s,counter,[victim]);decision=self.resolve(s)
            if mode:self.assertIsNone(decision)
            else:
                self.assertIsNotNone(decision);self.assertEqual('pilot:B',s.pending_principals()[0])
                command={'action_id':'choose','pay':False};self.reject(s,command,'A');s=self.replay(s,load=True)
                result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
            self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
            self.assertEqual(0,len(s.state.players['B'].zones['hand']));self.private_replay(s,private)

    def test_copied_spell_uses_copy_controller_condition_and_its_new_target(self):
        s=self.start(787220);self.artifacts(s)
        blast=self.add(s.engine,'Galvanic Blast',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        target=self.add(s.engine,'Generic Public Replacement Target',seat='B')
        s.state.players['B'].mana_pool['R']=2
        private=self.private(s);self.ready(s);self.cast(s,blast,[target.ref]);original=s.state.stack[-1].ref
        self.cast(s,copy,[original],seat='B');decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        command={'action_id':'choose','targets':['C']};self.reject(s,command,'A');s=self.replay(s,load=True)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['C'].life)
        self.assertEqual(4,s.state.cards[target.object_id].marked_damage)
        self.private_replay(s,private)

    def test_actual_responses_change_current_power_and_artifact_conditions_before_resolution(self):
        for mode in ('power','artifacts'):
            s=self.start(787230+(mode=='artifacts'))
            if mode=='power':
                target=self.add(s.engine,'Generic Public Replacement Ferocious')
                spell=self.add(s.engine,'Feed the Clan',zone='hand')
                response=self.add(s.engine,'Humble',seat='B',zone='hand')
                s.state.players['B'].mana_pool.update({'C':1,'W':1})
            else:
                target=self.artifacts(s)[0]
                spell=self.add(s.engine,'Galvanic Blast',zone='hand')
                response=self.add(s.engine,'Unsummon',seat='B',zone='hand')
                s.state.players['B'].mana_pool['U']=1
            private=self.private(s);self.ready(s)
            self.cast(s,spell,[] if mode=='power' else ['B'])
            self.cast(s,response,[target.ref],seat='B');s=self.replay(s,load=True)
            self.assertIsNone(self.resolve(s))
            self.assertEqual(45 if mode=='power' else 40,s.state.players['A'].life)
            self.assertEqual(40 if mode=='power' else 38,s.state.players['B'].life)
            self.private_replay(s,private)

    def test_wrong_target_and_departed_target_never_execute_either_damage_branch(self):
        s=self.start(787240);self.graveyard(s,['Generic Bound Plains']*7)
        spell=self.add(s.engine,'Thermal Blast',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='B')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=1
        private=self.private(s);self.ready(s)
        self.reject(s,{'action_id':'cast:'+spell.ref,'targets':['B'],'pay':'auto'},'A')
        self.cast(s,spell,[target.ref]);self.cast(s,bounce,[target.ref],seat='B')
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[target.object_id].zone)
        self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.assertEqual(40,s.state.players['B'].life)
        self.private_replay(s,private)

    def test_casting_flashback_removes_its_own_card_before_counting_the_graveyard(self):
        s=self.start(787250);spell=self.add(s.engine,'Grizzly Fate',zone='graveyard')
        self.graveyard(s,['Generic Bound Plains']*6);private=self.private(s);self.ready(s)
        self.assertEqual(7,len(s.state.players['A'].zones['graveyard']))
        self.cast(s,spell,payment={'C':5,'G':2});self.assertEqual(6,len(s.state.players['A'].zones['graveyard']))
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        self.assertEqual(2,sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values()))
        self.private_replay(s,private)

    def test_search_can_find_zero_and_an_opponents_land_entry_does_not_satisfy_yours(self):
        s=self.start(787260);self.graveyard(s,['Generic Bound Plains']*7)
        search=self.add(s.engine,'Far Wanderings',zone='hand');private=self.private(s);self.ready(s)
        before=len(s.state.players['A'].zones['battlefield']);self.cast(s,search);s,refs=self.search(s,0)
        self.assertFalse(refs);self.assertEqual(before,len(s.state.players['A'].zones['battlefield']));self.private_replay(s,private)
        s=self.start(787261);land=self.add(s.engine,'Generic Bound Plains',seat='B',zone='hand')
        rest=self.add(s.engine,'Rest for the Weary',zone='hand');s.state.players['A'].mana_pool.update({'C':1,'W':1})
        private=self.private(s);self.ready(s,actor='B',active='B');self.play_land(s,land,seat='B')
        self.cast(s,rest,['A']);self.assertIsNone(self.resolve(s));self.assertEqual(44,s.state.players['A'].life)
        self.private_replay(s,private)

    def test_same_target_source_schemas_and_opaque_siblings_fail_closed(self):
        from test_bound_effect_programs import record
        cases=[
            'Target creature gets +1/+1 until end of turn. If you have no cards in hand, another target creature gets +3/+3 until end of turn instead.',
            'Draw a card. If you have no cards in hand, draw X cards instead.',
            'Target creature gets +1/+1 until end of turn. If that creature is green, it gets +3/+3 until end of turn instead.',
            'Draw a card. If you cast this spell during your main phase, draw three cards instead.',
            'Draw a card. If you have no cards in hand, you may draw three cards instead.',
        ]
        for text in cases:
            ir=compile_oracle_card(record(text,'Instant'),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)
        original=self.db.lookup('Galvanic Blast')
        changed=replace(original,oracle_text=original.oracle_text+'\nChoose an unrepresented cosmic rule.')
        ir=compile_oracle_card(changed,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        ir,node=self.wrapper('Cabal Ritual');effect=deepcopy(node.effects[-1]);effect['effects'][0]['source']='wrong-source'
        with self.assertRaises(ValueError):validate_resolution_condition_instruction(effect)

    def test_public_caster_draw_branch_delivers_private_cards_only_to_the_declared_target(self):
        for mode in (False,True):
            s=self.start(787150+mode);source=self.add(s.engine,'Generic Public Replacement Draw',zone='hand')
            if not mode:self.add(s.engine,'Generic Bound Growth',zone='hand',ref='public-draw-held-card')
            private=self.private(s);self.ready(s);a=len(s.state.players['A'].zones['hand']);b=len(s.state.players['B'].zones['hand'])
            self.cast(s,source,['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(a-1,len(s.state.players['A'].zones['hand']));self.assertEqual(b+(3 if mode else 2),len(s.state.players['B'].zones['hand']))
            self.private_replay(s,private)

    def test_verified_capability_withdrawals_and_fresh_producer_condition_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in [(TARGET,'Thermal Blast'),(SOURCE,'Cabal Ritual'),(LAND,'Groundswell')]:
            value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn public self-replacement owner'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
        method='test_complete_originals_preserve_spans_siblings_and_independent_runtime_minima'
        with patch('quorune.compiler.public_replacement_rows.public_replacement_material_rows',side_effect=lambda rows,**kw:rows):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        from quorune.replacement.immutable import FrozenMap
        original=PublicResolutionConditionHandler.prepare
        def default_result(handler,effect,context):
            prepared=original(handler,effect,context)
            return replace(prepared,auto_continue=replace(prepared.auto_continue,
                prepend_effects=tuple(FrozenMap(child) for child in effect['otherwise_effects'])))
        method='test_metalcraft_damage_stats_and_spell_lifelink_keep_original_source_controller'
        with patch.object(PublicResolutionConditionHandler,'prepare',default_result):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        for scope,method in [('source','test_threshold_spell_is_not_a_graveyard_card_until_it_finishes_and_flashback_exiles'),
                ('land','test_landfall_uses_current_controller_entry_and_resets_after_the_turn')]:
            def scoped_default(handler,effect,context):
                matched=(effect.get('schema_version')==9 if scope=='source' else
                    effect.get('condition',{}).get('fact')=='controller_lands_entered_this_turn')
                return default_result(handler,effect,context) if matched else original(handler,effect,context)
            with patch.object(PublicResolutionConditionHandler,'prepare',scoped_default):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

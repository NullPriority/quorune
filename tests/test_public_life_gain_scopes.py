"""Pinned CR119/608: fixed public life gain is one affected-player batch."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.life_templates import fixed_life_effect_template
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry
from quorune.rules.fixed_controller_effect_shapes import fixed_life_node_capabilities
from quorune.rules.public_life_gain_scopes import (
    PUBLIC_LIFE_GAIN_FIELD as FIELD, PUBLIC_LIFE_GAIN_CAPABILITY as CAPABILITY,
    validate_public_life_gain, public_life_gain_runtime_dependencies,
)
from quorune.effect_runtime import life_effects
from quorune.record import authoritative_state_hash
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_life_effect_runtime as life_controls
import test_public_condition_self_replacements as controls
import test_fixed_player_life_effects as compiler_controls
import inherited_composition_fixture as combat

FIXTURE=ROOT/'tests/fixtures/public-life-gain-scopes-cards.json'
CANDIDATES=('Centaur Peacemaker','Fall to Earth','Soldevi Steam Beast','Pestilent Cauldron // Restorative Burst','Armistice')


class PublicLifeGainModelTests(unittest.TestCase):
    def effect(self,scope='all_active_players'):
        return {'op':'life',FIELD:1,'scope':scope,'delta':3}

    def test_closed_scopes_and_target_opponent_preserve_historical_player_bytes(self):
        for who,scope in (('Each player','all_active_players'),('Each opponent','controller_opponents')):
            template=fixed_life_effect_template(who+' gains 3 life.')
            self.assertEqual(self.effect(scope),template.effect)
            self.assertIn(CAPABILITY,fixed_life_node_capabilities(effects=(template.effect,),target_schema=None,mechanic_ids=template.mechanic_ids))
        template=fixed_life_effect_template('Target opponent gains 2 life.')
        self.assertEqual('opponent',template.target_schema['player_relation'])
        self.assertEqual({'op':'life','player':'$target.0','delta':2},template.effect)
        self.assertEqual({'life.change.effect','target.revalidate_resolution'},set(fixed_life_node_capabilities(effects=(template.effect,),target_schema=template.target_schema,mechanic_ids=template.mechanic_ids)))
        self.assertEqual({'op':'life','player':'$target.0','delta':5},fixed_life_effect_template('Target player gains 5 life.').effect)
        self.assertEqual({'op':'life','player':'$controller','delta':3},fixed_life_effect_template('You gain 3 life.').effect)
        for text in ('Each player gains X life.','Each opponent gains that much life.','Each player with a creature gains 2 life.','Each player gains 0 life.','Each player gains 3 life. Draw a card.'):
            self.assertIsNone(fixed_life_effect_template(text),text)

    def test_marker_amount_and_conflicting_recipients_reject_before_changes(self):
        cases=[{FIELD:True},{FIELD:2},{FIELD:None},{'delta':True},{'delta':0},{'delta':-1},{'scope':'opponents_of_target'},{'player':'A'},{'players':['A']},{'unexpected':1}]
        missing=self.effect();missing.pop(FIELD)
        for effect in [missing,*({**self.effect(),**changes} for changes in cases)]:
            host=life_controls._Host(('A','B','C','D'))
            with self.subTest(effect=effect),self.assertRaises((ValueError,TypeError)):
                validate_public_life_gain(effect)
            with self.assertRaises(Exception):
                life_controls.LifeEffectRuntimeTests.apply(host,effect)
            self.assertEqual([40]*4,[p.life for p in host.state.players.values()])
            self.assertFalse(host.logs);self.assertFalse(host.semantic_events)

    def test_public_scope_commits_one_current_active_batch_and_opponents_exclude_actor(self):
        for scope,expected in (('all_active_players',[43,43,43,40]),('controller_opponents',[43,40,43,40])):
            host=life_controls._Host(('A','B','C','D'));host.active_seats.remove('D')
            with patch('quorune.effect_runtime.life_effects.commit_life_change_batch',wraps=life_effects.commit_life_change_batch) as commit:
                life_controls.LifeEffectRuntimeTests.apply(host,self.effect(scope),actor='B')
            self.assertEqual(1,commit.call_count)
            self.assertEqual(expected,[host.state.players[p].life for p in 'ABCD'])
            self.assertEqual(['A','B','C'] if scope=='all_active_players' else ['A','C'],[ctx['player'] for event,ctx,_ in host.semantic_events if event=='life.gained'])


class PublicLifeGainCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_complete_originals_bind_normally_and_unknown_siblings_remain(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'life.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                semantics=SemanticRegistry()
                for name in (*CANDIDATES,'Aria of Flame','Putrid Warrior','Grollub','Fiery Justice','Grove of the Burnwillows'):
                    card=db.lookup(name,fuzzy=False)
                    register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                    program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                    bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                    self.assertEqual(name in CANDIDATES,bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),(name,bound['blockers']))
                    if name in CANDIDATES:self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review').status)

    def test_terminal_move_requires_the_exact_selected_spell_face(self):
        from quorune.compiler.declared_effect_amounts import declared_spell_effect_compiler
        from quorune.oracle_ir import _effect_template
        base=compiler_controls.fixture_card('')
        faces=({'name':'Front','type_line':'Artifact','oracle_text':'','mana_cost':'{1}'},
               {'name':'Back','type_line':'Sorcery','oracle_text':'You gain 3 life. Exile Back.','mana_cost':'{G}'})
        card=replace(base,name='Front // Back',type_line='Artifact // Sorcery',layout='modal_dfc',faces=faces)
        text='You gain 3 life. Exile Back.'
        for spell,face,expected in ((True,'Back',True),(True,'Front',False),(True,'Unknown',False),(False,'Back',False),(True,None,False)):
            compile_effect=declared_spell_effect_compiler(card,is_spell=spell,oracle_text=text,compile_effect=lambda text,**ctx:_effect_template(text,card_name=ctx['card_name']),face_name=face)
            self.assertEqual(expected,compile_effect(text,card_name=face or card.name)[0] is not None,(spell,face))

    def test_scope_capability_withdrawal_and_stripped_coverage_fail_closed(self):
        card=compiler_controls.fixture_card('Each player gains 4 life.')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['scope withdrawn'])
        withdrawn=CapabilityRegistry(raw);withdrawn.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=withdrawn,capability_profile='commander_review').status)
        effect=fixed_life_effect_template('Each player gains 4 life.').effect
        self.assertEqual({CAPABILITY,'life.change.effect'},set(public_life_gain_runtime_dependencies(SimpleNamespace(effects=(effect,)))))
        for bad in ({**effect,FIELD:False},{k:v for k,v in effect.items() if k!=FIELD}):
            with self.assertRaises(ValueError):public_life_gain_runtime_dependencies(SimpleNamespace(effects=(bad,)))


class PublicLifeGainRuntimeTests(unittest.TestCase):
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
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'public-life.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public life gain',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def life(self,s):return [s.state.players[p].life for p in 'ABCD']

    def world(self,seed,name):
        s=self.start(seed);source=self.add(s.engine,name,zone='hand')
        self.assertTrue(self.binding(s,source)['strict_capability_ready'])
        private=self.private(s)
        return s,source,private

    def test_actual_peacemaker_entry_gains_for_all_and_survives_source_departure(self):
        s,source,private=self.world(788200,'Centaur Peacemaker')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=1
        self.ready(s);self.cast(s,source)
        while s.state.cards[source.object_id].zone!='battlefield':
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.stack);self.cast(s,bounce,[source.ref],seat='B')
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual([44]*4,self.life(s));self.private_replay(s,private)

    def test_actual_fall_exile_precedes_gain_and_illegal_target_gates_every_result(self):
        for illegal in (False,True):
            s,source,private=self.world(788210+illegal,'Fall to Earth')
            target=self.add(s.engine,'Grizzly Bears',seat='B')
            bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=1
            self.ready(s);self.cast(s,source,[target.ref])
            if illegal:self.cast(s,bounce,[target.ref],seat='B')
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual([40 if illegal else 43]*4,self.life(s))
            self.assertEqual('hand' if illegal else 'exile',s.state.cards[target.object_id].zone)
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_actual_peacemaker_respects_prohibition_and_affected_player_multiplier(self):
        for prohibited in (False,True):
            s,source,private=self.world(788220+prohibited,'Centaur Peacemaker')
            self.add(s.engine,'Generic Life Gain Prohibition' if prohibited else 'Boon Reflection',seat='B')
            self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertEqual([40]*4 if prohibited else [44,48,44,44],self.life(s));self.private_replay(s,private)

    def cast_face(self,s,source,face,targets=()):
        self.priority(s,'A')
        offered=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref and a.get('face')==face)
        command={'action_id':offered['id'],'face':face,'targets':list(targets),'pay':'auto'}
        if offered.get('cost_options'):command['cost_option']=offered['cost_options'][0]['id']
        self.reject(s,command,'C');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def activate(self,s,source,ability,**values):
        self.priority(s,'A')
        row=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+source.ref+':'+ability)
        command={'action_id':row['id'],'pay':'auto',**values};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_fall_basic_landcycling_pays_source_and_keeps_search_private(self):
        s,source,private=self.world(788235,'Fall to Earth')
        land=self.add(s.engine,'Forest',zone='library',ref='fall-basic-find')
        self.add(s.engine,'Springleaf Drum',zone='library',ref='fall-not-land')
        self.ready(s)
        offers=[a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':')]
        self.assertEqual(1,len(offers));command={'action_id':offers[0]['id'],'pay':'auto'}
        self.reject(s,command,'B');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        decision=self.resolve(s);self.assertIsNotNone(decision)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertIn(land.ref,{r['id'] for r in ctx['search_cards']})
        self.assertNotIn('fall-not-land',{r['id'] for r in ctx['search_cards']})
        for seat in 'BCD':self.assertNotIn('search_cards',str(s.packet('pilot:'+seat,full=True)))
        command={'action_id':'choose','search_card':land.ref};self.reject(s,command,'C');s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[land.object_id].zone)
        self.assertEqual([40]*4,self.life(s));self.private_replay(s,private)

    def test_actual_steam_beast_tap_targets_only_an_opponent_and_regenerates(self):
        s,source,private=self.world(788240,'Soldevi Steam Beast')
        drum=self.add(s.engine,'Springleaf Drum');bolt=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand');s.state.players['B'].mana_pool['R']=1
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.activate(s,drum,'ab1',cost_cards=[source.ref],mana_output={'G':1})
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('semantic.target',s.state.pending_decision.kind)
        command={'action_id':'choose','targets':['A']};self.reject(s,command,'A')
        command['targets']=['C'];self.reject(s,command,'B');s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([40,40,42,40],self.life(s))
        self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s))
        self.cast(s,bolt,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped);self.private_replay(s,private)

    def test_actual_burst_zero_partial_and_failed_targets_preserve_self_exile(self):
        for i,mode in enumerate(('zero','one','two','partial','all_illegal')):
            s,source,private=self.world(788250+i,'Pestilent Cauldron // Restorative Burst')
            creature=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='burst-creature')
            land=self.add(s.engine,'Forest',zone='graveyard',ref='burst-land')
            exile=self.add(s.engine,'Cremate',seat='B',zone='hand');s.state.players['B'].mana_pool['B']=1
            self.ready(s)
            targets=[] if mode=='zero' else [creature.ref,land.ref] if mode in ('two','partial') else [creature.ref]
            self.cast_face(s,source,'Restorative Burst',targets)
            if mode in ('partial','all_illegal'):self.cast(s,exile,[creature.ref],seat='B')
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            failed=mode=='all_illegal'
            self.assertEqual([40 if failed else 44]*4,self.life(s))
            self.assertEqual('graveyard' if failed else 'exile',s.state.cards[source.object_id].zone)
            self.assertEqual('exile' if mode in ('partial','all_illegal') else 'hand' if mode in ('one','two') else 'graveyard',s.state.cards[creature.object_id].zone)
            self.assertEqual('hand' if mode in ('two','partial') else 'graveyard',s.state.cards[land.object_id].zone)
            self.private_replay(s,private)

    def test_actual_cauldron_front_retains_discard_pest_life_mill_and_four_card_exile(self):
        s,source,private=self.world(788260,'Pestilent Cauldron // Restorative Burst')
        discard=self.add(s.engine,'Forest',zone='hand',ref='cauldron-discard')
        kill=self.add(s.engine,'Lightning Bolt',zone='hand',ref='cauldron-kill-pest')
        loss=self.add(s.engine,'Lightning Bolt',zone='hand',ref='cauldron-lose-life')
        keys=[self.add(s.engine,'Voltaic Key',ref='cauldron-key-'+str(i)) for i in range(2)]
        cards=[self.add(s.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='cauldron-gy-'+str(i)) for i in range(4)]
        self.ready(s);self.cast_face(s,source,'Pestilent Cauldron');self.assertIsNone(self.resolve(s))
        self.activate(s,source,'ab1',cost_cards=[discard.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[discard.object_id].zone)
        pests=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.controller=='A']
        self.assertEqual(1,len(pests));self.cast(s,kill,[pests[0].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(41,s.state.players['A'].life)
        self.cast(s,loss,['A']);self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['A'].life)
        self.activate(s,keys[0],'ab1',targets=[source.ref]);self.assertIsNone(self.resolve(s))
        sizes={p:len(s.state.players[p].zones['library']) for p in 'BCD'}
        self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s))
        self.assertEqual({p:n-1 for p,n in sizes.items()},{p:len(s.state.players[p].zones['library']) for p in 'BCD'})
        self.activate(s,keys[1],'ab1',targets=[source.ref]);self.assertIsNone(self.resolve(s))
        before=len(s.state.players['A'].zones['hand'])
        self.activate(s,source,'ab3',targets=[c.ref for c in cards]);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+1,len(s.state.players['A'].zones['hand']))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards));self.private_replay(s,private)

    def test_actual_pending_batch_waits_for_affected_players_and_rolls_back_partial_fault(self):
        from quorune import life_change
        from quorune.errors import GameRuleError
        s,source,private=self.world(788270,'Centaur Peacemaker')
        for seat in 'AB':
            for i in range(2):self.add(s.engine,'Boon Reflection',seat=seat,ref='life-double-'+seat+str(i))
        self.ready(s);self.cast(s,source)
        for seat in 'AB':
            decision=self.resolve(s);self.assertEqual('replacement.order',decision.kind)
            self.assertEqual(['pilot:'+seat],list(s.pending_principals()))
            self.assertEqual([40]*4,self.life(s));s=self.replay(s,load=True)
            option=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options'][0]['id']
            command={'action_id':'choose','replacement':option};self.reject(s,command,'D')
            if seat=='B':
                original=life_change.apply_life_changes;partial=[]
                def fault(host,plan):
                    original(host,replace(plan,transitions=plan.transitions[:1]));partial.append(True)
                    raise GameRuleError('Injected public life gain commit fault')
                before=authoritative_state_hash(s.state)
                with patch.object(life_change,'apply_life_changes',fault):result=s.act('pilot:B',command)
                self.assertFalse(result.ok);self.assertTrue(partial);self.assertEqual(before,authoritative_state_hash(s.state))
            result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([56,56,44,44],self.life(s));self.private_replay(s,private)

    def test_actual_armistice_draws_only_for_controller_and_gains_for_target_opponent(self):
        s,source,private=self.world(788280,'Armistice')
        self.add(s.engine,'Boon Reflection',seat='B')
        drawn=self.add(s.engine,'Generic Bound Growth',zone='library',ref='armistice-private-draw')
        library=s.state.players['A'].zones['library'];library.remove(drawn.object_id);library.append(drawn.object_id)
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.reject(s,{'action_id':'activate:'+source.ref+':ab1','targets':['A'],'pay':'auto'},'A')
        before=len(s.state.players['A'].zones['hand'])
        self.activate(s,source,'ab1',targets=['B']);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual([40,46,40,40],self.life(s))
        self.assertEqual(before+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
        for seat in 'BCD':self.assertNotIn(drawn.ref,str(s.packet('pilot:'+seat,full=True)))
        self.private_replay(s,private)

    def test_public_scope_omission_is_killed_by_actual_original_entry(self):
        with patch('quorune.effect_runtime.life_effects._apply_selected_life',return_value={}):
            with self.assertRaises(AssertionError):
                self.test_actual_peacemaker_respects_prohibition_and_affected_player_multiplier()

    def test_runtime_scope_minimum_survives_removed_coverage_and_bad_marker_rejects(self):
        s,source,_=self.world(788230,'Centaur Peacemaker')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
        changed=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])


if __name__=='__main__':unittest.main()

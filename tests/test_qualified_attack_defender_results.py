"""CR508.2a/508.5: qualify one attacker, then retain its defender relation."""
from unittest.mock import patch
import unittest
from quorune.compiler.qualified_attack_subjects import qualified_attack_binding_spec
from quorune.qualified_attack_model import ATTACK_FACT_PREFIX
from quorune.rules.qualified_attacks import qualified_attack_defender_condition_is_closed


class QualifiedAttackDefenderModelTests(unittest.TestCase):
    def condition(self,subject='a Sliver you control'):
        binding=qualified_attack_binding_spec('Whenever '+subject+' attacks, defending player loses 1 life.')
        self.assertIsNotNone(binding);return binding.condition

    def test_existing_typed_owner_proves_one_controlled_attack_across_fixed_public_qualifiers(self):
        for subject in ('a Sliver you control','an Insect, Leech, Slug, or Worm you control',
                'an equipped creature you control','a creature you control with haste','a white creature you control'):
            with self.subTest(subject=subject):
                condition=self.condition(subject)
                self.assertTrue(qualified_attack_defender_condition_is_closed('creature.attacks',condition))
        self.assertFalse(qualified_attack_defender_condition_is_closed('creature.blocks',self.condition()))

    def test_missing_unknown_version_controller_and_batch_conditions_do_not_invent_a_defender(self):
        condition=self.condition();version={'field':ATTACK_FACT_PREFIX+'version','op':'eq','value':1}
        controller={'field':'controller','op':'eq','value':'$source.controller'}
        self.assertIn(version,condition['all']);self.assertIn(controller,condition['all'])
        malformed=[None,{}, {'all':[v for v in condition['all'] if v!=version]},
            {'all':[v for v in condition['all'] if v!=controller]},
            {'all':[{**v,'value':2} if v==version else v for v in condition['all']]},
            {'any':[condition,{'field':'controller','op':'eq','value':'B'}]}]
        for value in malformed:
            self.assertFalse(qualified_attack_defender_condition_is_closed('creature.attacks',value),value)
        for event in ('attack.batch','creature.dies',None):
            self.assertFalse(qualified_attack_defender_condition_is_closed(event,condition))

    def test_or_subtype_query_is_one_boolean_qualification_and_keeps_its_controller(self):
        condition=self.condition('an Insect, Leech, Slug, or Worm you control')
        parts=[v for v in condition['all'] if v.get('field')==ATTACK_FACT_PREFIX+'subtypes']
        self.assertEqual(1,len(parts));self.assertEqual('contains_any',parts[0]['op'])
        self.assertEqual({'insect','leech','slug','worm'},set(parts[0]['value']))
        self.assertEqual(1,len([v for v in condition['all'] if v.get('field')=='controller']))

    def test_fresh_owner_proof_omission_is_killed_after_positive_closed_scope(self):
        self.assertTrue(qualified_attack_defender_condition_is_closed('creature.attacks',self.condition()))
        with patch('quorune.rules.qualified_attacks.qualified_attack_runtime_dependencies',return_value=()):
            with self.assertRaises(AssertionError):self.assertTrue(qualified_attack_defender_condition_is_closed('creature.attacks',self.condition()))

    def test_qualified_defender_runtime_minimum_is_independent_of_optional_coverage_annotation(self):
        from types import SimpleNamespace
        from quorune.rules.combat_defending_players import combat_defending_player_runtime_dependencies
        values=dict(event='creature.attacks',event_condition=self.condition(),active_zone='battlefield',target_schema=None,
            coverage=(),effects=({'op':'lose_life','player':'$context.defending_player','amount':1},{'op':'life','player':'$controller','delta':1}))
        self.assertEqual(('life.change.effect','resolution.event_player.fixed_result'),combat_defending_player_runtime_dependencies(SimpleNamespace(**values)))
        for change in ({'active_zone':'hand'},{'target_schema':{}},
                {'effects':({'op':'lose_life','player':'$context.defending_player','amount':True},)}):
            with self.subTest(change=change),self.assertRaises(ValueError):combat_defending_player_runtime_dependencies(SimpleNamespace(**{**values,**change}))

    def test_qualified_result_minimum_does_not_take_over_other_attack_result_owners(self):
        from types import SimpleNamespace
        from quorune.rules.combat_defending_players import combat_defending_player_runtime_dependencies
        values=dict(event='creature.attacks',event_condition=self.condition(),active_zone='battlefield',target_schema=None,
            coverage=(),effects=({'op':'create_token','controller':'$controller','name':'Insect Token','quantity':1},))
        self.assertEqual((),combat_defending_player_runtime_dependencies(SimpleNamespace(**values)))
        values.update(event_condition={'field':'card','op':'eq','value':'$source.ref'},
            effects=({'op':'lose_life','player':'$context.defending_player','amount':1},))
        self.assertEqual((),combat_defending_player_runtime_dependencies(SimpleNamespace(**values)))




from dataclasses import replace
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class QualifiedAttackDefenderCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def compile(self,text):
        return compile_oracle_card(query_record(text),capability_registry=self.registry,capability_profile='commander_review')

    def test_full_single_qualified_result_preserves_its_query_and_each_owned_primitive(self):
        for subject in ('a Sliver you control','an Insect, Leech, Slug, or Worm you control','an equipped creature you control'):
            for body,ops in (('defending player loses 1 life.',['lose_life']),
                    ('defending player loses 1 life and you gain 1 life.',['lose_life','life']),
                    ('defending player draws a card.',['draw'])):
                with self.subTest(subject=subject,body=body):
                    ir=self.compile('Whenever '+subject+' attacks, '+body);self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=ir.faces[0].nodes[0];self.assertEqual('creature.attacks',node.event)
                    self.assertEqual(ops,[e['op'] for e in node.effects]);self.assertIsNone(node.target_schema)
                    self.assertEqual('$context.defending_player',node.effects[0]['player'])
                    self.assertIn('trigger.event.qualified_attack_singular',node.capability_dependencies)
                    self.assertIn('resolution.event_player.fixed_result',node.capability_dependencies)

    def test_ambiguous_batches_wrong_events_and_material_riders_remain_unrepresented(self):
        for text in ('Whenever one or more Slivers you control attack, defending player loses 1 life.',
                'Whenever a Sliver you control enters, defending player loses 1 life.',
                'Whenever a Sliver you control attacks, defending player loses X life.',
                'Whenever a Sliver you control attacks, defending player loses 1 life. Rewrite history.'):
            self.assertNotEqual('exact',self.compile(text).status,text)

    def test_closed_qualified_proof_cannot_be_skipped_and_existing_source_defender_stays_exact(self):
        from quorune.compiler.event_player_results import _occurrence_player
        from quorune.qualified_attack_model import QUALIFIED_ATTACK_VARIANT
        self.assertIsNone(_occurrence_player('creature.attacks',None,QUALIFIED_ATTACK_VARIANT,'defending player'))
        self.assertEqual('$context.defending_player',_occurrence_player('creature.attacks',None,'source_attacks','defending player'))
        self.assertIsNone(_occurrence_player('creature.blocks',None,QUALIFIED_ATTACK_VARIANT,'defending player'))

    def test_fresh_recipient_bridge_omission_is_killed_after_positive_full_clause(self):
        text='Whenever a Sliver you control attacks, defending player loses 1 life.'
        self.assertEqual('exact',self.compile(text).status)
        with patch('quorune.compiler.event_player_results._qualified_attack_defender',return_value=False):
            with self.assertRaises(AssertionError):self.assertEqual('exact',self.compile(text).status)
            self.assertEqual('exact',self.compile('Whenever this creature attacks, defending player loses 1 life.').status)

    def test_material_nontoken_creature_sacrifice_sibling_keeps_actor_type_and_token_facts(self):
        from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
        for head in ('a player sacrifices a','an opponent sacrifices a','you sacrifice a','you sacrifice another'):
            text='Whenever '+head+' nontoken creature, create a 1/1 black Insect creature token with flying.'
            binding=fixed_public_action_event_binding_spec(text);self.assertIsNotNone(binding,text)
            conditions=binding.condition['all'];self.assertIn({'field':'types','op':'contains_any','value':['creature']},conditions)
            self.assertIn({'field':'token','op':'eq','value':False},conditions)
            ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual('permanent.sacrificed',ir.faces[0].nodes[0].event)
        for text in ('Whenever a player sacrifices a nontoken creature or artifact, draw a card.',
                'Whenever a player sacrifices a nontoken planeswalker, draw a card.'):
            self.assertNotEqual('exact',self.compile(text).status,text)

    def test_existing_sacrifice_subjects_keep_their_owned_predicates(self):
        from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
        for subject in ('nontoken permanent','noncreature token','artifact or creature','Blood token'):
            text='Whenever a player sacrifices a '+subject+', draw a card.'
            self.assertIsNotNone(fixed_public_action_event_binding_spec(text),text)
            self.assertEqual('exact',self.compile(text).status,text)




from pathlib import Path
import json,tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers

ORIGINALS=('Leeching Sliver','Fumulus, the Infestation')


class QualifiedAttackDefenderNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject=recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/qualified-attack-defender-cards.json'],folder/'qualified-defender.sqlite3')
        cls.db=CardDatabase(folder/'qualified-defender.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified attacker defender results',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_complete_originals_normally_bind_all_printed_siblings_and_keep_true_unsupported_neighbors(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        s,_=self.start(725000)
        for name in (*ORIGINALS,'Silumgar, the Drifting Death'):self.assert_closed(s,self.add(s.engine,name,zone='hand'))
        for name in ('Portal Mage','Nazahn, Revered Bladesmith'):
            card=self.db.lookup(name);semantics=SemanticRegistry()
            register_generated_programs(self.db,semantics,(card,),capability_registry=self.registry,capability_profile='commander_review',
                promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
            p=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'],name)

    def cast_world(self,name,seed,*,mixed=False,response=None,extras=()):
        s,private=self.start(seed);source=self.add(s.engine,name,zone='hand')
        haste=self.add(s.engine,'Fervor');self.assert_closed(s,source);self.assert_closed(s,haste)
        other=self.add(s.engine,'Universal Automaton',ref='qualified-changeling') if mixed else None
        bear=self.add(s.engine,'Grizzly Bears',ref='unqualified-bear') if mixed else None
        foreign=self.add(s.engine,'Sliver Construct',seat='B',ref='foreign-sliver') if mixed else None
        if mixed:
            for card in (other,bear,foreign):self.assert_closed(s,card)
        if response:
            card=self.add(s.engine,response,seat='B',zone='hand',ref='qualified-response');self.assert_closed(s,card)
            s.state.players['B'].mana_pool.update({'U':3,'C':4})
        for name_,seat,zone,ref in extras:self.assert_closed(s,self.add(s.engine,name_,seat=seat,zone=zone,ref=ref))
        self.prepare(s,mana={'C':6,'B':3,'R':1});self.checkpoint(s)
        before=sum(s.state.players['A'].mana_pool.values());self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(before-int(self.db.lookup(name).mana_value),sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        if name=='Fumulus, the Infestation':self.assertTrue({'flying','deathtouch'}<=set(s.engine._combat_keywords(s.state.cards[source.object_id])))
        return s,private,source,other,bear,foreign

    def actual_printed_attack(self,name,seed):
        s,private,source,_,_,_=self.cast_world(name,seed);self.attack(s,[(source,'B')])
        self.assertEqual(1,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
        self.assertEqual((41 if name=='Fumulus, the Infestation' else 40,39,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.finish(s,private)

    def test_actual_printed_leech_cast_and_self_attack_loses_only_defender_life_with_private_exact_replay(self):
        self.actual_printed_attack('Leeching Sliver',725001)

    def test_actual_printed_fumulus_cast_and_self_attack_keep_keywords_and_controller_gain_with_exact_replay(self):
        self.actual_printed_attack('Fumulus, the Infestation',725002)

    def test_actual_changeling_matches_subtype_union_once_and_each_attacker_keeps_its_own_defender(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private,source,other,bear,foreign=self.cast_world(name,725010+index,mixed=True)
                self.attack(s,[(source,'B'),(other,'C'),(bear,'D')])
                self.assertEqual(2,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
                self.assertEqual((42 if name=='Fumulus, the Infestation' else 40,39,39,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_actual_conceded_defender_does_not_block_resolution_or_fumulus_controller_gain(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private,source,_,_,_=self.cast_world(name,725020+index)
                self.attack(s,[(source,'B')]);self.priority(s,'B')
                self.assertTrue(any(a['id']=='concede' for a in s.packet('pilot:B',full=True)['decision']['legal_actions']))
                result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
                self.assertNotIn('B',s.engine.active_seats);self.assertFalse(s.state.game_over)
                self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
                self.assertEqual(41 if name=='Fumulus, the Infestation' else 40,s.state.players['A'].life)
                self.finish(s,private)

    def activate_material_cost(self,s,altar,payment,seat):
        self.priority(s,seat);current=s.state.cards[altar.object_id]
        action=next(a for a in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+current.ref+':'))
        command={'action_id':action['id'],'cost_cards':[s.state.cards[payment.object_id].ref],'pay':'auto'}
        self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def material_world(self,seed,*,self_sac=False,exile=False,extras=()):
        s,private=self.start(seed);source=self.add(s.engine,'Fumulus, the Infestation',zone='hand')
        seat='A' if self_sac else 'B';altar=self.add(s.engine,'Carnage Altar',seat=seat)
        victim=source if self_sac else self.add(s.engine,'Grizzly Bears',seat='B',ref='material-victim')
        alarm=self.add(s.engine,'Raise the Alarm',seat='B',zone='hand') if not self_sac else None
        if exile:self.assert_closed(s,self.add(s.engine,'Samurai of the Pale Curtain',seat='D'))
        for card in (source,altar,victim):self.assert_closed(s,card)
        if alarm is not None:self.assert_closed(s,alarm)
        for name,seat,zone,ref in extras:self.assert_closed(s,self.add(s.engine,name,seat=seat,zone=zone,ref=ref))
        s.state.players['B'].mana_pool.update({'C':8,'W':1});self.prepare(s,mana={'C':6,'B':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual(3,sum(s.state.players['A'].mana_pool.values()))
        return s,private,source,altar,victim,alarm

    def insect_tokens(self,s,seat='A'):
        return [c for c in s.state.cards.values() if not c.is_card_object and c.controller==seat and c.zone=='battlefield' and 'insect' in s.engine._type_parts(s.engine._effective_card_data(c)['type_line'])[1]]

    def test_actual_opponent_nontoken_cost_creates_flying_token_before_draw_but_token_cost_does_not(self):
        s,private,source,altar,victim,alarm=self.material_world(725100)
        self.cast(s,'B',alarm);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if not c.is_card_object and c.controller=='B' and c.zone=='battlefield'];self.assertEqual(2,len(tokens))
        before=len(s.state.players['B'].zones['hand']);self.activate_material_cost(s,altar,victim,'B')
        self.assertEqual(1,self.trigger_count(s,source));self.assertEqual('graveyard',s.state.cards[victim.object_id].zone)
        self.resolve_top(s);created=self.insect_tokens(s);self.assertEqual(1,len(created))
        self.assertEqual(before,len(s.state.players['B'].zones['hand']));self.assertTrue('flying' in s.engine._combat_keywords(created[0]))
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(created[0].object_id,f) for f in ('power','toughness')))
        self.assertIsNone(self.resolve(s));self.assertEqual(before+1,len(s.state.players['B'].zones['hand']))
        self.activate_material_cost(s,altar,tokens[0],'B');self.assertEqual(0,self.trigger_count(s,source))
        self.assertIsNone(self.resolve(s));self.assertEqual(1,len(self.insect_tokens(s)));self.finish(s,private)

    def test_actual_self_sacrifice_looks_back_and_exile_replacement_does_not_erase_sacrifice_trigger(self):
        for index,exile in enumerate((False,True)):
            with self.subTest(exile=exile):
                s,private,source,altar,victim,_=self.material_world(725110+index,self_sac=True,exile=exile)
                self.activate_material_cost(s,altar,source,'A')
                self.assertEqual('exile' if exile else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual(1,self.trigger_count(s,source));self.resolve_top(s)
                self.assertEqual(1,len(self.insect_tokens(s)));self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_actual_source_or_attacker_departure_preserves_the_qualified_occurrence_and_prior_defender(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                extras=(('Island','B','battlefield','response-blue'),)
                s,private,source,attacker,_,_=self.cast_world(name,725120+index,mixed=True,response='Unsummon',extras=extras)
                self.attack(s,[(attacker,'D')]);self.assertEqual(1,self.trigger_count(s,source))
                bounce=next(c for c in s.state.cards.values() if c.ref=='qualified-response')
                removed=source if index==0 else attacker
                self.cast(s,'B',bounce,[removed.ref]);self.resolve_top(s)
                self.assertEqual('hand',s.state.cards[removed.object_id].zone)
                self.assertIsNone(self.resolve(s))
                self.assertEqual((41 if index else 40,40,40,39),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_actual_control_magic_changes_source_controller_but_not_pending_ability_controller_or_defender(self):
        extras=(('Control Magic','B','hand','steal-source'),('Vedalken Orrery','B','battlefield','steal-flash'),
            ('Island','B','battlefield','steal-blue-one'),('Island','B','battlefield','steal-blue-two'),
            ('Plains','B','battlefield','steal-generic-one'),('Plains','B','battlefield','steal-generic-two'))
        s,private,source,_,_,_=self.cast_world('Fumulus, the Infestation',725125,extras=extras)
        self.attack(s,[(source,'B')]);self.assertEqual(1,self.trigger_count(s,source))
        control=next(c for c in s.state.cards.values() if c.ref=='steal-source')
        self.cast(s,'B',control,[source.ref]);self.resolve_top(s)
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.assertEqual('A',s.state.stack[-1].controller);self.assertIsNone(self.resolve(s))
        self.assertEqual((41,39,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_stifle_counters_the_qualified_ability_without_life_loss_or_independent_gain(self):
        s,private,source,_,_,_=self.cast_world('Fumulus, the Infestation',725126,response='Stifle',
            extras=(('Island','B','battlefield','stifle-blue'),))
        self.attack(s,[(source,'B')]);trigger=s.state.stack[-1]
        stifle=next(c for c in s.state.cards.values() if c.ref=='qualified-response')
        self.cast(s,'B',stifle,[trigger.ref]);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.stack);self.assertEqual((40,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.finish(s,private)

    def test_actual_fault_after_both_life_commits_rolls_back_events_and_trigger_then_retries_once(self):
        import quorune.effect_runtime.life_effects as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,private,source,_,_,_=self.cast_world('Fumulus, the Infestation',725127)
        self.attack(s,[(source,'B')]);original=owner.commit_life_change_batch;observed=[];failed=False
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(tuple(s.state.players[p].life for p in 'AB'))
            if len(observed)==2:raise GameRuleError('Injected after both qualified defender life instructions')
            return result
        with patch.object(owner,'commit_life_change_batch',side_effect=fault):
            for _ in range(8):
                before=authoritative_state_hash(s.state);result=s.act(s.pending_principals()[0],{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected after both',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=True;break
        self.assertTrue(failed);self.assertEqual([(40,39),(41,39)],observed)
        self.assertEqual((40,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.assertEqual(1,self.trigger_count(s,source))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((41,39,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_noncreature_sacrifice_does_not_trigger_and_created_insect_then_attacks_as_qualified_creature(self):
        extras=(('Claws of Gix','B','battlefield','permanent-sacrifice'),('Sol Ring','B','battlefield','noncreature-victim'),
            ('Fervor','A','battlefield','created-token-haste'))
        s,private,source,_,victim,_=self.material_world(725130,extras=extras)
        claws=next(c for c in s.state.cards.values() if c.ref=='permanent-sacrifice')
        ring=next(c for c in s.state.cards.values() if c.ref=='noncreature-victim')
        self.activate_material_cost(s,claws,ring,'B');self.assertEqual(0,self.trigger_count(s,source))
        self.assertIsNone(self.resolve(s));self.assertEqual([],self.insect_tokens(s));self.assertEqual(41,s.state.players['B'].life)
        self.activate_material_cost(s,claws,victim,'B');self.assertEqual(1,self.trigger_count(s,source))
        self.resolve_top(s);created=self.insect_tokens(s);self.assertEqual(1,len(created));self.assertEqual(41,s.state.players['B'].life)
        self.assertIsNone(self.resolve(s));self.assertEqual(42,s.state.players['B'].life)
        self.attack(s,[(source,'B'),(created[0],'D')]);self.assertEqual(2,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
        self.assertEqual((42,41,40,39),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_native_recipient_mutant_is_killed_after_fresh_actual_positive_complete_original(self):
        import quorune.semantic_runtime.values as owner
        self.actual_printed_attack('Fumulus, the Infestation',725131)
        original=owner.resolve_semantic_value;observed=[]
        def wrong_player(host,value,item):
            if value=='$context.defending_player':
                observed.append((item.controller,item.context['defending_player']));return item.controller
            return original(host,value,item)
        with patch.object(owner,'resolve_semantic_value',side_effect=wrong_player):
            with self.assertRaises(AssertionError):self.actual_printed_attack('Fumulus, the Infestation',725132)
        self.assertEqual([('A','B')],observed)

    def test_complete_attack_and_material_siblings_require_independent_declared_runtime_owners(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,_=self.start(725133)
        for name in ORIGINALS:
            source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
            attack=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='creature.attacks')
            for cap in ('trigger.event.qualified_attack_singular','resolution.event_player.fixed_result'):
                deps=tuple(d for d in attack.capability_dependencies if d!=cap)
                raw=replace(attack,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(raw,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_genuinely_verified_attack_result_and_sacrifice_owner_withdrawals_block_whole_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        sacrifice='trigger.event.normalized_zone_change'
        for missing in ('trigger.event.qualified_attack_singular','resolution.event_player.fixed_result',sacrifice):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Qualified defender owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name,expected in (('Leeching Sliver',missing==sacrifice),('Fumulus, the Infestation',False),('Grizzly Bears',True),('Sol Ring',True)):
                card=self.db.lookup(name);semantics=SemanticRegistry()
                register_generated_programs(self.db,semantics,(card,),capability_registry=registry,capability_profile='commander_review',
                    promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertEqual(expected,bound['strict_capability_ready'],(missing,name,bound['blockers']))



if __name__=='__main__':unittest.main()

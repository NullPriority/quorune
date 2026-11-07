"""Setting life requests ordinary gain/loss from one resolution snapshot."""
import unittest
from pathlib import Path
import tempfile

from quorune.life_total_setting_model import LifeTotalValueSpec,plan_life_total_setting


class LifeTotalSettingModelTests(unittest.TestCase):
    def test_fixed_total_requests_required_gain_loss_and_no_change(self):
        plan=plan_life_total_setting(LifeTotalValueSpec('fixed',20),tuple('ABCD'),
            {'A':5,'B':40,'C':20,'D':-3},starting_life=40)
        self.assertEqual((15,-20,0,23),tuple(item.delta for item in plan))
        self.assertEqual((20,)*4,tuple(item.requested_total for item in plan))

    def test_highest_lowest_and_starting_totals_read_one_complete_snapshot(self):
        lives={'A':5,'B':40,'C':20,'D':-3}
        expected={'highest':(35,0,20,43),'lowest':(-8,-43,-23,0),'starting':(28,-7,13,36)}
        for kind,deltas in expected.items():
            with self.subTest(kind=kind):
                plan=plan_life_total_setting(LifeTotalValueSpec(kind),tuple('ABCD'),lives,starting_life=33)
                self.assertEqual(deltas,tuple(item.delta for item in plan))
        lives['B']=2
        self.assertEqual(40,plan[1].before,'Subsequent state does not rewrite the captured transition')

    def test_count_values_distinguish_known_zero_from_missing_counts(self):
        for kind in ('creatures','permanents','library'):
            with self.subTest(kind=kind):
                value=LifeTotalValueSpec(kind)
                plan=plan_life_total_setting(value,('A','B'),{'A':3,'B':5},starting_life=40,counts={'A':0,'B':7})
                self.assertEqual((-3,2),tuple(item.delta for item in plan))
                for invalid in (None,{'A':0},{'A':0,'B':True},{'A':0,'B':-1},{'A':0,'B':7,'C':1}):
                    with self.assertRaises(ValueError):
                        plan_life_total_setting(value,('A','B'),{'A':3,'B':5},starting_life=40,counts=invalid)

    def test_closed_value_codec_rejects_booleans_unknown_and_contradictory_fields(self):
        for value in (LifeTotalValueSpec('fixed',0),LifeTotalValueSpec('fixed',20),
                *(LifeTotalValueSpec(kind) for kind in ('starting','highest','lowest','creatures','permanents','library'))):
            self.assertEqual(value,LifeTotalValueSpec.from_dict(value.to_dict()))
        for wire in ({'schema_version':True,'kind':'fixed','total':20},
                {'schema_version':1,'kind':'fixed','total':True},
                {'schema_version':1,'kind':'fixed','total':-1},
                {'schema_version':1,'kind':'starting','total':20},
                {'schema_version':1,'kind':'dynamic'},
                {'schema_version':1,'kind':[]},
                {'schema_version':1,'kind':'highest','extra':1}):
            with self.subTest(wire=wire),self.assertRaises(ValueError):LifeTotalValueSpec.from_dict(wire)

    def test_unknown_or_duplicate_participants_and_malformed_live_facts_reject(self):
        value=LifeTotalValueSpec('fixed',20)
        for players,lives,start in ((('A','A'),{'A':5},40),(('B',),{'A':5},40),
                (('A',),{'A':True},40),(('A',),{'A':5},True),((),{'A':5},40)):
            with self.subTest(players=players,lives=lives,start=start),self.assertRaises(ValueError):
                plan_life_total_setting(value,players,lives,starting_life=start)


class LifeTotalSettingRuntimeTests(unittest.TestCase):
    def host(self):
        from types import SimpleNamespace
        from test_life_effect_runtime import _Host
        host=_Host();host.state.config=SimpleNamespace(starting_life=40)
        return host

    def apply(self,host,value,*,player='A',all_players=False):
        from quorune.effect_runtime.life_total_setting import apply_set_life_total
        effect={'op':'set_life_total','schema_version':1,'value':value.to_dict(),
            **({'scope':'all_active_players'} if all_players else {'player':player})}
        return apply_set_life_total(host,effect,actor='C',operation='set_life_total',reason='Setting fixture')

    def test_gain_replacement_modifies_required_difference_and_preserves_source_events(self):
        from unittest.mock import patch
        from quorune.replacement import ReplacementEffect,ReplacementClass,MultiplyAmount
        from quorune.effect_runtime import life_effects
        host=self.host();host.state.players['A'].life=5
        effect=ReplacementEffect('double','DOUBLER','life.change',ReplacementClass.OTHER,
            {'direction':'gain','affected_player':'A'},(MultiplyAmount('amount',2),))
        with patch.object(life_effects,'collect_life_change_replacement_effects',return_value=(effect,)):
            self.assertEqual({'A':35},self.apply(host,LifeTotalValueSpec('fixed',20)))
        self.assertEqual([('life.gained',{'player':'A','amount':30})],[(event,ctx) for event,ctx,_ in host.semantic_events])

    def test_prohibited_gain_loss_and_unchanged_total_keep_distinct_event_meanings(self):
        from unittest.mock import patch
        from quorune.life_gain_permission import LifeGainPermissionSnapshot
        from quorune.effect_runtime import life_effects
        host=self.host();host.state.players['A'].life=5
        with patch.object(life_effects,'collect_life_gain_permissions',return_value=LifeGainPermissionSnapshot(('A',))):
            self.assertEqual({'A':5},self.apply(host,LifeTotalValueSpec('fixed',20)))
            self.assertEqual({'A':1},self.apply(host,LifeTotalValueSpec('fixed',1)))
            self.assertEqual({'A':1},self.apply(host,LifeTotalValueSpec('fixed',1)))
        self.assertEqual([],host.semantic_events)
        self.assertEqual([0,-4,0],[row[0][3]['deltas']['A'] for row in host.logs])

    def test_mixed_simultaneous_changes_use_the_original_highest_before_replacements(self):
        from unittest.mock import patch
        from quorune.replacement import ReplacementEffect,ReplacementClass,MultiplyAmount
        from quorune.effect_runtime import life_effects
        host=self.host()
        for seat,life in zip('ABC',(5,20,40)):host.state.players[seat].life=life
        effect=ReplacementEffect('double','DOUBLER','life.change',ReplacementClass.OTHER,
            {'direction':'gain','affected_player':'A'},(MultiplyAmount('amount',2),))
        with patch.object(life_effects,'collect_life_change_replacement_effects',return_value=(effect,)):
            self.assertEqual({'A':75,'B':40,'C':40},self.apply(host,LifeTotalValueSpec('highest'),all_players=True))
        self.assertEqual({'A':40,'B':40,'C':40},host.logs[-1][0][3]['requested_totals'])

    def test_resolution_count_reads_current_creature_control_and_library_size(self):
        from types import SimpleNamespace
        host=self.host()
        host.state.cards={'one':SimpleNamespace(zone='battlefield',controller='A',kind='creature',phased_out=False),
            'two':SimpleNamespace(zone='battlefield',controller='A',kind='artifact',phased_out=False),
            'phased':SimpleNamespace(zone='battlefield',controller='A',kind='creature',phased_out=True)}
        host.state.players['A'].zones={'battlefield':['one','two','phased'],'library':['L1','L2','L3']}
        host._effective_card_data=lambda card:{'type_line':card.kind}
        host._type_parts=lambda text:({text},set(),set())
        self.assertEqual({'A':1},self.apply(host,LifeTotalValueSpec('creatures')))
        self.assertEqual({'A':2},self.apply(host,LifeTotalValueSpec('permanents')))
        self.assertEqual({'A':3},self.apply(host,LifeTotalValueSpec('library')))
        host.state.players['A'].zones['library'].append('L4')
        self.assertEqual(3,host.state.players['A'].life)
        self.assertEqual({'A':4},self.apply(host,LifeTotalValueSpec('library')))

    def test_effect_schema_rejects_unknown_principal_and_contradictory_scope_before_changes(self):
        from quorune.effect_runtime.life_total_setting import apply_set_life_total
        from quorune.errors import GameRuleError
        host=self.host()
        base={'op':'set_life_total','schema_version':1,'player':'A','value':LifeTotalValueSpec('fixed',20).to_dict()}
        for changes in ({'player':'unknown'},{'scope':'all_active_players'},{'extra':True},{'schema_version':True},
                {'_runtime_source':{}},
                {'source':'FORGED','_runtime_source':{'stack_ref':'S','object_id':'one','logical_object_id':'one@0','card_ref':'REAL'}}):
            with self.subTest(changes=changes),self.assertRaises(GameRuleError):
                apply_set_life_total(host,{**base,**changes},actor='C',operation='set_life_total',reason='Invalid')
            self.assertEqual((40,40,40),tuple(host.state.players[p].life for p in 'ABC'))


class LifeTotalSettingCompilerTests(unittest.TestCase):
    def test_capability_withdrawal_and_setting_producer_mutations_fail_closed(self):
        from copy import deepcopy
        from dataclasses import replace
        import json
        from common import ROOT
        from unittest.mock import patch
        from test_fixed_player_life_effects import fixture_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler import life_total_setting_templates
        text="Target player's life total becomes 20."
        registry=load_default_capability_registry();record=fixture_card(text)
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review').status)
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap_id in ('life.change.set_total','life.change.effect','target.revalidate_resolution'):
            altered=deepcopy(value);cap=next(row for row in altered['capabilities'] if row['id']==cap_id)
            cap.update(status='blocked',blockers=['Owner deliberately withdrawn by test'])
            test_registry=CapabilityRegistry(altered);test_registry.mark_evidence_verified('0'*64)
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=test_registry,capability_profile='commander_review').status)
        with patch.object(life_total_setting_templates,'life_total_setting_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review').status)
        activated=replace(fixture_card("−1: Target player's life total becomes 20.",type_line='Planeswalker'),loyalty='3')
        self.assertEqual('exact',compile_oracle_card(activated,capability_registry=registry,capability_profile='commander_review').status)
        from quorune.compiler import activated_mana_nodes
        without_setting=activated_mana_nodes._CAPABILITY_SHAPED_EFFECT_OPERATIONS-{'set_life_total'}
        with patch.object(activated_mana_nodes,'_CAPABILITY_SHAPED_EFFECT_OPERATIONS',without_setting):
            self.assertNotEqual('exact',compile_oracle_card(activated,capability_registry=registry,capability_profile='commander_review').status)

    def test_treating_requested_total_as_a_delta_is_killed_by_the_independent_result(self):
        from unittest.mock import patch
        from quorune.effect_runtime import life_total_setting
        from quorune.life_total_setting_model import LifeTotalTransition
        def wrong_plan(value,players,life_totals,**facts):
            return tuple(LifeTotalTransition(player,0,value.total) for player in players)
        result=unittest.TestResult()
        with patch.object(life_total_setting,'plan_life_total_setting',side_effect=wrong_plan):
            LifeTotalSettingRuntimeTests('test_gain_replacement_modifies_required_difference_and_preserves_source_events').run(result)
        self.assertEqual(1,len(result.failures));self.assertEqual([],result.errors)

    def test_complete_clauses_keep_requested_values_and_exact_target_domains(self):
        from quorune.compiler.life_total_setting_templates import life_total_setting_template
        from quorune.rules.life_total_setting_shapes import life_total_setting_capabilities
        for text,kind,recipient in (
                ("Target player's life total becomes 20.",'fixed','target'),
                ("Target opponent's life total becomes 10.",'fixed','target'),
                ('Your life total becomes equal to your starting life total.','starting','self'),
                ('Your life total becomes half your starting life total, rounded up.','starting_half_rounded_up','self'),
                ("Each player's life total becomes the highest life total among all players.",'highest','all'),
                ("Each player's life total becomes the lowest life total among all players.",'lowest','all'),
                ("Each player's life total becomes the number of creatures they control.",'creatures','all'),
                ('Count the number of cards in your library. Your life total becomes that number.','library','self'),
                ('Count the number of permanents you control. Your life total becomes that number.','permanents','self')):
            with self.subTest(text=text):
                result=life_total_setting_template(text);self.assertIsNotNone(result)
                self.assertEqual(kind,result.effect['value']['kind'])
                self.assertEqual('all_active_players' if recipient=='all' else '$target.0' if recipient=='target' else '$controller',
                    result.effect.get('scope',result.effect.get('player')))
                self.assertIn('life.change.set_total',life_total_setting_capabilities(effects=(result.effect,),
                    target_schema=result.target_schema,mechanic_ids=result.mechanic_ids))
                malformed={**result.effect,'value':{**result.effect['value'],'schema_version':True}}
                self.assertEqual((),life_total_setting_capabilities(effects=(malformed,),target_schema=result.target_schema,mechanic_ids=result.mechanic_ids))
                if recipient=='target':
                    self.assertEqual((),life_total_setting_capabilities(effects=(result.effect,),target_schema=None,mechanic_ids=result.mechanic_ids))

    def test_exchange_fraction_ambiguous_antecedents_and_uncovered_riders_stay_unrepresented(self):
        from quorune.compiler.life_total_setting_templates import life_total_setting_template
        for text in ('Exchange your life total with target player.',
                'Your life total becomes half your starting life total.',
                'It becomes equal to your starting life total.',
                'Your life total becomes -1.','Your life total becomes 20. Then you win the game.',
                'Each opponent\'s life total becomes the number of creatures they control.'):
            with self.subTest(text=text):self.assertIsNone(life_total_setting_template(text))


class LifeTotalSettingNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'life-total-setting.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/damage-result-cards.json',
            ROOT/'tests/fixtures/life-total-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Life-total setting',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from copy import deepcopy
        from common import keep_all
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        session=CommanderSession.create(self.db,{seat:deepcopy(self.deck) for seat in 'ABCD'},
            first_player='C',seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session)
        return session

    def assert_closed(self,session,card):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def start(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C';engine.state.started=True;engine.state.phase='precombat_main';engine.state.step='main'
        from quorune.turn_step_owner import TURN_STEPS
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'))
        engine.state.players['C'].mana_pool.update({'C':30,'W':10})
        H.add(self,engine,'Generic Bound Growth',seat='D',zone='hand',ref='SETTING_PRIVATE_D_HAND')
        engine._grant_priority('C');engine.pump();H.checkpoint(self,session)
        self.assert_private(session)

    def test_actual_biorhythm_commits_all_creature_counts_before_zero_life_departure(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(963103);engine=session.engine
        for seat,count in zip('ABCD',(1,0,2,3)):
            for i in range(count):H.add(self,engine,'Ornithopter',seat=seat,ref=f'SETTING_COUNT_{seat}_{i}')
        spell=H.add(self,engine,'Biorhythm',seat='C',zone='hand',ref='SETTING_BIORHYTHM')
        self.assert_closed(session,spell);engine.state.players['C'].mana_pool.update({'G':2})
        self.start(session);self.cast(session,spell);self.assertIsNone(H.resolve(self,session))
        self.assertEqual((1,0,2,3),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.assertFalse(engine.state.players['B'].in_game);self.assertEqual(['C','D','A'],list(engine.active_seats))
        self.assertEqual(['C','D','A'],engine.apnap_order())
        self.assertEqual('C',engine.state.active_player)
        self.assertFalse(engine.state.game_over)
        H.replay(self,session,load=True)
        self.assert_private(session)

    def test_actual_hymn_counts_library_after_a_response_and_does_not_keep_resetting_life(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(963104);engine=session.engine
        hymn=H.add(self,engine,'Invincible Hymn',seat='C',zone='hand',ref='SETTING_HYMN')
        draws=[H.add(self,engine,'Reach Through Mists',seat='C',zone='hand',ref='SETTING_DRAW_'+str(i)) for i in range(2)]
        for card in (hymn,*draws):self.assert_closed(session,card)
        engine.state.players['C'].life=10;engine.state.players['C'].mana_pool.update({'U':2})
        count=len(engine.state.players['C'].zones['library']);self.start(session)
        original_hand=set(engine.state.players['C'].zones['hand'])
        self.cast(session,hymn);self.cast(session,draws[0]);self.assertIsNone(H.resolve(self,session))
        self.assertEqual(count-1,engine.state.players['C'].life)
        self.cast(session,draws[1]);self.assertIsNone(H.resolve(self,session))
        self.assertEqual(count-2,len(engine.state.players['C'].zones['library']))
        self.assertEqual(count-1,engine.state.players['C'].life)
        drawn=set(engine.state.players['C'].zones['hand'])-original_hand;self.assertEqual(2,len(drawn))
        for oid in drawn:
            ref=engine.state.cards[oid].ref
            for seat in 'ABD':self.assertNotIn(ref,str(session.packet('pilot:'+seat,full=True)))
        H.replay(self,session,load=True)
        self.assert_private(session)

    def test_actual_form_of_the_dragon_sets_life_on_own_and_another_players_end_step(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from test_ability_label_composition import AbilityLabelCompositionTests as Advance
        session=self.session(963105);engine=session.engine
        form=H.add(self,engine,'Form of the Dragon',seat='C',zone='hand',ref='SETTING_FORM')
        self.assert_closed(session,form);engine.state.players['C'].mana_pool.update({'R':3})
        engine.state.players['C'].life=12;self.start(session);self.cast(session,form)
        self.assertIsNone(H.resolve(self,session))
        for seat in ('C','D'):
            Advance.advance(self,session,lambda s,seat=seat:s.state.active_player==seat
                and s.state.step in {'end','end_step'} and bool(s.state.stack))
            self.assertEqual('C',engine.state.stack[-1].controller)
            self.assertEqual(form.object_id,engine.state.stack[-1].source_object_id)
            self.assertIsNone(H.resolve(self,session));self.assertEqual(5,engine.state.players['C'].life)
        H.replay(self,session,load=True)
        self.assert_private(session)

    def assert_private(self,session):
        for seat in 'ABC':self.assertNotIn('SETTING_PRIVATE_D_HAND',str(session.packet('pilot:'+seat,full=True)))
        self.assertIn('SETTING_PRIVATE_D_HAND',str(session.packet('pilot:D',full=True)))

    def cast(self,session,spell,targets=()):
        from quorune.record import authoritative_state_hash
        actions=session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in actions if row.get('card')==spell.ref)
        command={'action_id':offer['id'],'targets':list(targets),'pay':'auto'}
        before=authoritative_state_hash(session.engine.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.engine.state))
        accepted=session.act('pilot:C',command);self.assertTrue(accepted.ok,accepted.summary)

    def test_actual_blessed_wind_resolves_doubled_gain_then_loss_then_no_change(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(963100);engine=session.engine
        doubler=H.add(self,engine,'Boon Reflection',ref='SETTING_DOUBLER')
        watcher=H.add(self,engine,"Ajani's Pridemate",ref='SETTING_GAIN_WATCHER')
        spells=[H.add(self,engine,'Blessed Wind',seat='C',zone='hand',ref='SETTING_WIND_'+str(i)) for i in range(3)]
        for card in (doubler,watcher,*spells):self.assert_closed(session,card)
        engine.state.players['A'].life=5;self.start(session)
        for spell,expected,counters in zip(spells,(35,20,20),(1,1,1)):
            self.cast(session,spell,('A',));self.assertIsNone(H.resolve(self,session))
            self.assertEqual(expected,engine.state.players['A'].life)
            self.assertEqual(counters,engine.state.cards[watcher.object_id].counters.get('+1/+1',0))
        H.replay(self,session,load=True)
        self.assert_private(session)

    def test_actual_gain_prohibition_blocks_setting_upward_and_allows_setting_downward(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(963101);engine=session.engine
        prohibition=H.add(self,engine,'Giant Cindermaw',ref='SETTING_GAIN_PROHIBITION')
        spells=[H.add(self,engine,'Blessed Wind',seat='C',zone='hand',ref='PROHIBITED_WIND_'+str(i)) for i in range(2)]
        for card in (prohibition,*spells):self.assert_closed(session,card)
        engine.state.players['A'].life=5;self.start(session)
        for spell,target,expected in zip(spells,('A','B'),(5,20)):
            self.cast(session,spell,(target,));self.assertIsNone(H.resolve(self,session))
            self.assertEqual(expected,engine.state.players[target].life)
        H.replay(self,session,load=True)
        self.assert_private(session)

    def test_actual_arbiter_freezes_all_totals_and_waits_for_multiplayer_replacements(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=self.session(963102);engine=session.engine
        sources=[H.add(self,engine,'Boon Reflection',seat=seat,ref=f'SETTING_{seat}_DOUBLER_{i}')
            for seat in ('A','B') for i in range(2)]
        arbiter=H.add(self,engine,'Arbiter of Knollridge',seat='C',zone='hand',ref='SETTING_ARBITER')
        for card in (*sources,arbiter):self.assert_closed(session,card)
        for seat,total in zip('ABCD',(5,10,20,40)):engine.state.players[seat].life=total
        self.start(session);self.cast(session,arbiter)
        for expected in ('A','B'):
            decision=H.resolve(self,session);self.assertEqual('replacement.order',decision.kind)
            self.assertEqual(['pilot:'+expected],list(session.pending_principals()))
            self.assertEqual((5,10,20,40),tuple(engine.state.players[p].life for p in 'ABCD'))
            session=H.replay(self,session,load=True);engine=session.engine
            option=session.packet('pilot:'+expected,full=True)['decision']['ctx']['options'][0]['id']
            before=authoritative_state_hash(engine.state)
            self.assertFalse(session.act('pilot:D',{'action_id':'choose','replacement':option}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
            self.assert_private(session)
            if expected=='B':
                from dataclasses import replace
                from unittest.mock import patch
                from quorune import life_change
                from quorune.errors import GameRuleError
                original=life_change.apply_life_changes;partial=[]
                def fail_after_first_player(host,plan):
                    original(host,replace(plan,transitions=plan.transitions[:1]))
                    partial.append(tuple(host.state.players[p].life for p in 'ABCD'))
                    raise GameRuleError('Injected setting fault after one life transition')
                with patch.object(life_change,'apply_life_changes',fail_after_first_player):
                    rejected=session.act('pilot:B',{'action_id':'choose','replacement':option})
                self.assertFalse(rejected.ok);self.assertEqual(1,len(partial));self.assertNotEqual((5,10,20,40),partial[0])
                self.assertIn('Injected setting fault',rejected.summary)
                self.assertEqual(before,authoritative_state_hash(engine.state))
            accepted=session.act('pilot:'+expected,{'action_id':'choose','replacement':option})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual((145,130,40,40),tuple(engine.state.players[p].life for p in 'ABCD'))
        H.replay(self,session,load=True)
        self.assert_private(session)

    def test_actual_vraska_loyalty_pays_before_source_departure_and_sets_target_life(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=self.session(963106);engine=session.engine
        walker=H.add(self,engine,'Vraska, Relic Seeker',seat='C',ref='SETTING_VRASKA')
        self.assert_closed(session,walker);walker.counters['loyalty']=10;engine.state.players['A'].life=5
        self.start(session)
        ability=next(a for a in engine._activated_abilities(walker) if a.loyalty_delta==-10)
        offered=next(row for row in session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==walker.ref and row.get('ability')==ability.ability_id)
        command={'action_id':offered['id'],'targets':['A']}
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:C',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[walker.object_id].zone)
        self.assertEqual(5,engine.state.players['A'].life,'Life changes when the ability resolves, after its loyalty cost')
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session));self.assertEqual(1,engine.state.players['A'].life)
        H.replay(self,session,load=True);self.assert_private(session)

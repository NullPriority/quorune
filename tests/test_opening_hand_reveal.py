from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.opening_hand_model import OpeningHandFrame,OpeningHandSubject
from quorune.opening_reveal_model import OpeningRevealSpec,OPENING_REVEAL_CAPABILITY
from quorune.errors import GameRuleError
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.rules.actual_life_transfer import ACTUAL_LIFE_TRANSFER_CAPABILITY
from quorune.semantic_choices.actual_life_transfer import ActualLifeTransferHandler
from quorune.rules.capabilities import CapabilityRegistry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.action_permissions import OpeningHandRevealPermissionHandler
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_opening_hand_battlefield as battlefield


TEXT='You may reveal this card from your opening hand. If you do, scry 3 at the beginning of your first upkeep.'


class OpeningRevealCompilerTests(unittest.TestCase):
    def test_actual_life_transfer_binds_resolved_loss_and_keeps_fixed_gain_drain_distinct(self):
        context=SimpleNamespace(actor='A',query=SimpleNamespace(active_seats=tuple('ABCD')),
            source_ref='SOURCE',stack_label='Actual life transfer')
        effect={'op':'life_loss_then_gain_actual','schema_version':1,'player':'A','amount':3}
        prep=ActualLifeTransferHandler().prepare(effect,context)
        self.assertEqual(tuple('BCD'),prep.preparation_intents[0].players)
        self.assertEqual(7,prep.auto_continue.resolved_effects({0:7})[0]['amount'])
        from quorune.oracle_ir import _effect_template
        self.assertEqual('drain_each_opponent',_effect_template('Each opponent loses 3 life and you gain 3 life.',card_name='Fixture')[1][0]['op'])
        for missing in (ACTUAL_LIFE_TRANSFER_CAPABILITY,'life.change.effect'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==missing).update(status='blocked',blockers=['Actual life transfer dependency omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record('Each opponent loses 3 life, then you gain life equal to the life lost this way.','Sorcery'),capability_registry=CapabilityRegistry(raw)).status)

    def test_actual_life_transfer_and_top_exile_results_have_reusable_closed_templates(self):
        for text in ('Each opponent loses 3 life, then you gain life equal to the life lost this way.',
                'Look at the top four cards of your library. You may put one of those cards back on top of your library. Exile the rest.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_fixed_existing_results_have_complete_source_and_scoped_delayed_specs(self):
        for text,timing in ((TEXT,'your_first_upkeep'),
                ('You may reveal this card from your opening hand. If you do, at the beginning of your first main phase of the game, add {G}.','your_first_main'),
                ('You may reveal this card from your opening hand. If you do, at the beginning of the first upkeep, create a 1/1 red Phyrexian Goblin creature token with haste.','first_upkeep')):
            with self.subTest(timing=timing):
                ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes
                self.assertEqual((text,0,len(text),'hand'),(node.text,node.span.start,node.span.end,node.active_zone))
                self.assertIn(OPENING_REVEAL_CAPABILITY,node.capability_dependencies)
                descriptor,=node.handlers
                spec=OpeningHandRevealPermissionHandler().validate(descriptor)
                self.assertEqual(timing,spec.timing)
                self.assertEqual(spec.to_dict(),OpeningRevealSpec.from_dict(spec.to_dict()).to_dict())

    def test_result_and_permission_descriptors_reject_unrepresented_or_malformed_inputs(self):
        for text in (TEXT.replace('scry 3','take an undefined phase'),TEXT.replace('opening hand','library'),
                TEXT.replace('your first upkeep','your next turn'),TEXT.replace('You may reveal','You may pay {2} to reveal')):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                capability_registry=load_default_capability_registry()).status)
        ir=compile_oracle_card(record(TEXT,'Creature'),capability_registry=load_default_capability_registry())
        descriptor,=ir.faces[0].nodes[0].handlers
        for field,value in (('schema_version',True),('requires_complete_card_program',False),('unknown',1)):
            with self.subTest(field=field),self.assertRaises(SemanticNodeError):OpeningHandRevealPermissionHandler().validate({**descriptor,field:value})
        value=deepcopy(descriptor['scheduled_result']);value['effects'][0]['count']=True
        with self.assertRaises(ValueError):OpeningRevealSpec.from_dict(value)

    def test_frame_one_bytes_remain_unchanged_and_frame_two_closes_action_identity(self):
        legacy={'schema_version':1,'seats':['A','B'],'index':0,'subjects':[
            {'object_id':'one','logical_object_id':'one:hand','ref':'ONE','player':'A'}]}
        self.assertEqual(legacy,OpeningHandFrame.from_dict(legacy).to_dict())
        subject=OpeningHandSubject('one','one:hand','ONE','A','reveal','program:reveal')
        current=OpeningHandFrame(('A','B'),(subject,),version=2)
        self.assertEqual(current.to_dict(),OpeningHandFrame.from_dict(current.to_dict()).to_dict())
        with self.assertRaises(GameRuleError):replace(current,version=1)

    def test_reveal_and_result_dependency_omissions_and_closed_result_mutants_are_killed(self):
        registry=load_default_capability_registry()
        with patch('quorune.compiler.runtime_templates.opening_hand_reveal_permission_handler',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),capability_registry=registry).status)
        for missing in (OPENING_REVEAL_CAPABILITY,'library.scry.fixed_controller'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==missing).update(status='blocked',blockers=['Opening reveal dependency omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),capability_registry=CapabilityRegistry(raw)).status)
        context=SimpleNamespace(actor='A',query=SimpleNamespace(active_seats=tuple('ABCD')),source_ref='SOURCE',stack_label='Actual loss')
        effect={'op':'life_loss_then_gain_actual','schema_version':1,'player':'A','amount':3}
        for change in ({'amount':True},{'schema_version':True},{'player':'B'},{'amount':-1},{'stage':'unknown'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):ActualLifeTransferHandler().prepare({**effect,**change},context)
        prep=ActualLifeTransferHandler().prepare(effect,context)
        self.assertEqual(6,prep.auto_continue.resolved_effects({0:6})[0]['amount'])
        for result in (True,-1,None):
            with self.assertRaises(ValueError):prep.auto_continue.resolved_effects({0:result})
        gain=ActualLifeTransferHandler().prepare(prep.auto_continue.resolved_effects({0:6})[0],context)
        self.assertEqual(6,gain.preparation_intents[0].amount)

    def test_top_exile_descriptor_and_intent_codec_preserve_the_legacy_hand_shape(self):
        from quorune.compiler.fixed_library_selection_templates import fixed_library_selection_effect_template
        from quorune.rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
        from quorune.rules.library_selection import LibrarySelectionArrangement,LibrarySelectionObjectIdentity
        from quorune.semantic_runtime.intents import LibrarySelectionIntent
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity
        template=fixed_library_selection_effect_template('Look at the top four cards of your library. You may put one of those cards back on top of your library. Exile the rest.')
        _,effects,target,mechanics=template.compiled();effect=effects[0]
        self.assertEqual(('library.select.fixed_controller',),fixed_library_selection_node_capabilities(effects=effects,target_schema=target,mechanic_ids=mechanics))
        for change in ({'schema_version':True},{'maximum':2},{'minimum':True},{'remainder_order':'random'},
                {'selected_destination':'hand'},{'remainder_destination':'graveyard'},{'public_reveal':True},{'unknown':1}):
            with self.subTest(change=change):self.assertEqual((),fixed_library_selection_node_capabilities(effects=({**effect,**change},),target_schema=target,mechanic_ids=mechanics))
        identity=LibrarySelectionObjectIdentity('one','one:library','ONE')
        for destination,remainder in (('hand','library_bottom'),('library_top','exile')):
            arrangement=LibrarySelectionArrangement((identity,),('ONE',),(),remainder,'chosen',destination)
            intent=LibrarySelectionIntent('A','A',arrangement,'Codec control','STACK',False,False)
            kind,raw=semantic_intent_identity(intent)
            self.assertEqual(destination=='library_top','selected_destination' in raw['arrangement'])
            self.assertEqual(raw,validate_semantic_intent_identity(kind,raw))


class OpeningRevealRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=battlefield.OpeningHandBattlefieldRuntimeTests.session
    add=battlefield.OpeningHandBattlefieldRuntimeTests.add
    keep=battlefield.OpeningHandBattlefieldRuntimeTests.keep
    card=battlefield.OpeningHandBattlefieldRuntimeTests.card
    checkpoint=battlefield.OpeningHandBattlefieldRuntimeTests.checkpoint
    replay=battlefield.OpeningHandBattlefieldRuntimeTests.replay
    reject=battlefield.OpeningHandBattlefieldRuntimeTests.reject
    resolve=battlefield.OpeningHandBattlefieldRuntimeTests.resolve

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'opening-reveal.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/opening-hand-reveal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def action(self,session,label,actor='A'):
        decision=session.packet('pilot:'+actor,full=True)['decision']
        self.assertIsNotNone(decision,'The actor must receive its native opening decision')
        self.assertEqual('opening.hand',session.state.pending_decision.kind)
        options=decision['ctx']['options']
        option=next(row for row in options if row['label']==label)
        result=session.act('pilot:'+actor,{'action_id':'choose','opening_action':option['id']})
        self.assertTrue(result.ok,result.summary)
        return option['id']

    def advance_to(self,session,predicate,limit=160):
        for _ in range(limit):
            if predicate(session.state):return
            decision=session.state.pending_decision
            self.assertIsNotNone(decision)
            actor=decision.actors[0]
            if decision.kind=='priority':payload={'action_id':'pass'}
            elif decision.kind=='cleanup.discard':
                packet=session.packet('pilot:'+actor,full=True)['decision']
                count=packet['ctx']['count']
                payload={'action_id':'discard','cards':[r['id'] for r in packet['ctx']['hand'][:count]]}
            elif decision.kind=='combat.attackers':payload={'action_id':'attack','attackers':{}}
            else:self.fail('Unexpected native transition '+decision.kind)
            result=session.act('pilot:'+actor,payload);self.assertTrue(result.ok,result.summary)
        self.fail('Scoped next-step witness exceeded its declared command bound')

    def fund_priority(self,session,actor,mana):
        e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_passes=[]
        e.state.players[actor].mana_pool.update(mana);e._grant_priority(actor);e.pump();self.checkpoint(session)

    def test_actual_sphinx_reveal_stays_in_hand_until_start_and_scry_is_private_loaded_and_replayed(self):
        session=self.session(103630,{'A':['Sphinx of Foresight'],'B':['Leyline of Sanctity']})
        sphinx=self.card(session,'Sphinx of Foresight')
        # Supply enough hidden library objects for the real scheduled Scry three.
        for i in range(3):
            card=self.add(session.engine,'Generic Bound Plains',zone='library',ref='sphinx-scry-'+str(i))
            card.known_to=[];card.revealed_to=[]
        self.checkpoint(session);self.keep(session)
        self.assertFalse(session.state.started)
        self.action(session,'Reveal Sphinx of Foresight')
        self.assertEqual('hand',session.state.cards[sphinx.object_id].zone)
        self.assertEqual(set('ABCD'),set(session.state.cards[sphinx.object_id].revealed_to))
        self.assertFalse(session.state.started);self.assertFalse(session.state.stack)
        self.assertEqual(1,len(session.state.delayed_triggers))
        session=self.replay(session,load=True)
        self.action(session,'Begin with Leyline of Sanctity',actor='B')
        self.assertTrue(session.state.started)
        self.assertFalse(session.state.cards[sphinx.object_id].revealed_to)
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        refs=packet['legal_actions'][0]['choice_schema']['legal_refs'];self.assertEqual(3,len(refs))
        for viewer in 'BCD':
            other=session.packet('pilot:'+viewer,full=True);self.assertIsNone(other['decision'])
            for ref in refs:self.assertNotIn(ref,json.dumps(other))
        session=self.replay(session,load=True)
        self.reject(session,'choose','B',cards={'top':refs,'bottom':[]})
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':refs[1:],'bottom':refs[:1]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertFalse(any(t.active for t in session.state.delayed_triggers))
        self.replay(session,load=True)

    def test_actual_forge_first_upkeep_uses_revealing_controller_instead_of_starting_player(self):
        session=self.session(103631,{'A':['Chancellor of the Forge']},first='C')
        source=self.card(session,'Chancellor of the Forge')
        self.keep(session);self.action(session,'Reveal Chancellor of the Forge')
        self.assertEqual(('C','upkeep'),(session.state.active_player,session.state.step))
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        self.assertIsNone(self.resolve(session))
        token,=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual('A',token.controller);self.assertIn('haste',session.engine._combat_keywords(token))
        self.assertEqual((1,1),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_actual_dross_transfers_all_opponents_actual_losses_and_gain_replacements_resume_once(self):
        for multipliers in (0,2):
            with self.subTest(multipliers=multipliers):
                session=self.session(103632,{'A':['Chancellor of the Dross']+['Generic Opening Life Multiplier']*multipliers},first='C')
                self.keep(session)
                for _ in range(multipliers):self.action(session,'Begin with Generic Opening Life Multiplier')
                self.action(session,'Reveal Chancellor of the Dross')
                self.assertEqual(('C','upkeep'),(session.state.active_player,session.state.step))
                self.assertEqual('A',session.state.stack[-1].controller)
                decision=self.resolve(session)
                if multipliers:
                    self.assertEqual('replacement.order',decision.kind)
                    self.assertEqual([37,37,37],[session.state.players[p].life for p in 'BCD'])
                    session=self.replay(session,load=True)
                    option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
                    result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}})
                    self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                else:self.assertIsNone(decision)
                self.assertEqual(40+9*(2**multipliers),session.state.players['A'].life)
                self.assertEqual([37,37,37],[session.state.players[p].life for p in 'BCD'])
                self.replay(session,load=True)

    def test_actual_devourer_keeps_up_to_one_on_top_exiles_exact_remainder_and_retains_cast_trigger(self):
        for count,keep in ((4,True),(2,False)):
            with self.subTest(count=count,keep=keep):
                session=self.session(103633,{'A':['Devourer of Destiny','Leyline of Anticipation'],'B':['Leyline of Sanctity']})
                source=self.card(session,'Devourer of Destiny')
                for i in range(count-1):
                    c=self.add(session.engine,'Generic Bound Plains',zone='library',ref='devourer-look-'+str(i));c.known_to=[];c.revealed_to=[]
                self.checkpoint(session);self.keep(session)
                self.action(session,'Begin with Leyline of Anticipation')
                self.action(session,'Reveal Devourer of Destiny')
                self.action(session,'Begin with Leyline of Sanctity',actor='B')
                decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
                packet=session.packet('pilot:A',full=True)['decision']
                refs=packet['legal_actions'][0]['choice_schema']['legal_refs'];self.assertEqual(count,len(refs))
                for viewer in 'BCD':
                    other=session.packet('pilot:'+viewer,full=True);self.assertIsNone(other['decision'])
                    for ref in refs:self.assertNotIn(ref,json.dumps(other))
                self.reject(session,'choose',cards={'top':refs[:2],'exile':refs[2:]})
                self.reject(session,'choose','B',cards={'top':refs[:1],'exile':refs[1:]})
                session=self.replay(session,load=True)
                top=refs[:1] if keep else [];exiled=refs[1:] if keep else refs
                result=session.act('pilot:A',{'action_id':'choose','cards':{'top':top,'exile':exiled}})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual(len(top),len(session.state.players['A'].zones['library']))
                self.assertEqual(set(exiled),{session.state.cards[oid].ref for oid in session.state.players['A'].zones['exile']})
                self.replay(session,load=True)
                sanctity=self.card(session,'Leyline of Sanctity','B',zone='battlefield')
                self.fund_priority(session,'A',{'C':7})
                result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertEqual('semantic.target',session.state.pending_decision.kind)
                result=session.act('pilot:A',{'action_id':'choose','targets':[sanctity.ref]})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual('exile',session.state.cards[sanctity.object_id].zone)
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.replay(session,load=True)

    def test_tangle_waits_for_its_creators_first_main_and_resolves_on_the_stack(self):
        session=self.session(103634,{'A':['Chancellor of the Tangle','Birds of Paradise']},first='C')
        self.keep(session);self.action(session,'Reveal Chancellor of the Tangle')
        self.assertEqual(('C','upkeep'),(session.state.active_player,session.state.step));self.assertFalse(session.state.stack)
        self.advance_to(session,lambda s:s.active_player=='C' and s.phase=='precombat_main')
        self.assertFalse(session.state.stack);self.assertFalse(session.state.players['A'].mana_pool['G'])
        self.advance_to(session,lambda s:s.active_player=='A' and s.phase=='precombat_main')
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        self.assertFalse(session.state.players['A'].mana_pool['G'])
        self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        bird=self.card(session,'Birds of Paradise')
        result=session.act('pilot:A',{'action_id':'cast:'+bird.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[bird.object_id].zone)
        self.assertFalse(any(t.active for t in session.state.delayed_triggers));self.replay(session,load=True)

    def test_revealed_copies_scry_separately_at_their_own_first_upkeep(self):
        session=self.session(103635,{'A':['Sphinx of Foresight','Sphinx of Foresight']},first='C')
        for i in range(7):
            c=self.add(session.engine,'Generic Bound Plains',zone='library',ref='separate-scry-'+str(i));c.known_to=[];c.revealed_to=[]
        self.checkpoint(session);self.keep(session)
        self.action(session,'Reveal Sphinx of Foresight');self.action(session,'Reveal Sphinx of Foresight')
        self.assertFalse(session.state.stack);self.assertEqual('C',session.state.active_player)
        self.advance_to(session,lambda s:s.active_player=='A' and s.step=='upkeep')
        if session.state.pending_decision.kind=='trigger.order':
            packet=session.packet('pilot:A',full=True)['decision']
            refs=[r['id'] for r in packet['ctx']['triggers']]
            result=session.act('pilot:A',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(session.state.stack))
        looked=[]
        for _ in range(2):
            decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
            refs=session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs']
            self.assertEqual(3,len(refs));looked.append(tuple(refs))
            result=session.act('pilot:A',{'action_id':'choose','cards':{'top':[],'bottom':refs}})
            self.assertTrue(result.ok,result.summary)
        self.assertNotEqual(looked[0],looked[1]);self.assertIsNone(self.resolve(session))
        self.assertFalse(any(t.active for t in session.state.delayed_triggers));self.replay(session,load=True)

    def test_reveal_once_and_battlefield_action_are_distinct_and_source_departure_keeps_delay(self):
        session=self.session(103636,{'A':['Generic Opening Dual Action'],'B':['Leyline of Sanctity']})
        for i in range(3):self.add(session.engine,'Generic Bound Plains',zone='library',ref='dual-action-scry-'+str(i))
        self.checkpoint(session);self.keep(session)
        source=self.card(session,'Generic Opening Dual Action')
        old_id=self.action(session,'Reveal Generic Opening Dual Action')
        self.assertFalse(session.state.started);self.assertEqual(1,len(session.state.delayed_triggers))
        self.reject(session,'choose',opening_action=old_id)
        self.action(session,'Begin with Generic Opening Dual Action')
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(set('ABCD'),set(session.state.cards[source.object_id].revealed_to))
        session=self.replay(session,load=True)
        self.action(session,'Begin with Leyline of Sanctity',actor='B')
        self.assertEqual('A',session.state.stack[-1].controller)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        refs=session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs']
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'bottom':[]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_declined_and_unclosed_sources_cannot_reveal_and_stale_finish_rolls_back(self):
        session=self.session(103637,{'A':['Sphinx of Foresight'],'B':['Leyline of Sanctity']})
        self.keep(session);source=self.card(session,'Sphinx of Foresight')
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        reveal=next(r['id'] for r in options if r['label']=='Reveal Sphinx of Foresight')
        self.reject(session,'choose','B',opening_action=reveal)
        original=deepcopy(session.state.pending_decision.continuation)
        session.state.pending_decision.continuation['opening_hand']['subjects'][0]['semantic_key']='not-the-offered-program'
        self.reject(session,'choose',opening_action='finish')
        session.state.pending_decision.continuation=original
        self.action(session,'Finish opening actions');self.assertFalse(session.state.delayed_triggers)
        self.action(session,'Begin with Leyline of Sanctity',actor='B')
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.cards[source.object_id].revealed_to);self.replay(session,load=True)
        session=self.session(103638,{'A':['Generic Unclosed Opening Reveal']})
        source=self.card(session,'Generic Unclosed Opening Reveal')
        program=compile_best_available_card_program(self.db,session.engine.card_record(source),semantic_registry=session.engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        self.keep(session);self.assertTrue(session.state.started);self.assertFalse(session.state.delayed_triggers)

    def test_actual_dross_uses_actual_loss_when_one_opponents_loss_is_stopped(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass
        from quorune.effect_runtime.life_effects import collect_life_change_replacement_effects
        session=self.session(103639,{'A':['Chancellor of the Dross']})
        self.keep(session);self.action(session,'Reveal Chancellor of the Dross')
        stopped=ReplacementEffect(effect_id='fixture:opening-stopped-loss',source_id='fixture:opening-loss-boundary',event_kind='life.change',
            replacement_class=ReplacementClass.OTHER,conditions={'player':{'eq':'B'},'direction':{'eq':'loss'}},
            operations=({'op':'set','field':'amount','value':0},))
        original=collect_life_change_replacement_effects
        with patch('quorune.effect_runtime.life_effects.collect_life_change_replacement_effects',side_effect=lambda host:(*original(host),stopped)):
            self.assertIsNone(self.resolve(session))
            self.assertEqual([46,40,37,37],[session.state.players[p].life for p in 'ABCD'])
            self.replay(session,load=True)

    def test_reveal_permission_and_actual_total_gain_omission_mutants_are_killed(self):
        from quorune.semantic_choices.model import AutoContinue
        with patch.object(OpeningHandRevealPermissionHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_forge_first_upkeep_uses_revealing_controller_instead_of_starting_player()
        original=AutoContinue.resolved_effects
        def fixed_gain(value,results):
            effects=original(value,results)
            return tuple({**dict(e),'amount':3} if e.get('op')=='life_loss_then_gain_actual' and e.get('stage')=='gain' else e for e in effects)
        with patch.object(AutoContinue,'resolved_effects',fixed_gain):
            with self.assertRaises(AssertionError):self.test_actual_dross_uses_actual_loss_when_one_opponents_loss_is_stopped()

    def test_actual_originals_retain_their_normal_creature_and_entry_bodies(self):
        for name,mana,stats,keywords in (
                ('Chancellor of the Dross',{'B':3,'C':4},(6,6),{'flying','lifelink'}),
                ('Chancellor of the Tangle',{'G':3,'C':4},(6,7),{'reach','vigilance'}),
                ('Sphinx of Foresight',{'U':2,'C':2},(4,4),{'flying'})):
            with self.subTest(name=name):
                session=self.session(103640,{'A':[name,'Leyline of Anticipation']})
                self.keep(session);self.action(session,'Begin with Leyline of Anticipation')
                self.action(session,'Finish opening actions')
                source=self.card(session,name);self.fund_priority(session,'A',mana)
                result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                current=session.state.cards[source.object_id];self.assertEqual('battlefield',current.zone)
                self.assertEqual(stats,tuple(session.engine._numeric_stat(current.object_id,k) for k in ('power','toughness')))
                self.assertTrue(keywords.issubset(session.engine._combat_keywords(current)))
                self.replay(session,load=True)
        session=self.session(103641,{'A':['Chancellor of the Forge','Leyline of Anticipation']})
        self.keep(session);self.action(session,'Begin with Leyline of Anticipation');self.action(session,'Reveal Chancellor of the Forge')
        self.assertIsNone(self.resolve(session));self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' for c in session.state.cards.values()))
        source=self.card(session,'Chancellor of the Forge');self.fund_priority(session,'A',{'R':3,'C':4})
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(3,len(tokens));self.assertTrue(all(c.controller=='A' and 'haste' in session.engine._combat_keywords(c) for c in tokens))
        self.replay(session,load=True)

    def test_devourer_empty_library_completes_and_changed_looked_incarnation_rejects(self):
        session=self.session(103642,{'A':['Devourer of Destiny']})
        for oid in tuple(session.state.players['A'].zones['library']):session.engine.move_card(oid,'graveyard',reason='Empty library boundary fixture')
        self.checkpoint(session);self.keep(session);self.action(session,'Reveal Devourer of Destiny')
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.players['A'].zones['library'])
        self.assertFalse(session.state.players['A'].zones['exile']);self.replay(session,load=True)
        session=self.session(103643,{'A':['Devourer of Destiny']})
        self.keep(session);self.action(session,'Reveal Devourer of Destiny')
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        refs=session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs']
        card=next(c for c in session.state.cards.values() if c.ref==refs[0])
        session.engine.move_card(card.object_id,'graveyard',reason='Looked incarnation boundary fixture')
        self.reject(session,'choose',cards={'top':[],'exile':refs})
        self.assertFalse(session.state.players['A'].zones['exile'])

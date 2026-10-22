from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.activation_condition_model import activation_restriction_spec,ActivationConditionKind
from quorune.activation_usage import ActivationLimit,activation_usage_verdict,commit_activation_usage,ActivationUsageError
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_activation_restriction_material as controls
from quorune.rules.evidence_storage import read_evidence_document

ONCE='activation.usage.once_per_object'
HISTORY='activation.condition.current_turn_events'
NAMES=('Coffin Puppets','Dread Wanderer','Heron-Blessed Geist','Kuldotha Phoenix','Security Detail',
    'Cabal Inquisitor','Thought Shucker','In the Trenches','Hall of Oracles','Gutterbones')


class CompoundActivationRestrictionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ActivationRestrictionMaterialTests.session
    start=controls.ActivationRestrictionMaterialTests.start
    add=controls.ActivationRestrictionMaterialTests.add
    ready=controls.ActivationRestrictionMaterialTests.ready
    prepare=controls.ActivationRestrictionMaterialTests.prepare
    checkpoint=controls.ActivationRestrictionMaterialTests.checkpoint
    resolve=controls.ActivationRestrictionMaterialTests.resolve
    replay=controls.ActivationRestrictionMaterialTests.replay
    binding=controls.ActivationRestrictionMaterialTests.binding
    priority=controls.ActivationRestrictionMaterialTests.priority
    private_replay=controls.ActivationRestrictionMaterialTests.private_replay
    private=controls.ActivationRestrictionMaterialTests.private
    cast=controls.ActivationRestrictionMaterialTests.cast
    reject=controls.ActivationRestrictionMaterialTests.reject
    graveyard=controls.ActivationRestrictionMaterialTests.graveyard
    play_land=controls.ActivationRestrictionMaterialTests.play_land
    advance=controls.ActivationRestrictionMaterialTests.advance
    actions=controls.ActivationRestrictionMaterialTests.actions

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'compound-activations.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/compound-activation-restriction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Conjunctive activations',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def until(self,s,predicate):
        for _ in range(600):
            s.engine.pump()
            if predicate(s):return
            decision=s.state.pending_decision;self.assertIsNotNone(decision)
            command=({'action_id':'pass'} if decision.kind=='priority' else
                {'action_id':'attack','attacks':{}} if decision.kind=='combat.attackers' else
                {'action_id':'block','blocks':{}} if decision.kind=='combat.blockers' else None)
            self.assertIsNotNone(command,decision.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail('Compound restriction journey did not reach the expected window')

    def action(self,s,c,ability_id,seat='A'):
        return next((a for a in self.actions(s,seat) if a['id']=='activate:'+c.ref+':'+ability_id),None)

    def activate(self,s,c,ability_id,targets=(),*,seat='A',**extra):
        self.priority(s,seat);row=self.action(s,c,ability_id,seat);self.assertIsNotNone(row,(c.printed_name,ability_id))
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto',**extra}
        self.reject(s,command,'D' if seat!='D' else 'C');result=s.act('pilot:'+seat,command)
        self.assertTrue(result.ok,result.summary)

    def test_complete_conjunction_preserves_all_fields_and_refuses_partial_unknown_or_duplicate_clauses(self):
        spec=activation_restriction_spec('as a sorcery and only if you have one or fewer cards in hand')
        self.assertTrue(spec.sorcery_speed);self.assertEqual(1,spec.conditions[0].maximum)
        reverse=activation_restriction_spec('if you have one or fewer cards in hand and only as a sorcery')
        self.assertEqual(spec,reverse)
        spec=activation_restriction_spec('during your upkeep and only if you control a Swamp')
        self.assertEqual((ActivationConditionKind.CONTROLLERS_UPKEEP,ActivationConditionKind.PUBLIC_QUERY_COUNT),tuple(c.kind for c in spec.conditions))
        spec=activation_restriction_spec('if there are seven or more cards in your graveyard and only once')
        self.assertIs(ActivationLimit.ONCE_PER_OBJECT,spec.activation_limit);self.assertEqual(7,spec.conditions[0].minimum)
        for text in ['as a sorcery and only if the moon is hungry','once and only once each turn',
                'as a sorcery and only as a sorcery','if you control a Swamp and only if you control a Swamp',
                'if you control a Swamp or only during your upkeep','during your upkeep and only']:
            self.assertIsNone(activation_restriction_spec(text),text)
        once=activation_restriction_spec('once each turn');self.assertIs(ActivationLimit.ONCE_PER_TURN,once.activation_limit)
        self.assertEqual((),once.conditions);self.assertFalse(once.sorcery_speed)

    def test_once_per_object_is_distinct_from_turn_and_legacy_usage_fields(self):
        source=SimpleNamespace(annotations={},controller='A')
        self.assertTrue(activation_usage_verdict(source,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=1).available)
        commit_activation_usage(source,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=1)
        source.controller='B'
        self.assertFalse(activation_usage_verdict(source,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=2).available)
        with self.assertRaises(ActivationUsageError):commit_activation_usage(source,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=2)
        self.assertNotIn('exhaust_activations',source.annotations)
        self.assertTrue(activation_usage_verdict(source,ability_id='ab2',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=2).available)
        source.phased_out=True;source.phased_out=False
        self.assertFalse(activation_usage_verdict(source,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=3).available)
        for value in ({},'ab1',['ab1','ab1'],[None],['']):
            malformed=SimpleNamespace(annotations={'once_per_object_activations':value})
            with self.assertRaises(ActivationUsageError):
                activation_usage_verdict(malformed,ability_id='ab1',limit=ActivationLimit.ONCE_PER_OBJECT,turn_sequence=1)

    def test_turn_event_conditions_use_sealed_kind_actor_recipient_and_current_turn_only(self):
        from quorune.model import TurnHistory,TurnHistoryEvent
        from quorune.rules.activation.turn_event_conditions import activation_turn_event_status
        cast=ActivationConditionKind.CONTROLLER_CAST_INSTANT_SORCERY_THIS_TURN
        loss=ActivationConditionKind.OPPONENT_LOST_LIFE_THIS_TURN
        state=SimpleNamespace(turn_sequence=4,turn_history=TurnHistory(turn_sequence=4),
            players={seat:SimpleNamespace(in_game=seat!='D',life=40) for seat in 'ABCD'})
        host=SimpleNamespace(state=state)
        cases=[
            (cast,TurnHistoryEvent('spell_cast',actor='A',types=('instant',)),True),
            (cast,TurnHistoryEvent('spell_cast',actor='A',types=('sorcery',)),True),
            (cast,TurnHistoryEvent('spell_cast',actor='B',types=('instant',)),False),
            (cast,TurnHistoryEvent('spell_cast',actor='A',types=('creature',)),False),
            (cast,TurnHistoryEvent('permanent_entered',actor='A',types=('instant',)),False),
            (loss,TurnHistoryEvent('player_lost_life',actor='A',target='B',amount=2),True),
            (loss,TurnHistoryEvent('player_lost_life',actor='B',target='A',amount=2),False),
            (loss,TurnHistoryEvent('player_lost_life',target='D',amount=2),False),
            (loss,TurnHistoryEvent('player_lost_life',target='B',amount=0),False),
            (loss,TurnHistoryEvent('player_gained_life',target='B',amount=2),False),
            (loss,TurnHistoryEvent('player_damaged',target='B',amount=3),False),
        ]
        for kind,event,expected in cases:
            state.turn_history.events[:]=[event]
            self.assertEqual('payable' if expected else 'unavailable',activation_turn_event_status(host,'A',kind)[0],event)
        for history in (None,TurnHistory(turn_sequence=3)):
            state.turn_history=history
            for kind in (cast,loss):self.assertEqual('unresolved',activation_turn_event_status(host,'A',kind)[0])
        state.turn_history=TurnHistory(turn_sequence=4,events=[TurnHistoryEvent('player_lost_life',target='B',amount=2)])
        self.assertEqual('unresolved',activation_turn_event_status(host,'A',ActivationConditionKind.CONTROLLERS_TURN)[0])
        self.assertEqual('unresolved',activation_turn_event_status(host,'A',cast.value)[0])

    def test_ten_originals_have_complete_registration_and_independent_new_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(789000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            for program in s.engine.semantics.programs_for_oracle(source.oracle_id):
                for cap in (ONCE,HISTORY):
                    if cap not in program.capability_dependencies:continue
                    deps=tuple(c for c in program.capability_dependencies if c!=cap)
                    changed=replace(program,capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                    result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_dread_wanderer_cast_tapped_and_return_obeys_hand_and_sorcery_limits(self):
        for extra in (0,2):
            s=self.start(789010+extra);source=self.add(s.engine,'Dread Wanderer',zone='hand')
            kill=self.add(s.engine,'Murder',zone='hand')
            for i in range(extra):self.add(s.engine,'Generic Bound Plains',zone='hand',ref='compound-held-'+str(i))
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertTrue(s.state.cards[source.object_id].tapped)
            self.cast(s,kill,[source.ref]);self.assertIsNone(self.resolve(s))
            if extra:
                self.assertIsNone(self.action(s,source,'ab2'));self.reject(s,{'action_id':'activate:'+source.ref+':ab2','pay':'auto'},'A')
            else:
                self.activate(s,source,'ab2');s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertTrue(s.state.cards[source.object_id].tapped)
            self.private_replay(s,private)

    def test_thought_shucker_commits_one_use_and_bounce_returns_a_fresh_allowance(self):
        s=self.start(789020);source=self.add(s.engine,'Thought Shucker',zone='hand')
        bounce=self.add(s.engine,'Unsummon',zone='hand');self.graveyard(s,['Generic Bound Plains']*7)
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        hand=len(s.state.players['A'].zones['hand']);self.activate(s,source,'ab1')
        self.assertIn('once_per_object_activations',s.state.cards[source.object_id].annotations)
        self.assertEqual(['ab1'],s.state.cards[source.object_id].annotations['once_per_object_activations'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters['+1/+1']);self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertIsNone(self.action(s,source,'ab1'));self.reject(s,{'action_id':'activate:'+source.ref+':ab1','pay':'auto'},'A')
        logical=s.state.cards[source.object_id].logical_object_id
        self.cast(s,bounce,[source.ref]);self.assertIsNone(self.resolve(s));self.cast(s,s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(logical,s.state.cards[source.object_id].logical_object_id)
        self.activate(s,s.state.cards[source.object_id],'ab1');self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters['+1/+1']);self.private_replay(s,private)

    def test_trenches_once_limit_keeps_target_scope_and_linked_return_across_reentry(self):
        s=self.start(789030);source=self.add(s.engine,'In the Trenches',zone='hand')
        own=self.add(s.engine,'Generic Bound Body');enemy=self.add(s.engine,'Grizzly Bears',seat='B')
        other=self.add(s.engine,'Sol Ring',seat='B');bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        s.state.players['B'].mana_pool['U']=2;private=self.private(s);self.ready(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s));self.assertEqual(3,s.engine._numeric_stat(own.object_id,'power'))
        row=self.action(s,source,'ab2');self.assertIsNotNone(row)
        self.reject(s,{'action_id':row['id'],'targets':[own.ref],'pay':'auto'},'A')
        self.activate(s,source,'ab2',[enemy.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[enemy.object_id].zone)
        self.assertIsNone(self.action(s,source,'ab2'))
        self.cast(s,bounce,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[enemy.object_id].zone)
        self.cast(s,s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.activate(s,s.state.cards[source.object_id],'ab2',[other.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[other.object_id].zone);self.private_replay(s,private)

    def test_security_detail_uses_all_conditions_and_resets_its_turn_allowance(self):
        s=self.start(789040);source=self.add(s.engine,'Security Detail',zone='hand')
        bounce=self.add(s.engine,'Unsummon',zone='hand')
        for i in range(2):self.add(s.engine,'Plains',ref='compound-security-mana-'+str(i))
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.activate(s,source,'ab1');self.assertIsNone(self.resolve(s))
        token=next(c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A')
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ['power','toughness']))
        self.cast(s,bounce,[token.ref]);self.assertIsNone(self.resolve(s));self.assertIsNone(self.action(s,source,'ab1'))
        self.until(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.activate(s,source,'ab1');self.assertIsNone(self.resolve(s))
        self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values()))
        self.private_replay(s,private)

    def test_cabal_sorcery_threshold_cost_is_checked_before_its_two_card_payment(self):
        for count in (6,7):
            s=self.start(789050+count);source=self.add(s.engine,'Cabal Inquisitor',zone='hand')
            self.add(s.engine,'Swamp',ref='compound-inquisitor-black-mana')
            self.add(s.engine,'Plains',ref='compound-inquisitor-generic-mana')
            graves=self.graveyard(s,['Generic Bound Plains']*count)
            victim=self.add(s.engine,'Generic Bound Growth',seat='B',zone='hand',ref='compound-victim-hand')
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            # Use the actual next turn to satisfy the source's tap acquisition restriction.
            self.until(s,lambda x:x.state.active_player=='A' and x.state.turn_sequence>1 and x.state.phase=='precombat_main')
            if count==6:
                self.assertIsNone(self.action(s,source,'ab1'));self.private_replay(s,private);continue
            self.activate(s,source,'ab1',['B'],cost_cards=[g.ref for g in graves[:2]])
            self.assertEqual(5,len(s.state.players['A'].zones['graveyard']))
            decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('pilot:B',s.pending_principals()[0])
            s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','cards':[victim.ref]})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
            self.assertEqual('graveyard',s.state.cards[victim.object_id].zone);self.private_replay(s,private)

    def test_coffin_requires_own_upkeep_swamp_and_both_lands_before_cost_payment(self):
        s=self.start(789070);source=self.add(s.engine,'Coffin Puppets',zone='hand')
        kill=self.add(s.engine,'Murder',zone='hand')
        swamp=self.add(s.engine,'Swamp');forest=self.add(s.engine,'Forest')
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.cast(s,kill,[source.ref]);self.assertIsNone(self.resolve(s));self.assertIsNone(self.action(s,source,'ab1'))
        self.until(s,lambda x:x.state.active_player=='A' and x.state.turn_sequence>1 and x.state.step=='upkeep')
        self.activate(s,source,'ab1',cost_cards=[swamp.ref,forest.ref])
        self.assertEqual('graveyard',s.state.cards[swamp.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[forest.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_heron_requires_own_enchantment_and_sorcery_but_not_after_exile_payment(self):
        for own in (False,True):
            s=self.start(789080+own);source=self.add(s.engine,'Heron-Blessed Geist',zone='hand')
            enchantment=self.add(s.engine,'Generic Compound Enchantment',seat='A' if own else 'B')
            kill=self.add(s.engine,'Murder',zone='hand')
            bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.cast(s,kill,[source.ref]);self.assertIsNone(self.resolve(s))
            if not own:self.assertIsNone(self.action(s,source,'ab2'));self.private_replay(s,private);continue
            self.activate(s,source,'ab2');self.assertEqual('exile',s.state.cards[source.object_id].zone)
            self.cast(s,bounce,[enchantment.ref],seat='B');self.assertIsNone(self.resolve(s))
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
            self.assertEqual(2,len(tokens))
            for c in tokens:self.assertIn('flying',s.engine._combat_keywords(c));self.assertEqual((1,1),tuple(s.engine._numeric_stat(c.object_id,k) for k in ['power','toughness']))
            self.private_replay(s,private)

    def test_phoenix_requires_three_current_artifacts_in_own_upkeep(self):
        for count in (2,3):
            s=self.start(789090+count);source=self.add(s.engine,'Kuldotha Phoenix',zone='hand')
            kill=self.add(s.engine,'Murder',zone='hand')
            for i in range(count):self.add(s.engine,'Memnite',ref='compound-phoenix-artifact-'+str(i))
            for i in range(4):self.add(s.engine,'Plains',ref='compound-phoenix-mana-'+str(i))
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertTrue({'flying','haste'}<=s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.cast(s,kill,[source.ref]);self.assertIsNone(self.resolve(s));self.assertIsNone(self.action(s,source,'ab2'))
            self.until(s,lambda x:x.state.active_player=='A' and x.state.turn_sequence>1 and x.state.step=='upkeep')
            if count==2:self.assertIsNone(self.action(s,source,'ab2'));self.private_replay(s,private);continue
            self.activate(s,source,'ab2');s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_hall_uses_a_real_own_spell_cast_even_when_countered_and_obeys_sorcery_timing(self):
        s=self.start(789100);source=self.add(s.engine,'Hall of Oracles',zone='hand')
        target=self.add(s.engine,'Grizzly Bears');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
        private=self.private(s);self.ready(s);self.play_land(s,source)
        self.assertIsNone(self.action(s,source,'ab3'))
        self.cast(s,bolt,['B']);self.assertIsNone(self.action(s,source,'ab3'))
        victim=s.state.stack[-1].ref;self.cast(s,counter,[victim],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['B'].life)
        self.activate(s,source,'ab3',[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[target.object_id].counters['+1/+1']);self.private_replay(s,private)

    def test_gutterbones_return_reads_actual_opponent_life_loss_not_own_damage(self):
        for who in ('A','B'):
            s=self.start(789110+(who=='B'));source=self.add(s.engine,'Gutterbones',zone='hand')
            kill=self.add(s.engine,'Murder',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertTrue(s.state.cards[source.object_id].tapped)
            self.cast(s,kill,[source.ref]);self.assertIsNone(self.resolve(s));self.assertIsNone(self.action(s,source,'ab2'))
            self.cast(s,bolt,[who]);self.assertIsNone(self.resolve(s))
            if who=='A':self.assertIsNone(self.action(s,source,'ab2'))
            else:
                self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
            self.private_replay(s,private)

    def test_once_usage_is_not_spent_by_threshold_rejection_and_survives_turn_and_control_change(self):
        s=self.start(789120);source=self.add(s.engine,'Thought Shucker',zone='hand')
        self.graveyard(s,['Generic Bound Plains']*6)
        for i in range(7):self.add(s.engine,'Generic Bound Plains',seat='B',zone='graveyard',ref='compound-b-grave-'+str(i))
        enemy=self.add(s.engine,'Grizzly Bears',seat='B');kill=self.add(s.engine,'Murder',zone='hand')
        control=self.add(s.engine,'Control Magic',seat='B',zone='hand')
        for i,name in enumerate(('Island','Island','Plains','Plains')):
            self.add(s.engine,name,seat='B',ref='compound-control-mana-'+str(i))
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertIsNone(self.action(s,source,'ab1'))
        self.reject(s,{'action_id':'activate:'+source.ref+':ab1','pay':'auto'},'A')
        self.assertNotIn('once_per_object_activations',s.state.cards[source.object_id].annotations)
        self.cast(s,kill,[enemy.ref]);self.assertIsNone(self.resolve(s))
        self.activate(s,source,'ab1');self.assertIsNone(self.resolve(s))
        logical=s.state.cards[source.object_id].logical_object_id
        self.until(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.cast(s,control,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];self.assertEqual(('B',logical),(current.controller,current.logical_object_id))
        self.assertEqual(1,current.counters['+1/+1'])
        self.priority(s,'B');self.assertIsNone(self.action(s,current,'ab1','B'))
        self.reject(s,{'action_id':'activate:'+current.ref+':ab1','pay':'auto'},'B')
        self.private_replay(s,private)

    def test_stale_enchantment_condition_rejects_before_exile_cost(self):
        s=self.start(789130);source=self.add(s.engine,'Heron-Blessed Geist',zone='graveyard')
        enchantment=self.add(s.engine,'Generic Compound Enchantment')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
        private=self.private(s);self.ready(s);row=self.action(s,source,'ab2');self.assertIsNotNone(row)
        self.cast(s,bounce,[enchantment.ref],seat='B');self.assertIsNone(self.resolve(s));self.priority(s,'A')
        self.assertIsNone(self.action(s,source,'ab2'));self.reject(s,{'action_id':row['id'],'pay':'auto'},'A')
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_hall_other_player_cast_and_actual_copy_do_not_create_own_cast_fact(self):
        s=self.start(789140);source=self.add(s.engine,'Hall of Oracles',zone='hand')
        bolt=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand');s.state.players['B'].mana_pool['R']=3
        private=self.private(s);self.ready(s);self.play_land(s,source)
        self.cast(s,bolt,['C'],seat='B');original=s.state.stack[-1].ref
        self.cast(s,copy,[original],seat='B');decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,{'action_id':'choose','targets':['D']},'A');s=self.replay(s,load=True)
        result=s.act('pilot:B',{'action_id':'choose','targets':['D']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(any(item.kind=='spell_copy' for item in s.state.stack));self.assertIsNone(self.resolve(s))
        self.assertEqual((37,37),(s.state.players['C'].life,s.state.players['D'].life))
        casts=s.engine._current_turn_history('spell_cast');self.assertEqual(2,len(casts));self.assertTrue(all(e.actor=='B' for e in casts))
        self.assertIsNone(self.action(s,source,'ab3'));self.reject(s,{'action_id':'activate:'+source.ref+':ab3','pay':'auto'},'A')
        self.private_replay(s,private)

    def test_hall_color_conversion_mana_remains_available_with_a_spell_on_the_stack(self):
        s=self.start(789145);source=self.add(s.engine,'Hall of Oracles',zone='hand')
        bolt=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand');s.state.players['B'].mana_pool['R']=1
        private=self.private(s);self.ready(s);self.play_land(s,source);self.cast(s,bolt,['A'],seat='B')
        before=dict(s.state.players['A'].mana_pool);stack=len(s.state.stack)
        self.activate(s,source,'ab2',pay='manual',payment={'C':1},mana_output={'G':1})
        self.assertEqual((before['C']-1,before['G']+1),(s.state.players['A'].mana_pool['C'],s.state.players['A'].mana_pool['G']))
        self.assertEqual(stack,len(s.state.stack));self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['A'].life);self.private_replay(s,private)

    def test_hall_qualifying_cast_fact_resets_on_the_next_turn(self):
        s=self.start(789150);source=self.add(s.engine,'Hall of Oracles',zone='hand')
        self.add(s.engine,'Grizzly Bears')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.private(s);self.ready(s)
        self.play_land(s,source);self.cast(s,bolt,['B']);self.assertIsNone(self.resolve(s))
        self.assertIsNotNone(self.action(s,source,'ab3'))
        self.until(s,lambda x:x.state.active_player=='A' and x.state.turn_sequence>1 and x.state.phase=='precombat_main')
        self.assertFalse(s.state.cards[source.object_id].tapped);self.assertIsNone(self.action(s,source,'ab3'))
        self.reject(s,{'action_id':'activate:'+source.ref+':ab3','pay':'auto'},'A')
        before=s.state.players['A'].mana_pool['C'];self.activate(s,source,'ab1')
        self.assertEqual(before+1,s.state.players['A'].mana_pool['C']);self.private_replay(s,private)

    def test_gutterbones_nondamage_loss_and_missing_history_share_offer_and_commit(self):
        for history_available in (True,False):
            s=self.start(789160+history_available);source=self.add(s.engine,'Gutterbones',zone='graveyard')
            loss=self.add(s.engine,'Sign in Blood',zone='hand')
            if not history_available:s.state.turn_history=None
            private=self.private(s);self.ready(s);self.assertIsNone(self.action(s,source,'ab2'))
            self.cast(s,loss,['B']);self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['B'].life)
            if history_available:
                self.assertFalse(s.engine._current_turn_history('player_damaged'))
                self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
            else:
                self.assertIsNone(self.action(s,source,'ab2'));self.reject(s,{'action_id':'activate:'+source.ref+':ab2','pay':'auto'},'A')
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            self.private_replay(s,private)

    def test_verified_withdrawals_and_fresh_composition_usage_omissions_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in [(ONCE,'Thought Shucker'),(HISTORY,'Hall of Oracles')]:
            value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn compound activation authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blocks=[b for n in ir.material_residuals for b in n.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blocks),blocks);self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        method='test_ten_originals_have_complete_registration_and_independent_new_runtime_minima'
        with patch('quorune.activation_restriction_composition.compound_activation_restriction',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_thought_shucker_commits_one_use_and_bounce_returns_a_fresh_allowance'
        with patch('quorune.rules.activation.payment_commit.commit_activation_usage',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_gutterbones_return_reads_actual_opponent_life_loss_not_own_damage'
        with patch('quorune.rules.activation.turn_event_conditions.activation_turn_event_status',return_value=('payable',None)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

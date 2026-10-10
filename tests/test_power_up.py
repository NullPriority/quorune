from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.abilities import ActivatedAbility,parse_activated_abilities
from quorune.activation_usage import ActivationLimit,activation_usage_verdict,commit_activation_usage
from quorune.compiler.power_up_templates import fixed_power_up_ability
from quorune.power_up_model import PowerUpSpec,power_up_reduction_options,reduce_power_up_requirements
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
from test_qualified_zone_event_queries import query_record


class PowerUpCompilerTests(unittest.TestCase):
    def test_reductions_follow_colorless_excess_hybrid_and_phyrexian_rules(self):
        cases=(
            ({'GENERIC':4,'G':1},'{2}{G}',{'GENERIC':2}),
            ({'GENERIC':4,'G':1},'{1}{U}',{'GENERIC':2,'G':1}),
            ({'GENERIC':4,'G':1},'{G}{G}{G}',{'GENERIC':2}),
            ({'GENERIC':2,'C':1},'{C}{C}',{'GENERIC':1}),
            ({'GENERIC':1,'U':1},'{5}',{'U':1}),
            ({'GENERIC':2,'G':1},'{G/P}',{'GENERIC':2}),
            ({'GENERIC':2},'{X}{G}',{'GENERIC':1}),
        )
        for required,cost,expected in cases:
            actual=reduce_power_up_requirements(required,power_up_reduction_options(cost)[0])
            self.assertEqual(expected,{k:v for k,v in actual.items() if v})
        outcomes=[dict(reduce_power_up_requirements({'GENERIC':4,'G':1},r)) for r in power_up_reduction_options('{1}{G/U}')]
        self.assertEqual({(3,0),(2,1)},{(r['GENERIC'],r['G']) for r in outcomes})
        for cost in ('{S}','{Q}','{G}junk','{2/W/W}',None):
            with self.assertRaises(ValueError):power_up_reduction_options(cost)
        for malformed in ({'G':True},{'UNKNOWN':1}):
            with self.assertRaises(ValueError):reduce_power_up_requirements(malformed,{'GENERIC':1})

    def test_power_up_codec_pins_pricing_and_usage_without_reinterpreting_old_descriptors(self):
        old=parse_activated_abilities(card_name='Generic Power-up',oracle_text='Power-up — {4}{G}: Draw a card.',keywords=())[0]
        self.assertTrue(old.uncompiled_costs)
        current=fixed_power_up_ability(old)
        self.assertEqual(ActivationLimit.POWER_UP_ONCE,current.activation_limit)
        self.assertEqual(PowerUpSpec(),current.power_up);self.assertFalse(current.uncompiled_costs)
        self.assertEqual(current,ActivatedAbility.from_dict(current.to_dict()))
        legacy=parse_activated_abilities(card_name='Generic Source',oracle_text='{1}: Draw a card.',keywords=())[0]
        self.assertNotIn('power_up',legacy.to_dict())
        self.assertEqual(legacy,ActivatedAbility.from_dict(legacy.to_dict()))
        for raw in ({'schema_version':True},{'schema_version':2},{'schema_version':1,'extra':0}):
            with self.assertRaises(ValueError):PowerUpSpec.from_dict(raw)
        with self.assertRaises(ValueError):replace(current,activation_limit=None)
        with self.assertRaises(ValueError):replace(current,power_up=None)

    def test_once_per_incarnation_usage_does_not_reset_on_another_turn(self):
        source=SimpleNamespace(annotations={})
        commit_activation_usage(source,ability_id='ability-one',limit=ActivationLimit.POWER_UP_ONCE,turn_sequence=3)
        for turn in (3,4,12):self.assertFalse(activation_usage_verdict(source,ability_id='ability-one',limit=ActivationLimit.POWER_UP_ONCE,turn_sequence=turn).available)
        self.assertTrue(activation_usage_verdict(source,ability_id='ability-two',limit=ActivationLimit.POWER_UP_ONCE,turn_sequence=3).available)

    def test_current_entry_turn_prices_ignore_mana_value_and_reject_unknown_symbols(self):
        from quorune.power_up import current_power_up_mana_options
        from quorune.activation_mana_cost import ActivationManaCostOption
        from quorune.replacement.immutable import FrozenMap
        source=SimpleNamespace(zone='battlefield',entered_battlefield_turn_sequence=2)
        cost={'mana_cost':'{1}{G}','mana_value':99}
        host=SimpleNamespace(state=SimpleNamespace(turn_sequence=3),_effective_card_data=lambda card:cost)
        base=(ActivationManaCostOption('base',FrozenMap({'GENERIC':4,'G':1})),)
        ability=SimpleNamespace(power_up=PowerUpSpec())
        self.assertEqual(base,current_power_up_mana_options(host,source,ability,base))
        source.entered_battlefield_turn_sequence=3
        prices=current_power_up_mana_options(host,source,ability,base)
        self.assertEqual({'GENERIC':3},{k:v for k,v in prices[0].requirements.items() if v})
        cost['mana_cost']='{2}{U}'
        prices=current_power_up_mana_options(host,source,ability,base)
        self.assertEqual({'GENERIC':1,'G':1},{k:v for k,v in prices[0].requirements.items() if v})
        cost['mana_cost']='{S}'
        with self.assertRaises(ValueError):current_power_up_mana_options(host,source,ability,base)
        source.entered_battlefield_turn_sequence=True
        with self.assertRaises(ValueError):current_power_up_mana_options(host,source,ability,base)

    def test_power_up_requires_its_pricing_capability_before_runtime_trust(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.oracle_ir import generated_programs
        from quorune.card_programs.binding import bind_semantic_program_runtime
        text='Power-up — {4}{G}: Put two +1/+1 counters on this creature.'
        record=replace(query_record(text),type_line='Creature',mana_cost='{2}{G}')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status)
        self.assertIn('activation.power_up.entered_turn_cost',ir.faces[0].nodes[0].capability_dependencies)
        class NoRulings:
            def rulings(self,record):return ()
        program=next(p for p in generated_programs(NoRulings(),record,capability_registry=registry,capability_profile='commander_review') if p.event=='activate' and p.effects)
        dependencies=tuple(cap for cap in program.capability_dependencies if cap!='activation.power_up.entered_turn_cost')
        forged=replace(program,capability_dependencies=dependencies,capability_closure=registry.closure(dependencies,profile='commander_review').to_dict())
        binding=bind_semantic_program_runtime(forged,capability_registry=registry,profile='commander_review')
        self.assertIn('capability:undeclared_runtime_dependency:activation.power_up.entered_turn_cost',binding['blockers'])
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in value['capabilities']:
            if row['id']=='activation.power_up.entered_turn_cost':row.update(status='blocked',blockers=['independent Power-up owner unavailable'])
        ir=compile_oracle_card(record,capability_registry=CapabilityRegistry(value),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_power_up_cost_and_usage_mutants_are_killed(self):
        from quorune import power_up as pricing
        host=SimpleNamespace(state=SimpleNamespace(turn_sequence=3),_effective_card_data=lambda source:{'mana_cost':'{1}{G}'})
        source=SimpleNamespace(zone='battlefield',entered_battlefield_turn_sequence=3)
        from quorune.activation_mana_cost import ActivationManaCostOption
        from quorune.replacement.immutable import FrozenMap
        with patch.object(pricing,'reduce_power_up_requirements',return_value=FrozenMap({'GENERIC':4,'G':1})):
            with self.assertRaises(AssertionError):
                options=pricing.current_power_up_mana_options(host,source,SimpleNamespace(power_up=PowerUpSpec()),(ActivationManaCostOption('base',FrozenMap({'GENERIC':4,'G':1})),))
                self.assertEqual(3,options[0].requirements['GENERIC'])


class PowerUpRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'power.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/power-up-cards.json',ROOT/'tests/fixtures/counter-placement-event-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Power-up witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_brawn_hybrid_entry_discount_once_and_replay(self):
        session=self.session(290001);engine=session.engine
        source=self.add(engine,'Brawn, Amadeus Cho',zone='hand')
        action=self.ready(session,source,{'C':4,'G':2});self.checkpoint(session)
        cost=next(row['id'] for row in action['cost_options'] if row['requirements'].get('G')==1)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':cost,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        options=activation['cost_options']
        self.assertTrue(any(row['requirements'].get('GENERIC')==3 and row['requirements'].get('G')==0 for row in options))
        self.assertTrue(any(row['requirements'].get('GENERIC')==2 and row['requirements'].get('G')==1 for row in options))
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'action_id':activation['id'],'cost_option':options[0]['id'],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        expected=len(session.state.players['A'].zones['hand'])
        selected=next(row['id'] for row in options if row['requirements'].get('GENERIC')==3 and row['requirements'].get('G')==0)
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_option':selected,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(expected,session.state.cards[source.object_id].counters.get('+1/+1',0))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']==activation['id'] for row in actions))
        before=authoritative_state_hash(session.state)
        repeated=session.act('pilot:A',{'action_id':activation['id'],'cost_option':selected,'pay':'auto'})
        self.assertFalse(repeated.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.replay(session,load=True)

    def test_actual_old_source_pays_full_price_and_pending_activation_replays(self):
        session=self.session(290002);engine=session.engine
        source=self.add(engine,'Aerial Doombot')
        action=self.ready(session,source,{'C':5,'U':1});self.checkpoint(session)
        options=action['cost_options'];self.assertEqual(1,len(options))
        self.assertEqual({'GENERIC':5,'U':1},{k:v for k,v in options[0]['requirements'].items() if v})
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':options[0]['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(session.state.stack));self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        resumed=self.replay(session,load=True);self.resolve(resumed)
        self.assertEqual(3,resumed.state.cards[source.object_id].counters['+1/+1'])
        self.replay(resumed,load=True)

    def test_actual_captain_marvel_entry_discount_commits_both_counter_kinds(self):
        session=self.session(290003);engine=session.engine
        source=self.add(engine,"Captain Marvel, Earth's Protector",zone='hand')
        action=self.ready(session,source,{'C':5,'W':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        self.assertEqual({'GENERIC':2},{k:v for k,v in activation['cost_options'][0]['requirements'].items() if v})
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_option':activation['cost_options'][0]['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual({'+1/+1':1,'indestructible':1},session.state.cards[source.object_id].counters)
        self.replay(session,load=True)

    def test_stale_entry_discount_rejects_without_spending_usage(self):
        session=self.session(290004);engine=session.engine
        source=self.add(engine,'Aerial Doombot',zone='hand')
        action=self.ready(session,source,{'C':5,'U':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        discounted=activation['cost_options'][0]['id']
        engine.state.turn_sequence+=1
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':activation['id'],'cost_option':discounted,'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertTrue(activation_usage_verdict(source,ability_id=activation['id'].rsplit(':',1)[-1],limit=ActivationLimit.POWER_UP_ONCE,turn_sequence=engine.state.turn_sequence).available)

    def test_power_up_usage_survives_control_change_and_resets_on_reentry(self):
        session=self.session(290005);engine=session.engine
        source=self.add(engine,'Aerial Doombot')
        action=self.ready(session,source,{'C':5,'U':1})
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':action['cost_options'][0]['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        ability=engine._activated_abilities(source)[0]
        self.assertFalse(activation_usage_verdict(source,ability_id=ability.ability_id,limit=ability.activation_limit,turn_sequence=engine.state.turn_sequence).available)
        engine.change_control(source.object_id,'B',reason='Power-up controller diagnostic')
        self.assertFalse(activation_usage_verdict(source,ability_id=ability.ability_id,limit=ability.activation_limit,turn_sequence=engine.state.turn_sequence+1).available)
        source.phased_out=True;source.phased_out=False
        self.assertFalse(activation_usage_verdict(source,ability_id=ability.ability_id,limit=ability.activation_limit,turn_sequence=engine.state.turn_sequence+1).available)
        engine.move_card(source.object_id,'graveyard',log=False);engine.move_card(source.object_id,'battlefield',controller='A',log=False)
        self.assertTrue(activation_usage_verdict(source,ability_id=ability.ability_id,limit=ability.activation_limit,turn_sequence=engine.state.turn_sequence).available)

    def test_underpayment_rolls_back_mana_usage_and_stack(self):
        session=self.session(290006);engine=session.engine
        source=self.add(engine,'Aerial Doombot')
        action=self.ready(session,source,{'C':5,'U':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'cost_option':action['cost_options'][0]['id'],
            'pay':'manual','payment':{'U':1,'C':4}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual([],session.state.stack)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':action['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session);self.replay(session,load=True)

    def test_actual_usage_mutant_is_killed_before_repeated_activation(self):
        from quorune.rules.activation import commit as owner
        with patch.object(owner,'commit_activation_usage',return_value=None):
            with self.assertRaises(AssertionError):self.test_actual_old_source_pays_full_price_and_pending_activation_replays()

    def test_entry_price_changes_after_new_incarnation_and_stale_source_is_rejected(self):
        session=self.session(290007);engine=session.engine
        source=self.add(engine,'Aerial Doombot')
        action=self.ready(session,source,{'C':6,'U':1})
        old_option=action['cost_options'][0]['id'];old_identity=source.logical_object_id
        engine.move_card(source.object_id,'graveyard',log=False);engine.move_card(source.object_id,'battlefield',controller='A',log=False)
        self.assertNotEqual(old_identity,source.logical_object_id)
        before=authoritative_state_hash(session.state)
        stale=session.act('pilot:A',{'action_id':action['id'],'cost_option':old_option,'pay':'auto'})
        self.assertFalse(stale.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        new=self.ready(session,source,{})
        self.assertEqual({'GENERIC':5},{k:v for k,v in new['cost_options'][0]['requirements'].items() if v})
        accepted=session.act('pilot:A',{'action_id':new['id'],'cost_option':new['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(3,session.state.cards[source.object_id].counters['+1/+1'])

    def test_unrepresented_current_mana_cost_is_unavailable_before_payment(self):
        session=self.session(290008);engine=session.engine
        source=self.add(engine,'Aerial Doombot',zone='hand')
        action=self.ready(session,source,{'C':5,'U':1})
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        ability=engine._activated_abilities(source)[0]
        from quorune.rules.activation.availability import activation_availability
        original=engine._effective_card_data
        def unsupported(card,*args,**kwargs):
            data=original(card,*args,**kwargs)
            return {**data,'mana_cost':'{S}'} if card.object_id==source.object_id else data
        before=authoritative_state_hash(session.state)
        with patch.object(engine,'_effective_card_data',side_effect=unsupported):
            status,reason=activation_availability(engine,'A',source,ability)
            self.assertEqual(('unresolved','unresolved_activation_price'),(status,reason))
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_proposal_revalidates_entry_price_before_payment_and_usage(self):
        session=self.session(290009);engine=session.engine
        source=self.add(engine,'Aerial Doombot',zone='hand')
        cast=self.ready(session,source,{'C':6,'U':1})
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        activation=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+source.ref+':'))
        ability=engine._activated_abilities(source)[0]
        from quorune.rules.activation.model import ActivationProposalRequest,ActivationProposalError
        from quorune.rules.activation.proposal import build_activation_proposal
        from quorune.rules.activation.commit import commit_activation
        proposal=build_activation_proposal(engine,ActivationProposalRequest.from_submission('A',{
            'source':source.ref,'from':'battlefield','ability':ability.ability_id,'cost_option':activation['cost_options'][0]['id']}))
        engine.state.turn_sequence+=1
        before=authoritative_state_hash(session.state)
        with self.assertRaises(ActivationProposalError):commit_activation(engine,proposal,{'pay':'auto'})
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_targeted_power_up_restores_card_and_replays(self):
        session=self.session(290010);engine=session.engine
        source=self.add(engine,'Unliving Legionnaire')
        target=self.add(engine,'Generic Bound Body',zone='graveyard')
        action=self.ready(session,source,{'C':5,'B':2});self.checkpoint(session)
        self.assertIn(target.ref,action['target_schema']['legal_refs'])
        option=action['cost_options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':option,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('hand',session.state.cards[target.object_id].zone)
        self.assertEqual(2,session.state.cards[source.object_id].counters['+1/+1'])
        self.replay(session,load=True)

    def test_actual_targeted_power_up_rejects_departed_target_before_usage(self):
        session=self.session(290011);engine=session.engine
        source=self.add(engine,'Unliving Legionnaire')
        target=self.add(engine,'Generic Bound Body',zone='graveyard')
        action=self.ready(session,source,{'C':5,'B':2})
        engine.move_card(target.object_id,'hand',log=False)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'cost_option':action['cost_options'][0]['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual([],session.state.stack)

    def test_actual_ultron_entry_discount_creates_one_token_and_replays(self):
        session=self.session(290012);engine=session.engine
        source=self.add(engine,'Ultron Drone',zone='hand')
        # Its {6} activation minus printed {3} is {3}, preserving the
        # independently compiled counter-and-token result.
        action=self.ready(session,source,{'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        activation=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+source.ref+':'))
        self.assertEqual({'GENERIC':3},{k:v for k,v in activation['cost_options'][0]['requirements'].items() if v})
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_option':activation['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,session.state.cards[source.object_id].counters['+1/+1'])
        tokens=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual('A',tokens[0].controller)
        self.replay(session,load=True)

    def test_zero_clamped_entry_price_is_offered_and_accepted_once(self):
        session=self.session(290013);engine=session.engine
        source=self.add(engine,'Generic Zero Price Power Up',zone='hand')
        action=self.ready(session,source,{'C':5,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        activation=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+source.ref+':'))
        self.assertFalse(any(activation['cost_options'][0]['requirements'].values()))
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_option':activation['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(1,session.state.cards[source.object_id].counters['+1/+1'])
        self.replay(session,load=True)

    def test_actual_copied_power_up_has_copied_mana_cost_and_independent_usage(self):
        session=self.session(290014);engine=session.engine
        source=self.add(engine,'Aerial Doombot')
        spell=self.add(engine,'Generic Counter Entry Copies',zone='hand')
        action=self.ready(session,spell,{'C':6,'U':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        copies=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield']
        self.assertEqual(2,len(copies))
        chosen=copies[0]
        self.assertEqual('{U}',engine._effective_card_data(chosen)['mana_cost'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:'+chosen.ref+':'))
        self.assertEqual({'GENERIC':5},{k:v for k,v in activation['cost_options'][0]['requirements'].items() if v})
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_option':activation['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(3,session.state.cards[chosen.object_id].counters['+1/+1'])
        self.assertEqual(0,session.state.cards[copies[1].object_id].counters.get('+1/+1',0))
        other=engine._activated_abilities(session.state.cards[copies[1].object_id])[0]
        self.assertTrue(activation_usage_verdict(session.state.cards[copies[1].object_id],ability_id=other.ability_id,limit=other.activation_limit,turn_sequence=engine.state.turn_sequence).available)
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json, tempfile, unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_effect_payment import FixedEffectPaymentSpec, MANA_DISCARD_PAYMENT_CAPABILITY as PAYMENT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.turn_cast_grant_model import TURN_CAST_NONBATTLEFIELD_CAPABILITY as ARRIVAL
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=("Banon, the Returners' Leader",)


class ManaDiscardPaymentTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    advance=controls.TurnCastGrantTests.advance
    options=controls.TurnCastGrantTests.options
    grants=controls.TurnCastGrantTests.grants
    use=controls.TurnCastGrantTests.use
    top=controls.TurnCastGrantTests.top

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'mana-discard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/mana-discard-payment-cards.json',ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Combined resolution payments',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def attack_choice(self,s,attacker):
        self.advance(s,lambda q:q.state.pending_decision.kind=='combat.attackers')
        command={'action_id':'attack','attackers':{attacker.ref:'B'}}
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        return pending

    def choose(self,s,**response):
        command={'action_id':'choose',**response};self.reject(s,command,'pilot:B')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_banon_has_complete_normal_admission_and_two_independent_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(804000);card=self.add(s.engine,NAMES[0]);bound=self.binding(s,card)
        self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        programs=s.engine.semantics.programs_for_oracle(card.oracle_id)
        for cap in (PAYMENT,ARRIVAL):
            program=next((p for p in programs if cap in p.capability_dependencies),None);self.assertIsNotNone(program,cap)
            deps=tuple(d for d in program.capability_dependencies if d!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            runtime=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(runtime['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,runtime['blockers'])

    def test_combined_payload_is_closed_and_old_cost_wires_remain_unchanged(self):
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
        spec=fixed_effect_payment_spec('pay {1}{R} and discard a card');self.assertIsNotNone(spec)
        self.assertEqual((5,'mana_discard',1),(spec.schema_version,spec.kind,spec.amount))
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for changes in ({'schema_version':2},{'amount':2},{'amount':True},{'requirements':None},
                {'predicate':replace(spec.predicate,zones=('graveyard',))}):
            with self.assertRaises(ValueError):replace(spec,**changes)
        with self.assertRaises(ValueError):FixedEffectPaymentSpec.from_dict({**spec.to_dict(),'unchecked':True})
        self.assertEqual({'schema_version':2,'kind':'life','amount':1,'requirements':None,'predicate':None},FixedEffectPaymentSpec('life').to_dict())
        for cost in ('pay {0} and discard a card','pay {X} and discard a card','pay {R/P} and discard a card',
                'pay {1} and discard two cards','pay {1} or discard a card','pay {1} and discard a card at random'):
            self.assertIsNone(fixed_effect_payment_spec(cost),cost)

    def test_other_carriers_keep_independent_unsupported_results_and_siblings(self):
        for name in ('Conspiracy Theorist',"Dralnu's Pet","Wolf of Devil's Breach"):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);self.assertTrue(ir.material_residuals,name)

    def test_actual_player_attack_pays_both_costs_draws_once_and_replays_private_choice(self):
        s=self.session(804010);self.add(s.engine,NAMES[0]);attacker=self.add(s.engine,'Generic Bound Body')
        land=self.add(s.engine,'Mountain');discard=self.add(s.engine,'Generic Bound Body',zone='hand',ref='paid-creature')
        drawn=self.top(s,'Sol Ring','paid-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);pending=self.attack_choice(s,attacker)
        context=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertTrue(context['payable']);self.assertEqual('mana_discard',context['payment_kind'])
        self.assertNotIn(discard.ref,json.dumps(s.packet('pilot:B',full=True)))
        s=self.replay(s,load=True);self.choose(s,cards=[discard.ref]);self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual('graveyard',s.state.cards[discard.object_id].zone);self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
        self.assertEqual(1,sum(e.code=='effect.optional_mana.paid' for e in s.state.events))
        self.assertFalse(s.state.stack);self.private_replay(s,private)
        self.advance(s,lambda q:q.state.phase=='postcombat_main')
        self.assertTrue(s.engine._compiled_zone_cast_permission('A',s.state.cards[discard.object_id]))

    def test_decline_and_unpayable_combination_preserve_both_resources(self):
        for available,paid_card in ((True,True),(False,True),(True,False)):
            s=self.session(804020+available+2*paid_card);self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
            land=self.add(s.engine,'Mountain') if available else None
            card=self.add(s.engine,'Sol Ring',zone='hand') if paid_card else None
            if not paid_card:
                for object_id in tuple(s.state.players['A'].zones['hand']):s.engine.move_card(object_id,'library',log=False)
            drawn=self.top(s,'Sol Ring','declined-top');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s);self.checkpoint(s);pending=self.attack_choice(s,body)
            self.assertEqual(available and paid_card,s.packet('pilot:A',full=True)['decision']['ctx']['payable'])
            if not available and card is not None:self.reject(s,{'action_id':'choose','cards':[card.ref]})
            s=self.replay(s,load=True);self.choose(s,cards=[]);self.assertIsNone(self.resolve(s))
            if land:self.assertFalse(s.state.cards[land.object_id].tapped)
            if card:self.assertEqual('hand',s.state.cards[card.object_id].zone)
            self.assertEqual('library',s.state.cards[drawn.object_id].zone);self.private_replay(s,private)

    def test_partial_foreign_duplicate_and_unknown_payment_responses_roll_back(self):
        s=self.session(804030);self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
        land=self.add(s.engine,'Mountain');card=self.add(s.engine,'Sol Ring',zone='hand')
        foreign=self.add(s.engine,'Generic Bound Growth',seat='B',zone='hand',ref='foreign-payment-card');drawn=self.top(s,'Sol Ring','invalid-top')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);self.attack_choice(s,body)
        for response in ({'pay':True},{'cards':[foreign.ref]},{'cards':[card.ref,card.ref]},
                {'cards':[card.ref],'pay':False},{'cards':[card.ref],'payment':{'C':0}},
                {'cards':[card.ref],'pay':1}):
            self.reject(s,{'action_id':'choose',**response})
            self.assertFalse(s.state.cards[land.object_id].tapped);self.assertEqual('hand',s.state.cards[card.object_id].zone)
            self.assertEqual('library',s.state.cards[drawn.object_id].zone)
        s=self.replay(s,load=True);self.choose(s,cards=[card.ref]);self.assertIsNone(self.resolve(s));self.private_replay(s,private)

    def test_actual_surveil_qualifies_creature_but_not_old_or_foreign_graveyard_cards(self):
        s=self.session(804040);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Consider',zone='hand')
        creature=self.top(s,'Generic Bound Body','surveilled-creature');old=self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='old-creature')
        foreign=self.add(s.engine,'Generic Bound Body',seat='B',zone='graveyard',ref='foreign-creature')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':1,'W':1});self.checkpoint(s);self.cast(s,'A',spell)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        s=self.replay(s,load=True);self.choose(s,cards={'top':[],'graveyard':[creature.ref]});self.assertIsNone(self.resolve(s))
        self.assertEqual([],self.grants(s,old));self.assertEqual([],self.grants(s,foreign))
        options=self.grants(s,creature);self.assertEqual(1,len(options));self.use(s,creature,options[0],{'W':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[creature.object_id].zone)
        self.private_replay(s,private)

    def test_actual_countered_creature_qualifies_but_battlefield_death_does_not(self):
        s=self.session(804050);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Generic Bound Body',zone='hand',ref='countered-body')
        corpse=self.add(s.engine,'Generic Bound Body',ref='battlefield-body');counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        kill=self.add(s.engine,'Doom Blade',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'W':2,'B':1,'C':1});self.checkpoint(s)
        normal=next((o for o in self.options(s,spell) if o['id']=='normal'),None);self.assertIsNotNone(normal)
        self.use(s,spell,normal,{'W':1});self.assertTrue(s.state.stack)
        self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(1,len(self.grants(s,spell)))
        kill_option=next((o for o in self.options(s,kill) if o['id']=='normal'),None);self.assertIsNotNone(kill_option)
        self.use(s,kill,kill_option,{'C':1,'B':1},[corpse.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[corpse.object_id].zone);self.assertEqual([],self.grants(s,corpse))
        options=self.grants(s,spell);self.assertEqual(1,len(options));self.use(s,spell,options[0],{'W':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.private_replay(s,private)

    def test_madness_discard_pays_once_and_draws_before_its_separate_trigger(self):
        s=self.session(804060);self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
        land=self.add(s.engine,'Mountain');card=self.add(s.engine,'Fiery Temper',zone='hand')
        drawn=self.top(s,'Sol Ring','madness-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);self.attack_choice(s,body);self.choose(s,cards=[card.ref])
        self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual('exile',s.state.cards[card.object_id].zone)
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
        self.assertEqual('priority',s.state.pending_decision.kind)
        s=self.replay(s,load=True)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(1,sum(e.code=='effect.optional_mana.paid' for e in s.state.events))
        self.private_replay(s,private)

    def test_competing_discard_replacements_resume_paid_mana_and_draw_once(self):
        s=self.session(804061);self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
        land=self.add(s.engine,'Mountain');card=self.add(s.engine,'Sol Ring',zone='hand')
        replacements=[self.add(s.engine,name,seat=seat) for name,seat in
            (('Generic Payment Destination Replacement','B'),('Generic Payment Competing Replacement','C'))]
        drawn=self.top(s,'Sol Ring','replacement-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);self.attack_choice(s,body);self.choose(s,cards=[card.ref])
        self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual('hand',s.state.cards[card.object_id].zone);self.assertEqual('library',s.state.cards[drawn.object_id].zone)
        self.assertIn('replacement',s.state.pending_decision.kind)
        for replacement in replacements:self.assertTrue(self.binding(s,replacement)['strict_capability_ready'])
        from quorune.replacement.replay import ReplacementContinuation
        from quorune.replacement.model import ReplacementEffectError
        wire=s.state.pending_decision.continuation;decoded=ReplacementContinuation.from_dict(wire)
        self.assertIsNotNone(decoded.completed_choice)
        self.assertEqual(1,len(wire['completed_choice']['intents']))
        for changes in ({'completed_choice':None},{'intent_index':1},
                {'completed_choice':{**wire['completed_choice'],'actor':'B'}},
                {'completed_choice':{**wire['completed_choice'],'response':{'cards':[]}}}):
            with self.assertRaises(ReplacementEffectError):ReplacementContinuation.from_dict({**wire,**changes})
        legacy={k:v for k,v in wire.items() if k!='completed_choice'}
        self.assertIsNone(ReplacementContinuation.from_dict(legacy).completed_choice)
        s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];self.assertTrue(options)
        self.choose(s,replacement=options[0]['id'])
        self.assertEqual('exile',s.state.cards[card.object_id].zone);self.assertEqual(1,sum(s.state.cards[card.object_id].counters.values()))
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
        self.assertEqual(1,sum(e.code=='effect.optional_mana.paid' for e in s.state.events));self.private_replay(s,private)

    def test_next_extra_turn_expires_prior_arrival_without_inventing_history(self):
        s=self.session(804070);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Consider',zone='hand')
        creature=self.top(s,'Generic Bound Body','prior-turn-creature');warp=self.add(s.engine,'Time Warp',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'U':3});self.checkpoint(s);self.cast(s,'A',spell)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.choose(s,cards={'top':[],'graveyard':[creature.ref]});self.assertIsNone(self.resolve(s))
        current=s.state.cards[creature.object_id];self.assertTrue(s.engine._compiled_zone_cast_permission('A',current))
        from quorune.model import TurnHistory
        from quorune.turn_history import current_graveyard_incarnation_qualifies
        legacy=s.state.turn_history.to_dict();legacy.pop('arrival_history_version')
        old=TurnHistory.from_dict(legacy);self.assertNotIn('arrival_history_version',old.to_dict())
        self.assertFalse(current_graveyard_incarnation_qualifies(old,turn_sequence=s.state.turn_sequence,card=current,qualification='nonbattlefield_arrival_this_turn'))
        option=next((o for o in self.options(s,warp) if o['id']=='normal'),None);self.assertIsNotNone(option)
        turn=s.state.turn_sequence;self.use(s,warp,option,{'C':3,'U':2},['A']);self.assertIsNone(self.resolve(s))
        self.advance(s,lambda q:q.state.turn_sequence>turn and q.state.active_player=='A' and q.state.phase=='precombat_main')
        self.assertFalse(s.engine._compiled_zone_cast_permission('A',s.state.cards[creature.object_id]));self.private_replay(s,private)

    def test_actual_ability_loss_removes_both_grant_and_player_attack_result(self):
        s=self.session(804080);source=self.add(s.engine,NAMES[0]);body=self.add(s.engine,'Generic Bound Body')
        spell=self.add(s.engine,'Consider',zone='hand');gift=self.add(s.engine,'Gift of Tusks',zone='hand')
        creature=self.top(s,'Generic Bound Body','loss-qualifier');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':2});self.checkpoint(s);self.cast(s,'A',spell)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.choose(s,cards={'top':[],'graveyard':[creature.ref]});self.assertIsNone(self.resolve(s))
        self.assertTrue(s.engine._compiled_zone_cast_permission('A',s.state.cards[creature.object_id]))
        self.cast(s,'A',gift,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.engine._compiled_zone_cast_permission('A',s.state.cards[creature.object_id]))
        self.advance(s,lambda q:q.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:A',{'action_id':'attack','attackers':{body.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(s.state.stack);self.private_replay(s,private)

    def test_combined_payer_and_qualification_shapes_reject_unknown_ownership(self):
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
        from quorune.fixed_effect_payment import compound_payment_payload_dependencies
        from quorune.compiler.turn_cast_grants import turn_cast_grant_handler
        spec=fixed_effect_payment_spec('pay {1} and discard a card');self.assertIsNotNone(spec)
        effect={'op':'offer_optional_mana_payment','player':'$controller','payment':spec.to_dict()}
        self.assertIn(PAYMENT,compound_payment_payload_dependencies(effect))
        foreign=replace(spec,predicate=replace(spec.predicate,owner='B'))
        with self.assertRaises(ValueError):compound_payment_payload_dependencies({**effect,'payment':foreign.to_dict()})
        text=self.db.lookup(NAMES[0]).oracle_text.splitlines()[0]
        self.assertIsNotNone(turn_cast_grant_handler(text))
        for bad in (text.replace('this turn','this game'),text.replace('other than the battlefield','other than the library'),text+' You gain 2 life.'):
            self.assertIsNone(turn_cast_grant_handler(bad),bad)

    def test_verified_capability_withdrawal_and_fresh_cost_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for capability in (PAYMENT,ARRIVAL):
            raw=deepcopy(baseline);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['withdrawn combined payment contract'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_choices import fixed_effect_payment as owner
        method='test_actual_player_attack_pays_both_costs_draws_once_and_replays_private_choice'
        original=owner._complete_mana_discard_cost
        for keep in ('mana','discard'):
            def omit(spec,effect,response,query,keep=keep):
                completion=original(spec,effect,response,query)
                return replace(completion,intents=completion.intents[:1] if keep=='mana' else completion.intents[1:])
            with patch.object(owner,'_complete_mana_discard_cost',side_effect=omit):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import turn_history
        method='test_actual_countered_creature_qualifies_but_battlefield_death_does_not'
        with patch.object(turn_history,'current_graveyard_incarnation_qualifies',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.semantic_choices import counter_coordination
        issue=counter_coordination._issue_semantic_intent_replacement_choice
        def omit_paid_prefix(host,**kwargs):
            kwargs['completed_choice']=None;return issue(host,**kwargs)
        method='test_competing_discard_replacements_resume_paid_mana_and_draw_once'
        with patch.object(counter_coordination,'_issue_semantic_intent_replacement_choice',side_effect=omit_paid_prefix):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

"""CR602.2b/605.3: a filtering mana ability must pay its own mana cost."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition,DeckEntry
from quorune.errors import GameRuleError
from quorune.mana_activation import complete_mana_plan_activations
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zones


class PricedManaSourceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zones.SelfPublicZoneRuntimeTests.prepare
    cast=zones.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'priced-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/priced-mana-source-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Priced mana',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,
            capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding)

    def private_replay(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def hybrid_input(self, session, source, color):
        ability, = [ability for ability in session.engine._activated_abilities(source) if ability.mana_cost_options]
        option, = [option for option in ability.mana_cost_options if option.requirements[color]]
        return {"ability_id": ability.ability_id, "cost_option": option.option_id, "payment": {color: 1}}

    def test_hybrid_filter_chooses_input_and_output_independently_and_consumes_undo_history(self):
        session = self.session(820010)
        source = self.add(session.engine, "Fetid Heath", ref="hybrid-explicit")
        plains = self.add(session.engine, "Generic Bound Plains", ref="hybrid-seed")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        seed, = [row["id"] for row in rows if row["id"].startswith("activate:" + plains.ref + ":")]
        result = session.act("pilot:A", {"action_id": seed})
        self.assertTrue(result.ok, result.summary)
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(row.get("action") == "undo_mana" for row in rows))
        cost = self.hybrid_input(session, source, "W")
        action = "activate:" + source.ref + ":" + cost["ability_id"]
        self.assertIn(action, {row["id"] for row in rows})
        command = {"action_id": action, "cost_option": cost["cost_option"], "mana_output": {"B": 2}, "pay": "auto"}
        before = authoritative_state_hash(session.state)
        for principal, submitted in (("pilot:D", command), ("pilot:A", {**command, "mana_output": {"G": 2}})):
            self.assertFalse(session.act(principal, submitted).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertFalse(session.state.stack)
        self.assertEqual({"B": 2}, {key: value for key, value in session.state.players["A"].mana_pool.items() if value})
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row.get("action") == "undo_mana" for row in rows))
        self.private_replay(session, private)

    def test_auto_and_explicit_plans_chain_filters_and_reject_a_bad_later_input_atomically(self):
        session = self.session(820011)
        plains = self.add(session.engine, "Generic Bound Plains", ref="chain-seed")
        first = self.add(session.engine, "Fetid Heath", ref="chain-first")
        second = self.add(session.engine, "Twilight Mire", ref="chain-second")
        spell = self.add(session.engine, "Elvish Warrior", zone="hand", ref="chain-spell")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        for card in (first, second, spell):
            self.assert_closed(session, card)
        self.prepare(session)
        self.checkpoint(session)
        manual = self.replay(session, load=True)
        action = "cast:" + spell.ref
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in offered})

        first_input = self.hybrid_input(manual, first, "W")
        second_input = self.hybrid_input(manual, second, "B")
        prefix = [{"source": plains.ref, "bundle": {"W": 1}},
                  {"source": first.ref, "bundle": {"B": 2}, "input": first_input}]
        final = {"source": second.ref, "bundle": {"G": 2}, "input": second_input}
        command = {"action_id": action, "pay": "manual", "payment": {"G": 2}}
        before = authoritative_state_hash(manual.state)
        for invalid in ({key: value for key, value in final.items() if key != "input"},
                        {**final, "input": {**second_input, "payment": {"W": 1}}}):
            result = manual.act("pilot:A", {**command, "mana": [*prefix, invalid]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(manual.state))

        for game, accepted in ((manual, {**command, "mana": [*prefix, final]}),
                               (session, {"action_id": action, "pay": "auto"})):
            result = game.act("pilot:A", accepted)
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (plains, first, second)))
            self.assertEqual([plains.ref, first.ref, second.ref], [
                event.details["source"] for event in game.state.events if event.code == "mana.produce"
            ])
            self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
            self.assertEqual(0, game.state.players["A"].mana_pool["G"])
            game = self.private_replay(game, private)
            self.assertIsNone(self.resolve(game))
            self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
            self.private_replay(game, private)

    def test_unseeded_filters_cannot_fund_each_other_or_their_own_tap(self):
        session = self.session(820012)
        first = self.add(session.engine, "Fetid Heath", ref="unseeded-first")
        second = self.add(session.engine, "Twilight Mire", ref="unseeded-second")
        spell = self.add(session.engine, "Elvish Warrior", zone="hand", ref="unseeded-spell")
        self.prepare(session)
        self.checkpoint(session)
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ids = {row["id"] for row in rows}
        self.assertNotIn("cast:" + spell.ref, ids)
        before = authoritative_state_hash(session.state)
        for source, color in ((first, "W"), (second, "B")):
            price = self.hybrid_input(session, source, color)
            self.assertEqual(1, sum(value.startswith("activate:" + source.ref + ":") for value in ids))
            command = {"action_id": "activate:" + source.ref + ":" + price["ability_id"],
                       "cost_option": price["cost_option"], "mana_output": {color: 2}, "pay": "auto"}
            self.assertFalse(session.act("pilot:A", command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

    def test_creature_spell_only_mana_cannot_seed_a_filter_for_that_spell(self):
        session = self.session(820013)
        producer = self.add(session.engine, "Ancient Ziggurat", ref="restricted-seed")
        source = self.add(session.engine, "Twilight Mire", ref="restricted-filter")
        spell = self.add(session.engine, "Elvish Warrior", zone="hand", ref="restricted-spell")
        self.assert_closed(session, producer)
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        seed, = [row["id"] for row in rows if row["id"].startswith("activate:" + producer.ref + ":")]
        result = session.act("pilot:A", {"action_id": seed, "mana_choice": "B"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.engine._restricted_mana("A")["creature_spell_only"]["B"])
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + spell.ref, {row["id"] for row in rows})
        before = authoritative_state_hash(session.state)
        cost = self.hybrid_input(session, source, "B")
        command = {"action_id": "cast:" + spell.ref, "pay": "manual", "payment": {"G": 2},
                   "mana": [{"source": source.ref, "bundle": {"G": 2}, "input": cost}]}
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)

    def test_resolving_optional_payment_can_use_the_same_filter_chain(self):
        session = self.session(820014)
        plains = self.add(session.engine, "Generic Bound Plains", ref="resolution-seed")
        first = self.add(session.engine, "Fetid Heath", ref="resolution-first")
        second = self.add(session.engine, "Twilight Mire", ref="resolution-second")
        artifact = self.add(session.engine, "Lifecrafter's Bestiary", ref="resolution-payment-source")
        creature = self.add(session.engine, "Phyrexian Walker", zone="hand", ref="resolution-creature")
        for card in (first, second, artifact, creature):
            self.assert_closed(session, card)
        self.prepare(session)
        self.checkpoint(session)
        before = len(session.state.players["A"].zones["hand"])
        self.cast(session, "A", creature)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertFalse(any(session.state.cards[card.object_id].tapped for card in (plains, first, second)))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in (plains, first, second)))
        self.assertEqual(before, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("battlefield", session.state.cards[creature.object_id].zone)
        self.replay(session, load=True)

    def filtering_fixture(self,name,seed,*,mana=None):
        session=self.session(seed);source=self.add(session.engine,name)
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='foreign-private')
        self.prepare(session,mana=mana);self.checkpoint(session)
        self.assert_closed(session,source);self.assert_closed(session,spell)
        return session,source,spell,target,private

    def assert_unfunded_filter_rejected(self,name,seed):
        session,source,spell,target,private=self.filtering_fixture(name,seed)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']=='cast:'+spell.ref for row in actions))
        before=authoritative_state_hash(session.state)
        command={'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'}
        for principal in ('pilot:D','pilot:A'):
            result=session.act(principal,command)
            self.assertFalse(result.ok,result.summary)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual('hand',session.state.cards[spell.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.private_replay(session,private)

    def test_original_prism_cannot_filter_green_from_zero_mana_for_an_automatic_cast(self):
        self.assert_unfunded_filter_rejected('Mana Prism',71402001)

    def test_original_filter_lands_do_not_skip_their_mana_price(self):
        for i,name in enumerate(('Shimmering Grotto','Unknown Shores')):
            self.assert_unfunded_filter_rejected(name,71402010+i)

    def test_free_colorless_mode_of_each_original_remains_available_for_automatic_payment(self):
        for i,name in enumerate(('Mana Prism','Shimmering Grotto','Unknown Shores')):
            session=self.session(71402020+i);source=self.add(session.engine,name)
            spell=self.add(session.engine,'Sol Ring',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session);self.checkpoint(session)
            self.assert_closed(session,source);self.assert_closed(session,spell)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            offered=next(row for row in actions if row['id']=='cast:'+spell.ref)
            result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_prism_can_pay_its_filter_cost_explicitly_then_cast_and_replay(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402030,mana={'C':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':')
            and row.get('cost_summary',{}).get('m',{}).get('GENERIC')==1)
        before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'mana_choice':'G','pay':'auto'}
        self.assertFalse(session.act('pilot:D',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual((0,1),tuple(session.state.players['A'].mana_pool[c] for c in ('C','G')))
        session=self.replay(session,load=True)
        self.cast(session,'A',spell,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(4,session.engine._numeric_stat(target.object_id,'power'))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_prism_cannot_tap_for_its_own_filter_payment(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402031)
        abilities=session.engine._activated_abilities(source)
        priced=next(a for a in abilities if a.mana.get('GENERIC')==1)
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'activate:'+source.ref+':'+priced.ability_id,
            'mana_choice':'G','pay':'auto'})
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_prepared_filter_plan_rejects_a_paid_mode_before_any_write(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402032)
        before=authoritative_state_hash(session.state)
        with self.assertRaises(GameRuleError):
            complete_mana_plan_activations(session.engine,'A',[{'source':source.ref,'bundle':{'G':1}}])
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_omitting_the_price_guard_is_killed_by_a_fresh_native_cast_witness(self):
        method='test_original_prism_cannot_filter_green_from_zero_mana_for_an_automatic_cast'
        with patch('quorune.mana_source_discovery._mana_plan_cost_is_supported',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


class OrderedHybridManaPlannerTests(unittest.TestCase):
    def test_filter_input_uses_its_own_spending_context_and_consumes_snow_provenance(self):
        from quorune.activation_mana_cost import ActivationManaCostOption
        from quorune.mana import ManaMode, ManaModePrice, ManaPlanError, ManaSource, auto_plan_payment
        from quorune.mana_planning import ManaPlanFunding
        from quorune.mana_provenance import PlanningManaPool, add_mana
        from quorune.replacement.immutable import FrozenMap

        price = ManaModePrice("ab2", ActivationManaCostOption("black", FrozenMap({"B": 1})), "activation", None)
        filter_source = ManaSource("filter", "F", "Filter", (ManaMode({"G": 2}, price=price),))
        def allows(restriction, context):
            return (restriction, context) == ("activation-only", "activation")

        pool = PlanningManaPool.capture(SimpleNamespace(mana_pool={}, stats={}))
        add_mana(pool, {"B": 1}, snow=True, restriction="activation-only")
        funding = ManaPlanFunding(pool, allows, "spell")
        plan = auto_plan_payment({"G": 2}, [filter_source], funding=funding)
        self.assertEqual({"B": 1}, plan.activations[0]["input"]["payment"])
        self.assertEqual(2, plan.payment["G"])
        self.assertEqual(1, pool.mana_pool["B"])
        intermediate = ManaSource("seed", "S", "Seed", (
            ManaMode({"B": 1}, restriction="activation-only", conditional=True, intermediate_only=True),
        ))
        unseeded = ManaPlanFunding(PlanningManaPool.capture(SimpleNamespace(mana_pool={}, stats={})), allows, "spell")
        plan = auto_plan_payment({"G": 2}, [filter_source, intermediate], funding=unseeded)
        self.assertEqual(["seed", "filter"], [step["source"] for step in plan.activations])
        with self.assertRaises(ManaPlanError):
            auto_plan_payment({"G": 1}, [filter_source], funding=funding, snow_required=1)
        add_mana(pool, {"C": 1}, snow=True)
        plan = auto_plan_payment({"G": 1}, [filter_source], funding=funding, snow_required=1,
                                 starting_pool={"C": 1}, starting_snow_pool={"C": 1})
        self.assertEqual(1, plan.snow_payment["C"])

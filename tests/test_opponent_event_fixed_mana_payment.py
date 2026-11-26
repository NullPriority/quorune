"""Original opposing payers and controller-owned unpaid results through real actions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_event_mana_payment import (
    OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY, opponent_event_mana_payment_program_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class OpponentEventFixedManaPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-event-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-event-fixed-mana-payment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opponent mana payment", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, actor="B", active="B", mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=actor, active=active, mana=mana)
        self.checkpoint(game)

    def act_ok(self, game, seat, command):
        outcome = game.act("pilot:" + seat, command)
        self.assertTrue(outcome.ok, outcome.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def pay_offer(self, game, actor, amount, payable=True):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [actor]), (decision.kind, decision.actors))
        context = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]
        expected = {"GENERIC": amount} if amount else {}
        self.assertEqual(expected, {key: value for key, value in context["cost"].items() if value})
        self.assertEqual(payable, context["payable"])
        action, = (row for row in decision.payload_by_actor[actor]["legal_actions"] if row["id"] == "choose")
        self.assertEqual([True, False] if payable else [False], list(action["choice_schema"]["legal_values"]))
        for seat in set("ABCD") - {actor}:
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])

    def optional_draw(self, game):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        action, = (row for row in decision.payload_by_actor["A"]["legal_actions"] if row["id"] == "choose")
        self.assertEqual(["draw", "decline"], list(action["choice_schema"]["legal_values"]))

    def test_esper_sentinel_seals_the_first_opposing_noncreature_and_unpaid_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.opponent_event_mana_payment import OPPONENT_SOURCE_POWER_MANA_CAPABILITY

        card = self.db.lookup("Esper Sentinel", fuzzy=False)
        compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertIsNone(node.target_schema)
        effect, = node.effects
        self.assertEqual("draw", effect["declined_effects"][0]["op"])
        self.assertIn(OPPONENT_SOURCE_POWER_MANA_CAPABILITY, node.capability_dependencies)
        binding, registry = register_card(self.db, self.registry, "Esper Sentinel")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = (program for program in registry.programs_for_oracle(card.oracle_id)
                    if program.event == "spell.cast")
        stripped = replace(trigger, event_condition={"field": "controller", "op": "ne", "value": "$source.controller"})
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        for old, proposed in (
            ("first noncreature", "second noncreature"),
            ("first noncreature", "first creature"),
            ("this creature's power", "this creature's toughness"),
            ("draw a card unless", "you may draw a card unless"),
        ):
            with self.subTest(old=old, proposed=proposed):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, proposed),
                    faces=tuple({**face, "oracle_text": face["oracle_text"].replace(old, proposed)}
                                for face in card.faces))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                    capability_profile=PROFILE).status)
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"]==OPPONENT_SOURCE_POWER_MANA_CAPABILITY).update(
            status="blocked", blockers=["Source-power price owner withdrawn"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=withdrawn,
            capability_profile=PROFILE).status)
        rhystic = self.db.lookup("Rhystic Study", fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(rhystic, capability_registry=withdrawn,
            capability_profile=PROFILE).status)

    def _boosted_esper(self, seed, *, return_source):
        game = self.session(seed)
        source = self.add(game.engine, "Esper Sentinel")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        buff = self.add(game.engine, "Giant Growth", zone="hand")
        self.add(game.engine, "Forest", ref="esper-controller-forest")
        blink = None
        if return_source:
            blink = self.add(game.engine,"Cloudshift",zone="hand")
            self.add(game.engine,"Plains",ref="esper-controller-plains")
        self.start(game, source, bear, shock, buff, *((blink,) if blink else ()),
                   mana={"R": 1, "G": 1, "C": 5})
        self.cast(game, "B", bear)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", shock, ["A"])
        self.assertEqual(1,self.trigger_count(game,source))
        self.cast(game,"A",buff,[source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(4,game.engine._numeric_stat(current.object_id,"power"))
        if blink:
            original = current.logical_object_id
            self.cast(game,"A",blink,[current.ref])
            self.resolve_top(game)
            current = game.state.cards[source.object_id]
            self.assertEqual("battlefield",current.zone)
            self.assertNotEqual(original,current.logical_object_id)
            self.assertEqual(1,game.engine._numeric_stat(current.object_id,"power"))
        self.pay_offer(game, "B", 4)
        return game

    def test_actual_esper_quotes_current_power_or_the_departed_original_and_draws_mandatorily(self):
        for return_source in (False,True):
            with self.subTest(return_source=return_source):
                game = self._boosted_esper(10660130+int(return_source),return_source=return_source)
                before = len(game.state.players["A"].zones["hand"])
                top = game.state.cards[game.state.players["A"].zones["library"][-1]]
                command = {"action_id":"choose","pay":not return_source}
                self.reject(game,"A",command)
                self.reject(game,"C",command)
                game = self.replay(game,load=True)
                self.act_ok(game,"B",command)
                self.assertEqual(before+int(return_source),len(game.state.players["A"].zones["hand"]))
                self.assertEqual(return_source,top.object_id in game.state.players["A"].zones["hand"])
                if return_source:
                    ref = game.state.cards[top.object_id].ref
                    for seat in "BCD":
                        self.assertNotIn(json.dumps(ref),json.dumps(game.packet(f"pilot:{seat}",full=True)["state"]))
                self.assertIsNone(self.resolve(game))
                self.replay(game,load=True)

    def test_actual_negative_esper_power_can_be_paid_or_declined_at_zero(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                game=self.session(10660140+int(paid))
                source=self.add(game.engine,"Esper Sentinel")
                shock=self.add(game.engine,"Shock",seat="B",zone="hand")
                shrink=self.add(game.engine,"Shrink",zone="hand")
                self.add(game.engine,"Forest",ref="esper-negative-forest")
                self.start(game,source,shock,shrink,mana={"R":1})
                self.cast(game,"B",shock,["A"])
                self.cast(game,"A",shrink,[source.ref])
                self.resolve_top(game)
                self.assertLess(game.engine._numeric_stat(source.object_id,"power"),0)
                self.pay_offer(game,"B",0,True)
                before=len(game.state.players["A"].zones["hand"])
                game=self.replay(game,load=True)
                self.act_ok(game,"B",{"action_id":"choose","pay":paid})
                self.assertEqual(before+int(not paid),len(game.state.players["A"].zones["hand"]))
                self.assertFalse(any(game.state.players["B"].mana_pool.values()))
                self.assertIsNone(self.resolve(game))
                self.replay(game,load=True)

    def test_actual_esper_reads_prior_casts_and_separates_opponents_in_one_turn(self):
        game=self.session(10660149)
        source=self.add(game.engine,"Esper Sentinel",zone="hand")
        earlier=self.add(game.engine,"Shock",seat="B",zone="hand",ref="esper-earlier")
        later=self.add(game.engine,"Shock",seat="B",zone="hand",ref="esper-later")
        first_c=self.add(game.engine,"Shock",seat="C",zone="hand",ref="esper-first-c")
        game.state.players["B"].mana_pool["R"]=2
        game.state.players["C"].mana_pool["R"]=1
        self.start(game,source,earlier,later,first_c,actor="A",active="A",mana={"W":1})
        self.cast(game,"A",source)
        self.cast(game,"B",earlier,["D"])
        self.assertEqual(0,self.trigger_count(game,source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield",game.state.cards[source.object_id].zone)
        self.cast(game,"B",later,["D"])
        self.assertEqual(0,self.trigger_count(game,source))
        self.assertIsNone(self.resolve(game))
        self.cast(game,"C",first_c,["D"])
        self.pay_offer(game,"C",1,False)
        before=len(game.state.players["A"].zones["hand"])
        self.reject(game,"C",{"action_id":"choose","pay":True})
        self.act_ok(game,"C",{"action_id":"choose","pay":False})
        self.assertEqual(before+1,len(game.state.players["A"].zones["hand"]))
        self.assertIsNone(self.resolve(game))
        self.replay(game,load=True)

    def test_esper_price_mutation_at_discovery_is_detected_by_actual_response(self):
        with patch("quorune.scalar_effect_amounts.resolve_scalar_effect_amount",return_value=1), self.assertRaisesRegex(
            AssertionError,"GENERIC"
        ):
            self._boosted_esper(10660160,return_source=True)

    def test_actual_rhystic_payer_precedes_controller_draw_and_pays_with_a_mana_ability(self):
        for seed, paid in ((61520101, True), (61520102, False)):
            with self.subTest(paid=paid):
                game = self.session(seed)
                source = self.add(game.engine, "Rhystic Study")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                land = self.add(game.engine, "Plains", seat="B")
                draw_size = len(game.state.players["A"].zones["hand"])
                top = game.state.cards[game.state.players["A"].zones["library"][-1]]
                life = game.state.players["A"].life
                counter = None
                if paid:
                    counter = self.add(game.engine, "Counterspell", zone="hand")
                    self.add(game.engine, "Island", ref="rhystic-island-one")
                    self.add(game.engine, "Island", ref="rhystic-island-two")
                self.start(game, source, shock, *((counter,) if counter else ()), mana={"R": 1})
                self.cast(game, "B", shock, ["A"])
                if counter:
                    culprit, = (item for item in game.state.stack if item.card_object_id == shock.object_id)
                    self.cast(game, "A", counter, [culprit.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[shock.object_id].zone)
                self.pay_offer(game, "B", 1)
                self.assertEqual(life, game.state.players["A"].life)
                self.assertEqual(draw_size, len(game.state.players["A"].zones["hand"]))
                command = {"action_id": "choose", "pay": paid}
                self.reject(game, "A", command)
                game = self.replay(game, load=True)
                self.act_ok(game, "B", command)
                self.assertEqual(paid, game.state.cards[land.object_id].tapped)
                if not paid:
                    self.optional_draw(game)
                    self.reject(game, "B", {"action_id": "choose", "choice": "draw"})
                    self.act_ok(game, "A", {"action_id": "choose", "choice": "draw"})
                    self.assertIn(top.object_id, game.state.players["A"].zones["hand"])
                    for seat in "BCD":
                        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life - 2 * int(not paid), game.state.players["A"].life)
                self.assertEqual(draw_size + int(not paid), len(game.state.players["A"].zones["hand"]))
                self.replay(game, load=True)

    def test_actual_departed_caster_cannot_pay_and_the_original_draw_choice_survives(self):
        game = self.session(61520103)
        source = self.add(game.engine, "Rhystic Study")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.start(game, source, shock, mana={"R": 1})
        self.cast(game, "B", shock, ["A"])
        self.assertEqual(1, self.trigger_count(game, source))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        concede, = (row for row in actions if row["id"] == "concede")
        self.act_ok(game, "B", {"action_id": concede["id"], "choices": {"confirm_concede": True}})
        self.assertFalse(game.state.players["B"].in_game)
        self.optional_draw(game)
        self.act_ok(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_remora_ignores_creature_casts_and_only_offers_the_printed_opponent_price(self):
        game = self.session(61520104)
        source = self.add(game.engine, "Mystic Remora")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        self.start(game, source, bear, shock, mana={"G": 1, "C": 1})
        self.cast(game, "B", bear)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", shock, ["A"])
        self.pay_offer(game, "B", 4, payable=False)
        self.reject(game, "B", {"action_id": "choose", "pay": True})
        self.act_ok(game, "B", {"action_id": "choose", "pay": False})
        self.optional_draw(game)
        self.act_ok(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_archived_remora_tax_record_resumes_v1_without_current_overlay(self):
        game = self.session(61520107)
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.register_fixture_cards_on_add = False
        try:
            source = self.add(game.engine, "Mystic Remora")
        finally:
            self.register_fixture_cards_on_add = True
        archived = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual(1, sum(any(effect.get("op") == "remora_tax" for effect in program.effects)
                                for program in archived))
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "B", shock, ["A"])
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
        self.assertEqual("choice.payment.remora-tax.v1", decision.continuation["handler_id"])
        game = self.replay(game, load=True)
        self.act_ok(game, "B", {"action_id": "choose", "pay": False})
        self.optional_draw(game)
        self.act_ok(game, "A", {"action_id": "choose", "choice": "draw"})
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_remora_preserves_its_separate_cumulative_upkeep(self):
        game = self.session(61520105)
        source = self.add(game.engine, "Mystic Remora")
        land = self.add(game.engine, "Plains")
        order = game.state.turn_order
        previous = order[order.index("A") - 1]
        for object_id in tuple(game.state.players[previous].zones["hand"][7:]):
            game.engine.move_card(object_id, "library", reason="Prepare the prior player's starting hand")
        game.state.last_normal_turn_player = previous
        self.start(game, source, actor=previous, active=previous)
        self.until(game, lambda session: session.state.active_player == "A" and session.state.step == "upkeep")
        self.assertEqual(1, self.trigger_count(game, source))
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("age"))
        self.act_ok(game, "A", {"action_id": "choose", "pay": True})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_tithe_waits_for_both_draws_then_creates_one_treasure_for_the_unpaid_trigger(self):
        game = self.session(61520106)
        source = self.add(game.engine, "Smothering Tithe")
        draw = self.add(game.engine, "Divination", seat="B", zone="hand")
        before = len(game.state.players["B"].zones["hand"])
        self.start(game, source, draw, mana={"U": 1, "C": 5})
        self.cast(game, "B", draw)
        decision = self.resolve(game)
        self.assertEqual(before + 1, len(game.state.players["B"].zones["hand"]))
        if decision.kind == "trigger.order":
            group = next(group for group in game.state.pending_trigger_batches[0].groups if group.controller == "A")
            self.assertEqual(2, len(group.items))
            self.act_ok(game, "A", {"action_id": "order", "triggers": [item.ref for item in group.items]})
        self.pay_offer(game, "B", 2)
        self.act_ok(game, "B", {"action_id": "choose", "pay": True})
        self.pay_offer(game, "B", 2, payable=False)
        game = self.replay(game, load=True)
        self.act_ok(game, "B", {"action_id": "choose", "pay": False})
        self.assertIsNone(self.resolve(game))
        token, = (card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.assertEqual(("Treasure Token", "A"), (token.printed_name, token.controller))
        self.replay(game, load=True)

    def test_three_whole_originals_preserve_their_scoped_payer_and_independent_authority(self):
        cases = (
            ("Mystic Remora", register_card(self.db, self.registry, "Mystic Remora")),
            ("Rhystic Study", register_card(self.db, self.registry, "Rhystic Study")),
            ("Smothering Tithe", register_card(self.db, self.registry, "Smothering Tithe")),
        )
        for name, (report, semantics) in cases:
            with self.subTest(name=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                card = self.db.lookup(name, fuzzy=False)
                program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                            if OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY in p.capability_dependencies)
                effect, = program.effects
                self.assertIsNone(program.target_schema)
                self.assertIn(OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY,
                              opponent_event_mana_payment_program_dependencies(program, effect))
                deps = tuple(cap for cap in program.capability_dependencies if cap != OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                runtime = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(runtime["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{OPPONENT_EVENT_MANA_PAYMENT_CAPABILITY}",
                              runtime["blockers"])
        source = self.db.lookup("Rhystic Study", fuzzy=False)
        for before, after in (("an opponent casts", "you cast"), ("{1}", "{X}"),
                              ("pays {1}.", "pays {1}. Change the name of each card in your hand.")):
            with self.subTest(after=after):
                changed = replace(source, oracle_text=source.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.trigger_result_templates.opponent_event_mana_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                source, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.commander_cast_counts import controller_commander_cast_count
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import semantic_preflight
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.commander_return_choice import COMMANDER_RETURN_CAPABILITY, commander_return_effect
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_reflexive_payments as witnesses


PRINTED = "{T}, Sacrifice this land: Put your commander into your hand from the command zone."
PARTNERS = ("Beacon Partner Alpha", "Beacon Partner Beta")
PROFILE = "commander_review"


class CommandZoneReturnCompilerTests(unittest.TestCase):
    def test_compiler_requires_the_complete_paid_nontargeted_commander_return(self):
        registry = load_default_capability_registry()
        card = replace(record(PRINTED), type_line="Land", mana_cost="", mana_value=0.0)
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertEqual((commander_return_effect(),), node.effects)
        self.assertIn(COMMANDER_RETURN_CAPABILITY, node.capability_dependencies)
        for changed in (
            PRINTED.replace("{T}, Sacrifice this land", "{T}"),
            PRINTED.replace("your commander", "target commander"),
            PRINTED.replace("your commander", "an opponent's commander"),
            PRINTED.replace("from the command zone", "from your graveyard"),
            PRINTED + " Draw a card.",
        ):
            with self.subTest(text=changed):
                ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=registry)
                self.assertFalse(any(COMMANDER_RETURN_CAPABILITY in item.capability_dependencies
                                     for face in ir.faces for item in face.nodes))


class CommandZoneReturnGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    helper = witnesses.ReflexivePaymentRuntimeTests
    session = helper.session
    add = helper.add
    prepare = helper.prepare
    checkpoint = helper.checkpoint
    resolve = helper.resolve
    resolve_top = helper.resolve_top
    choose = helper.choose
    replay = helper.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "command-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "command-zone-commander-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Command Beacon partner witness", [
            *(DeckEntry(name, 1, "commander") for name in PARTNERS),
            DeckEntry("Generic Bound Plains", 30),
        ], list(PARTNERS))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def scene(self, seed):
        game = self.session(seed)
        land = self.add(game.engine, "Command Beacon")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        commanders = tuple(game.state.cards[key] for key in game.state.players["A"].zones["command"])
        self.assertEqual(2, len(commanders))
        self.assertTrue(all(card.is_commander and card.commander_designation_id for card in commanders))
        self.prepare(game, mana={"C": 2})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        return game, land, commanders, counter

    def actions(self, game, actor):
        return game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]

    def beacon_offer(self, game, land):
        return next(row for row in self.actions(game, "A") if row.get("source") == land.ref
                    and row.get("ability") == "ab2")

    def activate(self, game, land):
        action = self.beacon_offer(game, land)
        self.assertEqual((1, 1), (action["cost_summary"]["tap"], action["cost_summary"]["sac_self"]))
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        self.assertEqual([], game.state.stack[-1].targets)

    def cast(self, game, actor, card, targets=()):
        offer = next(row for row in self.actions(game, actor) if row.get("card") == card.ref)
        payload = {"action_id": offer["id"], "pay": "auto"}
        if targets:
            payload["targets"] = list(targets)
        result = game.act("pilot:" + actor, payload)
        self.assertTrue(result.ok, result.summary)

    def reach_priority(self, game, seat):
        for _ in range(8):
            self.assertEqual("priority", game.state.pending_decision.kind)
            principal, = game.pending_principals()
            if principal == "pilot:" + seat:
                return
            self.assertTrue(game.act(principal, {"action_id":"pass"}).ok)
        self.fail("Expected actor did not receive priority")

    def choose_for_hand(self, game):
        decision = game.state.pending_decision
        if decision is None or decision.kind != "replacement.order":
            return
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        decline, = (row["id"] for row in ctx["options"] if row["id"].startswith("decline:"))
        self.choose(game, replacement=decline)

    def test_partner_choice_is_private_to_the_actor_and_moves_one_public_commander(self):
        game, land, commanders, counter = self.scene(6130141)
        self.activate(game, land)
        self.assertEqual(["command", "command"], [game.state.cards[card.object_id].zone for card in commanders])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        actor = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({card.ref for card in commanders}, {row["id"] for row in actor["objects"]})
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        for seat in ("A", "C", "D"):
            self.assertNotIn(counter.ref, str(game.packet("pilot:" + seat, full=True)))
        rival = game.state.cards[game.state.players["B"].zones["command"][0]].ref
        before = authoritative_state_hash(game.state)
        for seat, refs in (("B", [commanders[0].ref]), ("A", []),
                           ("A", [rival]), ("A", [card.ref for card in commanders])):
            result = game.act("pilot:" + seat, {"action_id":"choose", "objects":refs})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, objects=[commanders[1].ref])
        self.choose_for_hand(game)
        self.assertEqual(["command","hand"], [game.state.cards[card.object_id].zone for card in commanders])
        self.assertEqual(0, controller_commander_cast_count(game.state, "A"))
        self.replay(game)

    def test_resolving_return_excludes_a_commander_cast_while_the_ability_waits(self):
        game, land, commanders, _ = self.scene(6130142)
        self.activate(game, land)
        self.cast(game, "A", commanders[0])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[commanders[0].object_id].zone)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose_for_hand(game)
        self.assertEqual(["battlefield","hand"], [game.state.cards[card.object_id].zone for card in commanders])
        self.assertEqual(1, controller_commander_cast_count(game.state, "A"))
        self.replay(game)

    def test_empty_current_command_zone_resolves_without_a_choice(self):
        game, land, commanders, _ = self.scene(6130148)
        self.activate(game, land)
        for card in commanders:
            self.cast(game, "A", card)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield", "battlefield"],
                         [game.state.cards[card.object_id].zone for card in commanders])
        self.assertEqual(2, controller_commander_cast_count(game.state, "A"))
        self.replay(game)

    def test_a_commander_returning_during_the_wait_is_offered_in_its_new_incarnation(self):
        game, land, commanders, counter = self.scene(6130143)
        original = commanders[0].logical_object_id
        self.activate(game, land)
        self.cast(game, "A", commanders[0])
        cast_ref = game.state.stack[-1].ref
        self.reach_priority(game, "B")
        self.cast(game, "B", counter, [cast_ref])
        self.resolve_top(game)
        self.assertEqual("state.commander_zone", game.state.pending_decision.kind)
        result = game.act("pilot:A", {"action_id":"command"})
        self.assertTrue(result.ok, result.summary)
        self.assertNotEqual(original, game.state.cards[commanders[0].object_id].logical_object_id)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        actor = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({card.ref for card in commanders}, {row["id"] for row in actor["objects"]})
        game = self.replay(game, load=True)
        self.choose(game, objects=[commanders[0].ref])
        self.choose_for_hand(game)
        self.assertEqual("hand", game.state.cards[commanders[0].object_id].zone)
        self.assertEqual(1, controller_commander_cast_count(game.state, "A"))
        self.replay(game)

    def test_failed_actual_payment_restores_untapped_source_and_original_priority(self):
        game, land, _, _ = self.scene(6130144)
        offer = self.beacon_offer(game, land)
        before = authoritative_state_hash(game.state)
        original = game.engine.move_card

        def fault(object_id, destination, **kwargs):
            if object_id == land.object_id and destination == "graveyard":
                self.assertTrue(game.state.cards[land.object_id].tapped)
                raise GameRuleError("Injected activation payment failure")
            return original(object_id, destination, **kwargs)

        with patch.object(game.engine, "move_card", side_effect=fault):
            result = game.act("pilot:A", {"action_id":offer["id"], "pay":"auto"})
            self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.activate(game, land)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.replay(game, load=True)

    def test_native_admission_and_price_binding_reject_unpaid_or_retargeted_carriers(self):
        game, land, _, _ = self.scene(6130145)
        report = semantic_preflight(self.db, DeckDefinition("Command Beacon", [DeckEntry("Command Beacon")], []))
        row, = report["cards"]
        self.assertEqual("fully_playable", row["status"], row["unresolved"])
        self.assertTrue(row["card_program_strict_capability_ready"])
        program, = [entry for entry in game.engine.semantics.programs_for_oracle(land.oracle_id)
                    if COMMANDER_RETURN_CAPABILITY in entry.capability_dependencies]
        changes = (
            {"cost_schema": {**program.cost_schema, "sacrifice_source": False}},
            {"effects": (commander_return_effect("$opponent"),)},
            {"target_schema": {"zones": ["command"], "count": 1}},
        )
        for change in changes:
            with self.subTest(change=change):
                bad = replace(program, **change)
                bound = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])

    def test_native_choice_witness_kills_using_an_empty_candidate_set(self):
        def witness(seed):
            game, land, _, _ = self.scene(seed)
            self.activate(game, land)
            self.assertIsNotNone(self.resolve(game))

        witness(6130146)
        with patch("quorune.semantic_choices.commander_return._eligible_commanders", return_value=()):
            with self.assertRaisesRegex(AssertionError, "unexpectedly None"):
                witness(6130147)


STUDY = "Stinging Study"
STUDY_PARTNERS = ("Study Partner One", "Study Partner Two")
STUDY_TEXT = ("You draw X cards and you lose X life, where X is the mana value of "
              "a commander you own on the battlefield or in the command zone.")


class OwnedCommanderAmountGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    helper = CommandZoneReturnGameplayTests
    session = helper.session
    add = helper.add
    prepare = helper.prepare
    checkpoint = helper.checkpoint
    actions = helper.actions
    cast = helper.cast
    resolve = helper.resolve
    choose = helper.choose
    replay = helper.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "owned-commander-amount.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "command-zone-commander-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owned commander amount witness", [
            *(DeckEntry(name, 1, "commander") for name in STUDY_PARTNERS),
            DeckEntry("Generic Bound Plains", 30),
        ], list(STUDY_PARTNERS))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def scene(self, seed, *, move_first=None, move_second=None):
        game = self.session(seed)
        spell = self.add(game.engine, STUDY, zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        partners = {game.state.cards[key].printed_name: game.state.cards[key]
                    for key in game.state.players["A"].zones["command"]}
        owned = tuple(partners[name] for name in STUDY_PARTNERS)
        if move_first is not None:
            controller = "B" if move_first == "battlefield" else None
            game.engine.move_card(owned[0].object_id, move_first, controller=controller, log=False)
        if move_second is not None:
            game.engine.move_card(owned[1].object_id, move_second, log=False)
        self.prepare(game, mana={"B": 1, "C": 4})
        for zone in (move_first, move_second):
            if zone in {"graveyard", "exile"}:
                self.assertEqual("state.commander_zone", game.state.pending_decision.kind)
                result = game.act("pilot:A", {"action_id": "remain"})
                self.assertTrue(result.ok, result.summary)
        self.checkpoint(game)
        return game, spell, owned, counter

    def test_owned_commander_amount_compiles_only_the_closed_spell(self):
        from quorune.rules.owned_commander_amount import (
            OWNED_COMMANDER_AMOUNT_CAPABILITY, owned_commander_amount_effect,
        )
        card = self.db.lookup(STUDY)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((owned_commander_amount_effect(),), node.effects)
        self.assertIsNone(node.target_schema)
        self.assertIn(OWNED_COMMANDER_AMOUNT_CAPABILITY, node.capability_dependencies)
        for text in (
            STUDY_TEXT.replace("you own", "you control"),
            STUDY_TEXT.replace("on the battlefield", "in your graveyard"),
            STUDY_TEXT.replace("you lose X life", "target player loses X life"),
            STUDY_TEXT.replace("you lose X life", "you gain X life"),
        ):
            ir = compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
            )
            self.assertFalse(any(OWNED_COMMANDER_AMOUNT_CAPABILITY in row.capability_dependencies
                                 for face in ir.faces for row in face.nodes), text)
        game, spell, _, _ = self.scene(6132030)
        program, = (entry for entry in game.engine.semantics.programs_for_oracle(spell.oracle_id)
                    if OWNED_COMMANDER_AMOUNT_CAPABILITY in entry.capability_dependencies)
        for changed in (
            replace(program, effects=(owned_commander_amount_effect("$opponent"),)),
            replace(program, effects=({**owned_commander_amount_effect(), "owned_commander_mana_value": True},)),
            replace(program, target_schema={"zones": ["battlefield"], "count": 1}),
            replace(program, effects=(owned_commander_amount_effect(), {"op": "draw", "count": 1})),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"])

    def test_owned_commander_amount_partner_choice_private_draw_and_replay(self):
        game, spell, owned, counter = self.scene(6132031, move_first="battlefield")
        self.assertEqual("B", game.state.cards[owned[0].object_id].controller)
        self.assertEqual("A", game.state.cards[owned[0].object_id].owner)
        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
        self.assertEqual({item.ref for item in owned}, {row["id"] for row in choices})
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        self.assertNotIn(counter.ref, str(game.packet("pilot:A", full=True)))
        rival = game.state.cards[game.state.players["B"].zones["command"][0]]
        before_hash = authoritative_state_hash(game.state)
        for seat, selection in (("B", [owned[0].ref]), ("A", [rival.ref]), ("A", [])):
            rejected = game.act("pilot:" + seat, {"action_id": "choose", "objects": selection})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before_hash, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        life = game.state.players["A"].life
        hand = set(game.state.players["A"].zones["hand"])
        self.choose(game, objects=[owned[0].ref])
        self.assertEqual(life - 3, game.state.players["A"].life)
        drawn = set(game.state.players["A"].zones["hand"]) - hand
        self.assertEqual(3, len(drawn))
        for object_id in drawn:
            ref = game.state.cards[object_id].ref
            self.assertNotIn(ref, str(game.packet("pilot:B", full=True)))
        self.replay(game)

    def test_owned_commander_amount_zero_and_single_current_candidates(self):
        for first_zone, second_zone, amount in (("exile", "graveyard", 0), ("exile", None, 5)):
            with self.subTest(amount=amount):
                game, spell, _, _ = self.scene(6132032 + amount, move_first=first_zone, move_second=second_zone)
                self.cast(game, "A", spell)
                before = game.state.players["A"].life
                hand = len(game.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual((before - amount, hand + amount),
                                 (game.state.players["A"].life, len(game.state.players["A"].zones["hand"])))
                self.replay(game)

    def test_owned_commander_amount_draw_only_mutation_is_detected(self):
        from quorune.semantic_choices.owned_commander_amount import _ordered_results
        with patch("quorune.semantic_choices.owned_commander_amount._ordered_results",
                   side_effect=lambda player, count: _ordered_results(player, count)[:1]):
            with self.assertRaisesRegex(AssertionError, '37 != 40'):
                self.test_owned_commander_amount_partner_choice_private_draw_and_replay()

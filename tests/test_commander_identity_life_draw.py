"""Native activation pricing from pregame commander designations."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.ordering import ReplacementChoiceRequired
from quorune.rules.activation.commander_identity_life import (
    ACTIVATION_COMMANDER_LIFE_CAPABILITY, ACTIVATION_COMMANDER_LIFE_FIELD,
    commander_identity_life_draw_runtime_dependencies,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


CARD = "War Room"
PROFILE = "commander_review"
PAIR = ("Vial Smasher the Fierce", "Kraum, Ludevic's Opus")
COLORLESS = "Ulamog, the Ceaseless Hunger"
ISAMARU = "Isamaru, Hound of Konda"


class CommanderIdentityLifeDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "commander-identity-mana-cards.json": {ISAMARU, COLORLESS, PAIR[0]},
            "commander-pairing-cards.json": {PAIR[1]},
        }
        paths = []
        for filename, names in support.items():
            payload = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            payload["cards"] = [card for card in payload["cards"] if card["name"] in names]
            payload["rulings"] = []
            path = root / filename
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)
        db = root / "commander-life-draw.sqlite3"
        build_fixture_database([*paths, fixtures / "commander-identity-life-draw-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = cls.commander_deck((ISAMARU,))

    @staticmethod
    def commander_deck(names):
        return DeckDefinition("Commander life price", [
            *(DeckEntry(name, 1, "commander") for name in names),
            DeckEntry("Generic Bound Plains", 30),
        ], list(names))

    def game(self, names, seed):
        decks = {seat: self.commander_deck(names if seat == "A" else (ISAMARU,)) for seat in "ABCD"}
        return CommanderSession.create(self.db, decks, first_player="A", seed=seed,
                                       config=GameConfig(seed=seed, auto_pass_empty_priority=False))

    def actions(self, game, seat="A"):
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_exact_original_requires_its_closed_price_and_private_draw(self):
        record = self.db.lookup(CARD, fuzzy=False)
        opts = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **opts)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        priced, = [node for node in ir.faces[0].nodes
                   if ACTIVATION_COMMANDER_LIFE_CAPABILITY in node.capability_dependencies]
        self.assertEqual(1, priced.cost[ACTIVATION_COMMANDER_LIFE_FIELD])
        for old, new in (("number of colors", "number of cards"), ("your commanders'", "an opponent's commander’s"),
                         ("Draw a card.", "Draw two cards.")):
            with self.subTest(operand=old):
                mutant = replace(record, oracle_text=record.oracle_text.replace(old, new, 1))
                self.assertNotEqual("exact", compile_oracle_card(mutant, **opts).status)
        game = self.session(622712100)
        room = self.add(game.engine, CARD)
        self.assert_closed(game, room)
        program, = [row for row in game.engine.semantics.programs_for_oracle(room.oracle_id)
                    if ACTIVATION_COMMANDER_LIFE_CAPABILITY in row.capability_dependencies]
        price = {k: v for k, v in program.cost_schema.items() if k != ACTIVATION_COMMANDER_LIFE_FIELD}
        with self.assertRaises(ValueError):
            commander_identity_life_draw_runtime_dependencies(replace(program, cost_schema=price))
        deps = tuple(dep for dep in program.capability_dependencies if dep != ACTIVATION_COMMANDER_LIFE_CAPABILITY)
        mutated = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            mutated, capability_registry=self.registry, profile=PROFILE,
        )["strict"])

    def test_paid_draw_uses_payers_distinct_partner_colors_after_zone_and_control_changes(self):
        game = self.game(PAIR, 622712101)
        first, second = [next(card for card in game.state.cards.values()
                             if card.owner == "A" and card.printed_name == name) for name in PAIR]
        with self.assertRaises(ReplacementChoiceRequired) as choice:
            game.engine.move_card(first.object_id, "hand", log=False, semantic_events=False)
        decline, = [value for value in choice.exception.pending.choice.legal_selections
                    if value.startswith("decline:")]
        game.engine.move_card(first.object_id, "hand", log=False, semantic_events=False,
                              replacement_selections=(decline,))
        game.engine.move_card(second.object_id, "battlefield", controller="B", log=False, semantic_events=False)
        room = self.add(game.engine, CARD, seat="B")
        game.engine.change_control(room.object_id, "A", reason="Fixture separates activator and land owner")
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.assertEqual({"U", "B", "R"}, game.engine._commander_identity("A"))
        action = f"activate:{room.ref}:ab2"
        offer, = [row for row in self.actions(game) if row["id"] == action]
        self.assertEqual(3, offer["cost_summary"]["life"])
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": action, "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        life = game.state.players["A"].life
        library = game.state.players["A"].zones["library"]
        top_ref = game.state.cards[library[-1]].ref
        hand = len(game.state.players["A"].zones["hand"])
        paid = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual((life - 3, 0), (game.state.players["A"].life, game.state.players["A"].mana_pool["C"]))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(hand, len(game.state.players["A"].zones["hand"]))
        self.assertTrue(game.state.cards[room.object_id].tapped)
        self.assertEqual(("activated_ability", "A"), (game.state.stack[-1].kind, game.state.stack[-1].controller))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        self.assertIn(top_ref, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(top_ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_zero_color_payment_is_legal_and_missing_or_unaffordable_designation_cannot_pay(self):
        game = self.game((COLORLESS,), 622712102)
        room = self.add(game.engine, CARD)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        action = f"activate:{room.ref}:ab2"
        offer, = [row for row in self.actions(game) if row["id"] == action]
        self.assertEqual(0, offer["cost_summary"]["life"])
        result = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[room.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

        for missing in (False, True):
            with self.subTest(missing_designation=missing):
                game = self.game(PAIR, 622712103 + missing)
                room = self.add(game.engine, CARD)
                game.state.players["A"].life = 2
                if missing:
                    game.state.commander_oracle_ids["A"] = []
                self.prepare(game, mana={"C": 3})
                actions = {row["id"] for row in self.actions(game)}
                self.assertIn(f"activate:{room.ref}:ab1", actions)
                self.assertNotIn(f"activate:{room.ref}:ab2", actions)
                self.assertFalse(game.state.cards[room.object_id].tapped)

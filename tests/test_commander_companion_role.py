"""Commander role legality and native cast/uncast behavior for the exact original."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.commander_companion_roles import (
    COMPANION_ROLE_CAPABILITY, COMPANION_ROLE_EVENT, companion_role_runtime_dependencies,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


CARD = "Lutri, the Spellchaser"
PROFILE = "commander_review"
COMMANDERS = ("Kraum, Ludevic's Opus", "Tymna the Weaver")


class CommanderCompanionRoleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        supports = {
            "commander-pairing-cards.json": set(COMMANDERS),
            "fixed-stack-spell-copy-cards.json": {CARD, "Shock"},
            "activation-restriction-material-cards.json": {"Cloudshift", "Lightning Bolt"},
            "ability-label-composition-cards.json": {"Island"},
        }
        paths = []
        for filename, names in supports.items():
            data = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            if {row["name"] for row in data["cards"]} != names:
                raise ValueError(f"Missing pinned test card in {filename}")
            oids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", []) if row["oracle_id"] in oids]
            path = root / filename
            path.write_text(json.dumps(data), encoding="utf-8")
            paths.append(path)
        path = root / "commander-companion-role.sqlite3"
        build_fixture_database(paths, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @staticmethod
    def deck_for(*, role="mainboard"):
        commanders = COMMANDERS if role != "commander" else (CARD,)
        entries = [DeckEntry(name, 1, "commander") for name in commanders]
        entries.extend(DeckEntry(name, 1) for name in ("Shock", "Lightning Bolt"))
        if role != "commander":
            entries.extend((DeckEntry(CARD, 1, role), DeckEntry("Cloudshift", 1)))
        entries.append(DeckEntry("Island", 100 - sum(entry.quantity for entry in entries if entry.board != "companion")))
        return DeckDefinition("Commander role witness", entries, list(commanders))

    def game(self, seed, *, role="mainboard"):
        return CommanderSession.create(
            self.db, {seat: self.deck_for(role=role if seat == "A" else "mainboard") for seat in "ABCD"},
            first_player="A", seed=seed,
            config=GameConfig(seed=seed, semantic_policy="trusted_only", auto_pass_empty_priority=False),
        )

    def hand_card(self, game, seat, name):
        card, = [item for item in game.state.cards.values() if item.owner == seat and item.printed_name == name]
        if card.zone != "hand":
            game.engine.move_card(card.object_id, "hand", log=False, semantic_events=False)
        return card

    def test_commander_role_authority_allows_mainboard_and_commander_and_rejects_companion_setup(self):
        loader = DeckLoader(self.db)
        for role in ("mainboard", "commander"):
            with self.subTest(role=role):
                self.assertEqual([], loader.validate_commander_deck(self.deck_for(role=role)))
        game = self.game(617028101, role="commander")
        commander, = [card for card in game.state.cards.values() if card.owner == "A" and card.printed_name == CARD]
        self.assertEqual(("command", True), (commander.zone, commander.is_commander))
        banned, = loader.commander_legality_issues(self.deck_for(role="companion"))
        self.assertEqual(("commander_companion_banned", "companion", "banned"),
                         (banned["code"], banned["board"], banned["legality"]))
        with self.assertRaisesRegex(ValueError, "banned as a companion"):
            self.game(617028102, role="companion")

    def test_companion_clause_requires_exact_commander_authority_and_has_no_executable_action(self):
        record = self.db.lookup(CARD, fuzzy=False)
        for profile in ("commander_review", "commander_duel", "traditional"):
            with self.subTest(profile=profile):
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=profile)
                self.assertEqual("partial" if profile == "traditional" else "exact", ir.status, ir.material_residuals)
        changed = replace(record, oracle_text=record.oracle_text.replace("a different name", "the same name"))
        self.assertEqual("partial", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)
        game = self.game(617028103)
        card = self.hand_card(game, "A", CARD)
        self.assert_closed(game, card)
        program, = [row for row in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if row.event == COMPANION_ROLE_EVENT]
        self.assertEqual((COMPANION_ROLE_CAPABILITY,), companion_role_runtime_dependencies(program))
        self.assertFalse(program.effects or program.handlers)
        with self.assertRaises(ValueError):
            companion_role_runtime_dependencies(replace(program, effects=[{"op": "draw", "count": 1}]))
        deps = tuple(dep for dep in program.capability_dependencies if dep != COMPANION_ROLE_CAPABILITY)
        reduced = replace(program, capability_dependencies=deps,
                          capability_closure=None)
        self.assertFalse(bind_semantic_program_runtime(
            reduced, capability_registry=self.registry, profile=PROFILE,
        )["strict"])

    def test_starting_deck_lutri_flashes_copies_its_controllers_spell_and_blinks_without_trigger(self):
        game = self.game(617028104)
        lutri = self.hand_card(game, "A", CARD)
        bolt = self.hand_card(game, "A", "Lightning Bolt")
        own_shock = self.hand_card(game, "A", "Shock")
        foreign_shock = self.hand_card(game, "B", "Shock")
        blink = self.hand_card(game, "A", "Cloudshift")
        self.assert_closed(game, lutri)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"C": 1, "U": 2, "R": 5, "W": 3})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, ["C"])
        own_ref = game.state.stack[-1].ref
        self.cast_for(game, "B", foreign_shock, ["D"])
        other_ref = game.state.stack[-1].ref
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        flash, = [row for row in offers if row["id"] == "cast:" + lutri.ref]
        result = game.act("pilot:A", {
            "action_id": flash["id"], "cost_option": flash["cost_options"][0]["id"], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.resolve(game).kind)
        game = self.replay(game, load=True)
        schema = json.dumps(game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"])
        self.assertIn(own_ref, schema)
        self.assertNotIn(other_ref, schema)
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": "choose", "targets": [other_ref]})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "targets": [own_ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(result.ok, result.summary)
        original = next(item for item in game.state.stack if item.ref == own_ref)
        self.assertEqual(["C"], original.targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([37, 37, 38], [game.state.players[seat].life for seat in "BCD"])

        self.cast_for(game, "A", own_shock, ["C"])
        shock_ref = game.state.stack[-1].ref
        incarnation = game.state.cards[lutri.object_id].logical_object_id
        self.cast_for(game, "A", blink, [lutri.ref])
        self.resolve_top(game)
        current = game.state.cards[lutri.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(incarnation, current.logical_object_id)
        self.assertEqual([shock_ref], [item.ref for item in game.state.stack])
        self.assertEqual("priority", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(35, game.state.players["C"].life)
        self.replay(game, load=True)

"""A granted death occurrence returns the original owner card and independently creates Treasure."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.granted_death_treasure import CAPABILITY, validate_granted_death_treasure_child
from scripts.build_test_database import build_fixture_database

CARD = "Fake Your Own Death"
PROFILE = "commander_review"
ORACLE = ('Until end of turn, target creature gets +2/+0 and gains "When this creature dies, return it '
          'to the battlefield tapped under its owner\'s control and you create a Treasure token."')


class GrantedDeathTreasureTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "granted-death-treasure.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "granted-death-return-treasure-cards.json",
            "attacking-card-entry-cards.json", "state-qualified-zone-cards.json",
            "ability-source-counter-cards.json", "activated-self-transform-cards.json",
            "activation-public-state-cards.json", "graveyard-exile-replacement-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Granted death and Treasure", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_binds_the_exact_temporary_parent_and_independent_death_effects(self):
        record = replace(query_record(ORACLE), type_line="Instant", mana_cost="{1}{B}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        parent, child = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[parent.span.start:parent.span.end])
        self.assertEqual(child.text, ORACLE[child.span.start:child.span.end])
        self.assertEqual((2, 0), (parent.effects[0]["power"], parent.effects[0]["toughness"]))
        self.assertEqual(("permanent.graveyard.self", "battlefield"), (child.event, child.active_zone))
        self.assertEqual(["reanimate", "create_token"], [effect["op"] for effect in child.effects])
        self.assertEqual(("$controller", "Treasure Token"), (child.effects[1]["controller"], child.effects[1]["name"]))
        self.assertIn(CAPABILITY, child.capability_dependencies)
        for old, new in (("and you create", "and if you do, create"),
                         ("under its owner's", "under your"),
                         ("a Treasure token", "a Clue token")):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=ORACLE.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        programs = semantics.programs_for_oracle(self.db.lookup(CARD, fuzzy=False).oracle_id)
        root = next(program for program in programs if program.event == "resolve")
        bound = next(program for program in programs if program.event == "permanent.graveyard.self")
        missing = [key for key in bound.capability_dependencies if key != CAPABILITY]
        changed = replace(bound, capability_dependencies=missing,
                          capability_closure=self.registry.closure(missing, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])
        with self.assertRaisesRegex(ValueError, "original death trigger"):
            validate_granted_death_treasure_child(root, lambda _key: None)

    def start(self, game, *cards, mana=None, ending=False):
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana or {"C": 2, "B": 3})
        if ending:
            game.engine.permissions.invalidate_current()
            game.state.pending_decision = None
            game.state.priority_player = None
            game.state.phase = "postcombat_main"
            game.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
            game.engine._grant_priority("A")
            game.engine.pump()
        self.checkpoint(game)

    @staticmethod
    def current(game, card):
        return game.state.cards[card.object_id]

    @staticmethod
    def treasures(game):
        return [card for card in game.state.cards.values() if card.is_token and card.printed_name == "Treasure Token"]

    def grant_then_remove(self, game, grant, murder, body, *, before_killing=None):
        self.cast_for(game, "A", grant, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(body.object_id, "power"))
        if before_killing is not None:
            before_killing()
        self.cast_for(game, "A", murder, [body.ref])
        self.resolve_top(game)

    def test_the_controller_at_death_creates_treasure_and_the_card_returns_tapped_to_its_owner(self):
        game = self.session(60331101)
        grant = self.add(game.engine, CARD, zone="hand")
        murder = self.add(game.engine, "Murder", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", seat="B")
        timing = self.add(game.engine, "Vedalken Orrery", seat="C")
        treason = self.add(game.engine, "Act of Treason", seat="C", zone="hand")
        game.state.players["C"].mana_pool.update({"C": 2, "R": 1})
        initial = body.logical_object_id
        self.start(game, grant, murder, timing, treason)

        def take_control():
            self.cast_for(game, "C", treason, [body.ref])
            self.assertIsNone(self.resolve(game))
            self.assertEqual("C", self.current(game, body).controller)

        self.grant_then_remove(game, grant, murder, body, before_killing=take_control)
        trigger, = game.state.stack
        self.assertEqual("C", trigger.controller)
        self.assertEqual("graveyard", self.current(game, body).zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        returned = self.current(game, body)
        self.assertEqual(("battlefield", "B", "B", True), (returned.zone, returned.owner, returned.controller, returned.tapped))
        self.assertNotEqual(initial, returned.logical_object_id)
        self.assertEqual(2, game.engine._numeric_stat(returned.object_id, "power"))
        treasure, = self.treasures(game)
        self.assertEqual(("C", "battlefield"), (treasure.controller, treasure.zone))
        self.replay(game, load=True)

    def test_stale_original_graveyard_card_still_yields_treasure(self):
        game = self.session(60331102)
        grant = self.add(game.engine, CARD, zone="hand")
        murder = self.add(game.engine, "Murder", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", seat="B")
        exile = self.add(game.engine, "Cremate", seat="C", zone="hand")
        game.state.players["C"].mana_pool.update({"B": 1})
        self.start(game, grant, murder, exile)
        self.grant_then_remove(game, grant, murder, body)
        self.assertEqual("B", game.state.stack[-1].controller)
        self.cast_for(game, "C", exile, [body.ref])
        self.resolve_top(game)
        self.assertEqual("exile", self.current(game, body).zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", self.current(game, body).zone)
        treasure, = self.treasures(game)
        self.assertEqual("B", treasure.controller)
        self.replay(game, load=True)

    def test_dead_token_still_yields_treasure_after_state_based_actions(self):
        game = self.session(60331103)
        grant = self.add(game.engine, CARD, zone="hand")
        murder = self.add(game.engine, "Murder", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.start(game, grant, murder, alarm)
        self.cast_for(game, "B", alarm)
        self.assertIsNone(self.resolve(game))
        body = next(card for card in game.state.cards.values() if card.is_token and card.controller == "B")
        self.cast_for(game, "A", grant, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", murder, [body.ref])
        self.resolve_top(game)
        self.assertEqual("outside", self.current(game, body).zone)
        self.assertNotIn(body.object_id, game.state.players["B"].zones["graveyard"])
        self.assertEqual("B", game.state.stack[-1].controller)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        treasure, = self.treasures(game)
        self.assertEqual("B", treasure.controller)
        self.assertEqual("outside", self.current(game, body).zone)
        self.assertNotIn(body.object_id, game.state.players["B"].zones["graveyard"])
        self.replay(game, load=True)

    def test_cleanup_expires_both_the_pump_and_granted_death_trigger(self):
        game = self.session(60331104)
        grant = self.add(game.engine, CARD, zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", seat="A")
        self.add(game.engine, "Generic Bound Plains", seat="B")
        for index in range(2):
            self.add(game.engine, "Swamp", seat="B", ref=f"b-swamp-{index}")
        self.start(game, grant, murder, mana={"C": 1, "B": 1}, ending=True)
        self.cast_for(game, "A", grant, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(body.object_id, "power"))
        self.until(game, lambda session: session.state.active_player == "B" and session.state.phase == "precombat_main")
        self.assertEqual(2, game.engine._numeric_stat(body.object_id, "power"))
        self.cast_for(game, "B", murder, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertFalse(self.treasures(game))
        self.assertEqual("graveyard", self.current(game, body).zone)
        self.replay(game, load=True)

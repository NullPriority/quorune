"""Actual destruction receipt sizes one token before triggers and state-based actions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_result_model import DestructionFollowup
from quorune.rules.destruction_sized_token import DESTRUCTION_SIZED_TOKEN_CAPABILITY, DESTRUCTION_SIZED_TOKEN_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CreatureDestructionTokenSizeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-destruction-token-size.sqlite3"
        fixture = ROOT / "tests/fixtures/creature-destruction-token-size-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed destruction sizes one token", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "B": 1})
        self.prepare(game, mana={"C": 4, "W": 2})
        self.checkpoint(game)

    def horrors(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Phyrexian Horror Token"]

    def assert_horror(self, game, size):
        horror, = self.horrors(game)
        self.assertEqual(("A", "A"), (horror.owner, horror.controller))
        self.assertEqual((size, size), tuple(game.engine._numeric_stat(horror.object_id, stat) for stat in ("power", "toughness")))
        self.assertEqual({}, horror.counters)
        return horror

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_original_creatures_size_the_horror_before_the_destroyed_myr_chooses_its_target(self):
        game = self.session(76352001)
        spell = self.add(game.engine, "Phyrexian Rebirth", zone="hand")
        death = self.add(game.engine, "Perilous Myr")
        protected = self.add(game.engine, "Grizzly Bears", seat="B")
        umbra = self.add(game.engine, "Hyena Umbra", seat="B")
        survives = self.add(game.engine, "Darksteel Myr", seat="C")
        alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        attach_objects(game.state.cards, umbra, protected, source_timestamp=umbra.zone_timestamp, players=game.state.players)
        self.ready(game, spell, death, umbra, survives, alarm)
        self.cast(game, "B", alarm)
        self.resolve_top(game)
        self.assertEqual(2, sum(card.zone == "battlefield" and card.printed_name == "Soldier Token"
                                for card in game.state.cards.values()))
        self.cast(game, "A", spell)
        self.until(game, lambda session: session.state.pending_decision is not None
                   and session.state.pending_decision.kind == "semantic.target")
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[death.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[umbra.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[protected.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[survives.object_id].zone)
        horror = self.assert_horror(game, 3)
        game = self.replay(game, load=True)
        self.target(game, "A", [horror.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[horror.object_id].marked_damage)
        self.assert_horror(game, 3)
        self.replay(game, load=True)

    def test_actual_zero_creates_one_zero_toughness_token_then_the_normal_entry_trigger_resolves(self):
        game = self.session(76352002)
        spell = self.add(game.engine, "Phyrexian Rebirth", zone="hand")
        tremors = self.add(game.engine, "Impact Tremors")
        self.ready(game, spell, tremors)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.horrors(game))
        creation, = (event for event in game.state.events if event.code == "token.create")
        self.assertEqual(1, creation.details["base_quantity"])
        self.assertEqual([40, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_regeneration_and_exile_replacement_resume_with_the_same_size_receipt(self):
        game = self.session(76352003)
        spell = self.add(game.engine, "Phyrexian Rebirth", zone="hand")
        skeletons = self.add(game.engine, "Drudge Skeletons", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        rest = self.add(game.engine, "Rest in Peace")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="rebirth-replacement-unrelated-hand")
        self.ready(game, spell, skeletons, rest, leyline)
        self.cast(game, "A", spell)
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = (row for row in rows if row["id"].startswith(f"activate:{skeletons.ref}:"))
        result = game.act("pilot:B", {"action_id": activate["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(1, game.state.cards[skeletons.object_id].regeneration_shields)
        self.assertEqual([], self.horrors(game))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "A", command)
        self.reject(game, "C", {**command, "replacement": "unoffered"})
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        if pending is not None:
            self.assertEqual("replacement.order", pending.kind)
            principal = game.pending_principals()[0]
            options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
            result = game.act(principal, {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[creature.object_id].zone)
        survivor = game.state.cards[skeletons.object_id]
        self.assertEqual(("battlefield", 0), (survivor.zone, survivor.regeneration_shields))
        self.assert_horror(game, 1)
        self.replay(game, load=True)

    def test_complete_original_requires_the_exact_committed_size_and_ordinary_destruction_scope(self):
        cap = DESTRUCTION_SIZED_TOKEN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Phyrexian Rebirth")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        result = DestructionFollowup.from_destruction_effect(program.effects[0])
        self.assertEqual(DESTRUCTION_SIZED_TOKEN_VERSION, result.schema_version)
        self.assertEqual(1, result.effect["quantity"])
        self.assertIsNone(program.target_schema)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        widened = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(widened, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Phyrexian Rebirth", fuzzy=False)
        for before, after in (("Destroy all creatures", "Destroy all creatures you control"),
                              ("creatures destroyed this way", "creatures that died this way"),
                              ("an X/X", "two X/X"), ("this way.", "this way. Rewrite history.")):
            with self.subTest(unsupported=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

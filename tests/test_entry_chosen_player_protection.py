"""Native evidence for all protection consequences of one linked player entry."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE, ProtectionQualityKind, ProtectionSpec, ability_fragment_from_dict,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "True-Name Nemesis"


class EntryChosenPlayerProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-chosen-player-protection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-chosen-player-protection-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Protection from the chosen player", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def programs(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        entry, = [program for program in programs if program.event == "zone.change"]
        protection, = [program for program in programs if program.event == "characteristics.evaluate"]
        return original, entry, protection

    def test_original_compiles_the_exact_any_player_pair_with_current_protection(self):
        original, entry, protection = self.programs()
        self.assertEqual(("all", "battlefield"), (entry.active_zone, protection.active_zone))
        self.assertIsNone(entry.target_schema)
        self.assertIsNone(protection.target_schema)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, protection.coverage)
        for changed in (
            original.oracle_text.splitlines()[0],
            original.oracle_text.splitlines()[1],
            original.oracle_text.replace("choose a player", "choose an opponent"),
            original.oracle_text.replace("the chosen player", "the chosen creature"),
            original.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(original, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_partners_require_the_battlefield_component_and_linked_entry(self):
        _, entry, protection = self.programs()
        for changed in (
            replace(entry, handlers=[]),
            replace(protection, handlers=[]),
            replace(protection, active_zone="command"),
            replace(protection, coverage=tuple(tag for tag in protection.coverage
                                                   if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def cast_and_choose(self, game, source, player, *, reserve=None):
        self.cast_for(game, "A", source, reserve=reserve)
        choice = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        game = self.replay(game, load=True)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        options = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in rows}
        self.assertEqual(set("ABCD"), set(options))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:C", {"action_id": "choose", "replacement": options[player]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": options[player]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        return game

    def test_actual_targeting_and_nontargeted_damage_use_the_chosen_source_player(self):
        for unpreventable in (False, True):
            with self.subTest(unpreventable=unpreventable):
                game = self.session(70216031 + unpreventable)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                shot = self.add(game.engine, "Shock", seat="B", zone="hand", ref="chosen-shot")
                other = self.add(game.engine, "Shock", seat="C", zone="hand", ref="unchosen-shot")
                fallout = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
                pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand") if unpreventable else None
                for card in (source, shot, other, fallout, pain):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "U": 2})
                game.state.players["B"].mana_pool.update({"C": 2, "R": 3})
                game.state.players["C"].mana_pool["R"] = 1
                self.checkpoint(game)
                game = self.cast_and_choose(game, source, "B")
                self.priority(game, "B")
                offered = self.action(game, "B", shot)
                self.assertNotIn(source.ref, offered["target_schema"]["legal_refs"])
                before = authoritative_state_hash(game.state)
                denied = game.act("pilot:B", {"action_id": offered["id"], "targets": [source.ref], "pay": "auto"})
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                if pain is not None:
                    self.cast_for(game, "B", pain, reserve={"C": 1, "R": 2})
                    self.resolve_top(game)
                self.cast_for(game, "B", fallout)
                self.resolve_top(game)
                if unpreventable:
                    self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                else:
                    self.assertEqual(("battlefield", 0), (game.state.cards[source.object_id].zone,
                                                         game.state.cards[source.object_id].marked_damage))
                    self.priority(game, "C")
                    self.assertIn(source.ref, self.action(game, "C", other)["target_schema"]["legal_refs"])
                    self.cast_for(game, "C", other, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_actual_chosen_player_creature_cannot_block_the_protected_attacker(self):
        game = self.session(70216043)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        ordinary = self.add(game.engine, "Grizzly Bears", ref="ordinary-attacker")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="chosen-blocker")
        haste = self.add(game.engine, "Fervor")
        for card in (source, ordinary, blocker, haste):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        game = self.cast_and_choose(game, source, "B")
        self.attack(game, [(source, "B"), (ordinary, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        self.assertEqual(["B"], game.state.pending_decision.actors)
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: source.ref}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        allowed = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: ordinary.ref}})
        self.assertTrue(allowed.ok, allowed.summary)
        self.replay(game, load=True)

    def test_actual_aura_and_equipment_are_rechecked_when_the_chosen_player_takes_control(self):
        for name in ("Pacifism", "Bonesplitter"):
            with self.subTest(attachment=name):
                game = self.session(70216051 + (name == "Bonesplitter"))
                source = self.add(game.engine, ORIGINAL, zone="hand")
                attachment = self.add(game.engine, name, zone="hand")
                donate = self.add(game.engine, "Donate", zone="hand")
                for card in (source, attachment, donate):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 5, "U": 3, "W": 1})
                self.checkpoint(game)
                game = self.cast_and_choose(game, source, "B", reserve={"U": 1, "W": 1, "C": 4})
                self.cast_for(game, "A", attachment, [source.ref] if name == "Pacifism" else [],
                              reserve={"U": 1, "C": 2})
                self.resolve_top(game)
                if name == "Bonesplitter":
                    self.priority(game, "A")
                    rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    equip, = [row["id"] for row in rows if row["id"].startswith(f"activate:{attachment.ref}:")]
                    attached = game.act("pilot:A", {"action_id": equip, "targets": [source.ref], "pay": "auto",
                                                    "reserve": {"U": 1, "C": 2}})
                    self.assertTrue(attached.ok, attached.summary)
                    self.resolve_top(game)
                self.assertEqual(source.object_id, game.state.cards[attachment.object_id].attached_to)
                self.cast_for(game, "A", donate, ["B", attachment.ref])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                current = game.state.cards[attachment.object_id]
                self.assertIsNone(current.attached_to)
                self.assertEqual("graveyard" if name == "Pacifism" else "battlefield", current.zone)
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                if name == "Bonesplitter":
                    self.priority(game, "B")
                    conceded = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
                    self.assertTrue(conceded.ok, conceded.summary)
                    self.assertFalse(game.state.players["B"].in_game)
                    game = self.replay(game, load=True)
                    data = game.engine._effective_card_data(game.state.cards[source.object_id])
                    self.assertIn(ProtectionSpec(ProtectionQualityKind.PLAYER, "B", 4), [
                        ability_fragment_from_dict(raw) for raw in data["ability_fragments"]
                    ])
                self.replay(game, load=True)

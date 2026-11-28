"""Native witnesses for optional private imprint consumed by linked mana."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class LinkedHandManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-hand-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-hand-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Linked mana witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def act_checked(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def colors(self, game, source):
        return {
            color for row in game.engine.available_mana_sources("A") if row.ref == source.ref
            for mode in row.modes for color, amount in mode.bundle.items() if amount
        }

    def offer(self, game, source):
        self.cast(game, "A", source)
        self.resolve_top(game)
        return self.resolve(game)

    def pit_scene(self, seed, *, extra_land=False):
        game = self.session(seed)
        source = self.add(game.engine, "Pit of Offerings", zone="hand", ref="pit-of-offerings")
        satyr = self.add(game.engine, "Voyaging Satyr", ref="pit-untapper")
        satyr.acquired_control_turn_count = -1
        if extra_land:
            self.add(game.engine, "Exploration")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        return game, source, satyr

    def pit_enter(self, game, source, refs):
        self.priority(game, "A")
        self.act_checked(game, "A", {"action_id": "play-land:" + source.ref})
        source = game.state.cards[source.object_id]
        self.assertTrue(source.tapped)
        self.target(game, "A", list(refs))
        return source

    def pit_untap(self, game, source, satyr):
        self.priority(game, "A")
        source = game.state.cards[source.object_id]
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in choices if row["id"].startswith("activate:" + satyr.ref + ":"))
        self.act_checked(game, "A", {"action_id": action["id"], "targets": [source.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)

    def pit_mana_action(self, game, source, *, linked=True):
        self.priority(game, "A")
        source = game.state.cards[source.object_id]
        abilities = game.engine._activated_abilities(source)
        ability, = (candidate for candidate in abilities
                    if (candidate.color_set_mana_output is not None) == linked)
        action = "activate:" + source.ref + ":" + ability.ability_id
        return next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"] == action)

    def test_pit_of_offerings_uses_the_public_union_and_removes_departed_colors(self):
        game, source, satyr = self.pit_scene(60725510)
        charm = self.add(game.engine, "Boros Charm", seat="B", zone="graveyard")
        bears = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard")
        ring = self.add(game.engine, "Sol Ring", zone="graveyard")
        self.add(game.engine, "Counterspell", seat="D", zone="exile", ref="unrelated-exile")
        removal = self.add(game.engine, "Pull from Eternity", zone="hand", ref="pull-linked-charm")
        private = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="private-pit-hand")
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        source = self.pit_enter(game, source, [charm.ref, bears.ref, ring.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[c.object_id].zone == "exile" for c in (charm, bears, ring)))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.pit_untap(game, source, satyr)
        self.assertEqual({"C", "W", "R", "G"}, self.colors(game, source))
        choices = self.pit_mana_action(game, source)["choice_schema"]["mana_output"]["options"]
        self.assertEqual([{"W": 1}, {"R": 1}, {"G": 1}], [row["value"] for row in choices])
        self.cast(game, "A", removal, [game.state.cards[charm.object_id].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[charm.object_id].zone)
        self.assertEqual({"C", "G"}, self.colors(game, source))
        offer = self.pit_mana_action(game, source)
        self.assert_rejected(game, "A", {"action_id": offer["id"], "mana_output": {"R": 1}})
        self.assert_rejected(game, "A", {"action_id": offer["id"], "mana_output": {"C": 1}})
        self.act_checked(game, "A", {"action_id": offer["id"], "mana_output": {"G": 1}})
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game)

    def test_pit_of_offerings_reentry_isolates_old_links_and_can_tap_for_zero(self):
        game, source, satyr = self.pit_scene(60725511, extra_land=True)
        charm = self.add(game.engine, "Boros Charm", seat="B", zone="graveyard")
        ring = self.add(game.engine, "Sol Ring", zone="graveyard")
        bounce = self.add(game.engine, "Boomerang", zone="hand", ref="bounce-old-pit")
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        source = self.pit_enter(game, source, [charm.ref])
        old = source.logical_object_id
        self.cast(game, "A", bounce, [source.ref])
        self.resolve_top(game)
        source = game.state.cards[source.object_id]
        self.assertEqual("hand", source.zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[charm.object_id].zone)
        source = self.pit_enter(game, source, [ring.ref])
        self.assertNotEqual(old, source.logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.pit_untap(game, source, satyr)
        self.assertEqual({"C"}, self.colors(game, source))
        modes = [mode for row in game.engine.available_mana_sources("A") if row.ref == source.ref for mode in row.modes]
        self.assertEqual(1, len(modes))
        self.assertEqual(1, modes[0].bundle["C"])
        before = dict(game.state.players["A"].mana_pool)
        action = self.pit_mana_action(game, source)
        self.assert_rejected(game, "A", {"action_id": action["id"], "mana_output": {"W": 1}})
        self.act_checked(game, "A", {"action_id": action["id"]})
        self.assertEqual(before, dict(game.state.players["A"].mana_pool))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.assertEqual(["exile", "exile"], [game.state.cards[c.object_id].zone for c in (charm, ring)])
        self.replay(game)

    def test_pit_of_offerings_partial_target_survival_and_normal_colorless_mana(self):
        game, source, satyr = self.pit_scene(60725512)
        taken_early = self.add(game.engine, "Boros Charm", seat="B", zone="graveyard")
        survives = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard")
        cremate = self.add(game.engine, "Cremate", zone="hand")
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        source = self.pit_enter(game, source, [taken_early.ref, survives.ref])
        self.cast(game, "A", cremate, [taken_early.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["exile", "exile"], [game.state.cards[c.object_id].zone for c in (taken_early, survives)])
        self.pit_untap(game, source, satyr)
        self.assertEqual({"C", "G"}, self.colors(game, source))
        offer = self.pit_mana_action(game, source, linked=False)
        self.act_checked(game, "A", {"action_id": offer["id"]})
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.replay(game)

    def test_pit_of_offerings_exact_pair_and_closed_scope_are_required(self):
        from quorune.rules.linked_graveyard_mana import validate_linked_graveyard_mana_group

        record = self.db.lookup("Pit of Offerings", fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
        with patch("quorune.compiler.linked_graveyard_mana.paired_linked_graveyard_mana",
                   side_effect=lambda nodes, *_args, **_kwargs: nodes):
            self.assertEqual("partial", compile_oracle_card(record, capability_registry=self.registry).status)
        program = compile_card_program(self.db, record, capability_registry=self.registry,
                                       capability_profile="commander_review", trust_level="trusted")
        reader, = (handler for ability in program.abilities for handler in ability.handlers
                   if handler["handler_id"] == "ability.activated.mana.linked-graveyard-colors.v1")
        self.assertEqual(("ab4", 3), (reader["ability"]["ability_id"], reader["ability"]["line_index"]))
        with self.assertRaises(ValueError):
            validate_linked_graveyard_mana_group(tuple(a for a in program.abilities if a.event != "permanent.enter.self"))
        for text in ("up to two target cards", "up to three target creature cards"):
            changed = replace(record, oracle_text=record.oracle_text.replace("up to three target cards", text))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

    def test_exact_pair_is_required_and_unsupported_grammar_remains_residual(self):
        card = self.db.lookup("Chrome Mox", fuzzy=False)
        registry = self.registry
        def ir(record):
            return compile_oracle_card(record, capability_registry=registry,
                                       capability_profile="commander_review")
        self.assertEqual("exact", ir(card).status)
        for text in (card.oracle_text.splitlines()[0], card.oracle_text.splitlines()[1],
                     card.oracle_text.replace("nonartifact, nonland", "nonland")):
            with self.subTest(text=text):
                self.assertNotEqual("exact", ir(replace(card, oracle_text=text)).status)
        program = compile_card_program(self.db, card, capability_registry=registry,
                                       capability_profile="commander_review", trust_level="trusted")
        complete = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertTrue(complete["strict_capability_ready"], complete)
        truncated = type(program).create(
            compiler_version=program.compiler_version, oracle_id=program.oracle_id, card_name=program.card_name,
            faces=program.faces, oracle_source_hash=program.oracle_source_hash,
            rulings_source_hash=program.rulings_source_hash, provenance=program.provenance,
            abilities=tuple(a for a in program.abilities if a.event != "permanent.enter.self"),
        )
        partial = bind_card_program_runtime(truncated, capability_registry=registry, profile="commander_review")
        self.assertFalse(partial["strict_capability_ready"], partial)
        self.assertTrue(any("requires its original same-face" in b for b in partial["blockers"]), partial)

    def test_actual_imprint_privacy_legal_colors_manual_and_automatic_replay(self):
        for mode in ("manual", "automatic"):
            with self.subTest(mode=mode):
                game = self.session(77202370 + (mode == "manual"))
                source = self.add(game.engine, "Chrome Mox", zone="hand", ref="linked-mox")
                selected = self.add(game.engine, "Boros Charm", zone="hand", ref="eligible-private")
                wrong_land = self.add(game.engine, "Dryad Arbor", zone="hand", ref="wrong-land")
                wrong_artifact = self.add(game.engine, "Sol Ring", zone="hand", ref="wrong-artifact")
                unrelated = self.add(game.engine, "Grizzly Bears", zone="exile", ref="unrelated-exile")
                payment = self.add(game.engine, "Isamaru, Hound of Konda", zone="hand", ref="needs-white")
                self.prepare(game, mana={"C": 0})
                game.state.config.semantic_policy = "trusted_only"
                self.assert_closed(game, source)
                self.checkpoint(game)
                self.assertEqual("semantic.choice", self.offer(game, source).kind)
                view = game.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertEqual({selected.ref, payment.ref}, {row["id"] for row in view["objects"]})
                for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                    packet = json.dumps(game.packet(principal, full=True))
                    self.assertNotIn(selected.ref, packet)
                    self.assertNotIn(wrong_land.ref, packet)
                    self.assertNotIn(wrong_artifact.ref, packet)
                command = {"action_id": "choose", "cards": [selected.ref]}
                self.assert_rejected(game, "B", command)
                self.assert_rejected(game, "A", {"action_id": "choose", "cards": [wrong_land.ref]})
                self.assert_rejected(game, "A", {"action_id": "choose", "cards": [wrong_artifact.ref]})
                game = self.replay(game, load=True)
                self.act_checked(game, "A", command)
                actual = game.state.cards[selected.object_id]
                source = game.state.cards[source.object_id]
                self.assertEqual(("exile", False), (actual.zone, actual.face_down))
                self.assertIn(actual.printed_name, json.dumps(game.packet("spectator", full=True)))
                self.assertEqual("exile", game.state.cards[unrelated.object_id].zone)
                self.assertEqual({"W", "R"}, self.colors(game, source))
                with patch("quorune.linked_ability_exile_state.current_linked_ability_exile", return_value=()):
                    self.assertEqual(set(), self.colors(game, source))
                if mode == "automatic":
                    payment = game.state.cards[payment.object_id]
                    self.cast(game, "A", payment)
                    self.assertEqual("stack", game.state.cards[payment.object_id].zone)
                else:
                    self.priority(game, "A")
                    actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    offer = next(row for row in actions if row.get("source") == source.ref and row.get("ability"))
                    activate = {"action_id": offer["id"]}
                    self.assert_rejected(game, "A", {**activate, "mana_output": {"U": 1}})
                    self.act_checked(game, "A", {**activate, "mana_output": {"R": 1}})
                    self.assertEqual(1, game.state.players["A"].mana_pool["R"])
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.replay(game, load=True)

    def test_actual_decline_and_colorless_imprint_leave_no_productive_mode(self):
        for take in (False, True):
            with self.subTest(take=take):
                game = self.session(77202380 + take)
                source = self.add(game.engine, "Chrome Mox", zone="hand")
                card = self.add(game.engine, "All Is Dust", zone="hand", ref="colorless-private")
                self.prepare(game, mana={"C": 0})
                self.checkpoint(game)
                self.assertEqual("semantic.choice", self.offer(game, source).kind)
                self.act_checked(game, "A", {"action_id": "choose", "cards": [card.ref] if take else []})
                self.assertEqual("exile" if take else "hand", game.state.cards[card.object_id].zone)
                if not take:
                    self.assertNotIn(card.ref, json.dumps(game.packet("spectator", full=True)))
                self.assertFalse(self.colors(game, source))
                self.replay(game, load=True)


    def test_actual_duplicate_imprint_and_reentry_scope_use_current_linked_colors(self):
        game = self.session(77202440)
        source = self.add(game.engine, "Chrome Mox", zone="hand", ref="original-mox")
        other = self.add(game.engine, "Chrome Mox", ref="other-mox")
        self.add(game.engine, "Panharmonicon")
        first = self.add(game.engine, "Boros Charm", zone="hand", ref="first-imprint")
        second = self.add(game.engine, "Grizzly Bears", zone="hand", ref="second-imprint")
        bounce = self.add(game.engine, "Boomerang", zone="hand", ref="bounce")
        self.add(game.engine, "All Is Dust", zone="hand", ref="decline-card")
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)

        def enter(card):
            self.cast(game, "A", card)
            self.resolve_top(game)
            if game.state.pending_decision.kind == "trigger.order":
                refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
                self.act_checked(game, "A", {"action_id": "order", "triggers": refs})
            self.assertEqual(2, sum(item.source_object_id == card.object_id for item in game.state.stack))

        enter(source)
        for card in (first, second):
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            self.act_checked(game, "A", {"action_id": "choose", "cards": [card.ref]})
        self.assertEqual({"W", "R", "G"}, self.colors(game, source))
        self.assertFalse(self.colors(game, other))
        original = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "A", bounce, [source.ref])
        self.resolve_top(game)
        source = game.state.cards[source.object_id]
        self.assertEqual("hand", source.zone)
        enter(source)
        for _ in range(2):
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            self.act_checked(game, "A", {"action_id": "choose", "cards": []})
        current = game.state.cards[source.object_id]
        self.assertNotEqual(original, current.logical_object_id)
        self.assertFalse(self.colors(game, current))
        self.assertEqual(["exile", "exile"], [game.state.cards[x.object_id].zone for x in (first, second)])
        self.replay(game, load=True)

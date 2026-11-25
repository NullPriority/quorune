"""Exercise mandatory self-reveal and owner-library redirection at resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.self_graveyard_exile_model import SELF_GRAVEYARD_SHUFFLE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Blightsteel Colossus", "Darksteel Colossus", "Legacy Weapon", "Nexus of Fate", "Progenitus")
HANDLER = "replacement.zone.self-graveyard-library-shuffle.v1"


class SelfGraveyardRevealAndOwnerShuffleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "self-graveyard-reveal-and-owner-shuffle.sqlite3"
        fixture = ROOT / "tests/fixtures/self-graveyard-reveal-and-owner-shuffle-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Reveal and owner library", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return card, semantics.programs_for_oracle(card.oracle_id)

    def test_all_five_originals_register_one_mandatory_self_replacement_and_keep_siblings(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, programs = self.programs(name)
                replacement, = [item for item in programs if item.event == "zone.change"]
                self.assertEqual("all", replacement.active_zone)
                self.assertIsNone(replacement.target_schema)
                handler, = replacement.handlers
                self.assertEqual(HANDLER, handler["handler_id"])
                self.assertEqual(1, handler["schema_version"])
                self.assertIn(SELF_GRAVEYARD_SHUFFLE_CAPABILITY, replacement.capability_dependencies)
                capabilities = tuple(value for value in replacement.capability_dependencies
                                     if value != SELF_GRAVEYARD_SHUFFLE_CAPABILITY)
                omitted = replace(replacement, capability_dependencies=capabilities,
                    capability_closure=self.registry.closure(capabilities, profile="commander_review").to_dict())
                binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile="commander_review")
                self.assertIn("capability:undeclared_runtime_dependency:" + SELF_GRAVEYARD_SHUFFLE_CAPABILITY,
                              binding["blockers"])
                if name == "Nexus of Fate":
                    self.assertTrue(any(item.event == "resolve" and item.effects for item in programs))
                elif name == "Legacy Weapon":
                    self.assertTrue(any(item.event == "activate" and item.effects for item in programs))
                for changed in (
                    card.oracle_text.replace("its owner's library", "your library"),
                    card.oracle_text.replace("from anywhere", "from the battlefield"),
                    card.oracle_text.replace("reveal " + name + " and ", ""),
                    card.oracle_text.replace("reveal " + name, "reveal a card in your hand"),
                ):
                    candidate = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
                    self.assertNotEqual("exact", candidate.status, changed)

    def shuffles(self, game, seat):
        return game.state.players[seat].stats.get("shuffle_count", 0)

    def reveal_before_shuffles(self, game, marker, originals, owner, *, shuffle_count=None):
        events = [event for event in game.state.events if event.event_id > marker]
        refs = {card.ref for card in originals}
        reveals = [event for event in events if event.code == "card.reveal" and event.details.get("object") in refs]
        self.assertEqual({card.printed_name for card in originals}, {event.details["card"] for event in reveals})
        self.assertEqual(len(originals), len(reveals))
        public_viewers = {*game.engine.seats, "spectator"}
        self.assertTrue(all(public_viewers <= set(event.visibility) for event in reveals))
        shuffles = [event for event in events if event.code == "library.shuffle" and owner in event.changed_players]
        self.assertEqual(len(originals) if shuffle_count is None else shuffle_count, len(shuffles))
        self.assertLess(max(event.event_id for event in reveals), min(event.event_id for event in shuffles))

    def activate(self, game, seat, source, targets):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_nexus_finishes_its_extra_turn_result_and_replaces_its_stack_exit(self):
        game = self.session(61343601)
        nexus = self.add(game.engine, "Nexus of Fate", zone="hand")
        self.assert_closed(game, nexus)
        self.prepare(game, mana={"C": 5, "U": 2})
        self.checkpoint(game)
        marker = game.state.event_sequence
        before = self.shuffles(game, "A")
        self.cast_for(game, "A", nexus)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(["A"], [turn.player for turn in game.state.extra_turns])
        self.assertIn(nexus.object_id, game.state.players["A"].zones["library"])
        self.assertNotIn(nexus.object_id, game.state.players["A"].zones["graveyard"])
        self.assertEqual(before + 1, self.shuffles(game, "A"), "The selected replacement shuffles the owner's library once")
        self.reveal_before_shuffles(game, marker, [nexus], "A")
        self.replay(game, load=True)

    def test_omitting_the_shuffle_obligation_fails_the_native_nexus_witness(self):
        with patch("quorune.zone_transitions.finish_self_graveyard_shuffles", return_value=None):
            with self.assertRaisesRegex(AssertionError, "The selected replacement shuffles"):
                self.test_actual_nexus_finishes_its_extra_turn_result_and_replaces_its_stack_exit()

    def test_actual_mill_reveals_two_hidden_originals_and_preserves_both_shuffle_occurrences(self):
        game = self.session(61343602)
        engine = game.engine
        millstone = self.add(engine, "Millstone")
        originals = [self.add(engine, name, seat="B", zone="library") for name in ORIGINALS[:2]]
        library = game.state.players["B"].zones["library"]
        for card in reversed(originals):
            library.remove(card.object_id)
            library.append(card.object_id)
        for card in (millstone, *originals):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            for card in originals:
                self.assertNotIn(card.printed_name, packet)
                self.assertNotIn(card.ref, packet)
        marker = game.state.event_sequence
        before = self.shuffles(game, "B")
        self.activate(game, "A", millstone, ["B"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        for original in originals:
            live = game.state.cards[original.object_id]
            self.assertEqual(("library", "B"), (live.zone, live.owner))
            self.assertNotEqual(original.logical_object_id, live.logical_object_id)
            self.assertEqual(([], []), (live.known_to, live.revealed_to))
        self.assertFalse(game.state.players["B"].zones["graveyard"])
        self.assertEqual(before + 2, self.shuffles(game, "B"))
        self.reveal_before_shuffles(game, marker, originals, "B")
        self.replay(game, load=True)

    def test_actual_borrowed_legacy_weapon_can_activate_then_shuffles_into_its_owners_library(self):
        game = self.session(61343603)
        engine = game.engine
        source = self.add(engine, "Legacy Weapon", seat="B")
        engine.change_control(source.object_id, "A", reason="Prepare borrowed Weapon")
        target = self.add(engine, "Sol Ring", seat="D")
        removal = self.add(engine, "Disenchant", seat="C", zone="hand")
        for card in (source, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={color: 1 for color in "WUBRG"})
        game.state.players["C"].mana_pool.update({"W": 1, "C": 1})
        self.checkpoint(game)
        actor_shuffles, owner_shuffles = (self.shuffles(game, seat) for seat in "AB")
        self.activate(game, "A", source, [target.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.cast_for(game, "C", removal, [source.ref])
        mark = game.state.event_sequence
        self.resolve_top(game)
        self.assertIn(source.object_id, game.state.players["B"].zones["library"])
        self.assertEqual(actor_shuffles, self.shuffles(game, "A"))
        self.assertEqual(owner_shuffles + 1, self.shuffles(game, "B"))
        self.reveal_before_shuffles(game, mark, [source], "B")
        self.replay(game, load=True)

    def test_actual_commander_redirection_keeps_the_selected_reveal_and_owner_shuffle(self):
        self.deck = DeckDefinition("Progenitus commander", [
            DeckEntry("Progenitus", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Progenitus"])
        game = self.session(61343604)
        origin = game.state.cards[game.state.players["A"].zones["command"][0]]
        game.engine.move_card(origin.object_id, "battlefield", controller="C", reason="Prepare borrowed commander sacrifice")
        edict = self.add(game.engine, "Diabolic Edict", seat="B", zone="hand")
        self.assert_closed(game, origin)
        self.assert_closed(game, edict)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"B": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", edict, ["C"])
        marker = game.state.event_sequence
        before = self.shuffles(game, "A")
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertNotEqual("replacement.order", pending.kind)
        sacrificed = game.act("pilot:C", {"action_id": "choose", "cards": [origin.ref]})
        self.assertTrue(sacrificed.ok, sacrificed.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertEqual("battlefield", game.state.cards[origin.object_id].zone)
        self.assertEqual(before, self.shuffles(game, "A"))
        self.assertFalse(any(event.code == "card.reveal" and event.event_id > marker for event in game.state.events))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        chosen, = [row["id"] for row in options if not row.get("decline")]
        action = {"action_id": "choose", "replacement": chosen}
        digest = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", action).ok)
        self.assertEqual(digest, authoritative_state_hash(game.state))
        result = game.act("pilot:A", action)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("command", game.state.cards[origin.object_id].zone)
        self.assertEqual(before + 1, self.shuffles(game, "A"))
        self.reveal_before_shuffles(game, marker, [origin], "A")
        self.replay(game, load=True)

    def test_actual_spell_copy_shuffles_its_owners_library_and_reveals_no_physical_card(self):
        game = self.session(61343605)
        nexus = self.add(game.engine, "Nexus of Fate", zone="hand")
        copy_spell = self.add(game.engine, "Twincast", seat="B", zone="hand")
        for card in (nexus, copy_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 2})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        a_shuffles, b_shuffles = (self.shuffles(game, seat) for seat in "AB")
        self.cast_for(game, "A", nexus)
        original_ref = game.state.stack[-1].ref
        self.cast_for(game, "B", copy_spell, [original_ref])
        self.resolve_top(game)
        copy, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual("B", game.state.cards[copy.card_object_id].owner)
        marker = game.state.event_sequence
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(["B"], [turn.player for turn in game.state.extra_turns])
        self.assertEqual("stack", game.state.cards[nexus.object_id].zone)
        self.assertEqual(a_shuffles, self.shuffles(game, "A"))
        self.assertEqual(b_shuffles + 1, self.shuffles(game, "B"))
        self.assertFalse(any(event.code == "card.reveal" and event.event_id > marker for event in game.state.events))
        self.resolve_top(game)
        self.assertIn(nexus.object_id, game.state.players["A"].zones["library"])
        self.assertEqual(a_shuffles + 1, self.shuffles(game, "A"))
        self.replay(game, load=True)

    def test_actual_merged_departure_reveals_both_physical_cards_and_shuffles_once(self):
        game = self.session(61343606)
        engine = game.engine
        colossus = self.add(engine, "Darksteel Colossus")
        part = self.add(engine, "Dreamtail Heron", zone="hand")
        edict = self.add(engine, "Diabolic Edict", seat="B", zone="hand")
        for card in (colossus, part, edict):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"B": 1, "C": 1})
        self.checkpoint(game)
        offer = self.action(game, "A", game.state.cards[part.object_id])
        option, = [row["id"] for row in offer["cost_options"] if row["id"].startswith("mutate")]
        mutate = {"action_id": offer["id"], "cost_option": option, "targets": [colossus.ref], "pay": "auto"}
        cast = game.act("pilot:A", mutate)
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        merge = game.act("pilot:A", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(merge.ok, merge.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIsNotNone(game.state.cards[colossus.object_id].permanent_composition)

        before = self.shuffles(game, "A")
        marker = game.state.event_sequence
        self.cast_for(game, "B", edict, ["A"])
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        sacrifice = game.act("pilot:A", {"action_id": "choose", "cards": [colossus.ref]})
        self.assertTrue(sacrifice.ok, sacrifice.summary)
        pending = game.state.pending_decision
        self.assertEqual(("permanent.composition_order", ["A"]), (pending.kind, pending.actors))
        frame = pending.continuation["apnap_composition_order"]
        self.assertEqual("B", frame["continuation"]["semantic_frame"]["controller"])
        self.assertEqual("library", frame["request"]["subjects"][0]["destination"])
        self.assertEqual(before, self.shuffles(game, "A"))
        game = self.replay(game, load=True)
        ordered = game.act("pilot:A", {"action_id": "order", "cards": [part.ref, colossus.ref]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertIsNone(self.resolve(game))
        for card in (colossus, part):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.assertEqual(before + 1, self.shuffles(game, "A"))
        self.reveal_before_shuffles(game, marker, [colossus, part], "A", shuffle_count=1)
        self.replay(game, load=True)

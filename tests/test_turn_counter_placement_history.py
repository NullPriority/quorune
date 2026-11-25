"""Exact current-turn +1/+1 placement recipients through live layer-six reads."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameState, TurnHistory
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import (
    COUNTER_PLACEMENT_HISTORY_CAPABILITY,
    PublicHistoryQuerySpec,
    current_plus_one_counter_placement_query,
    matching_public_history_events,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


BEAST = "Beast, Erudite Aerialist"
PALADIN = "Sigardian Paladin"
LOKI = "Kid Loki"
SCHEMA = "plus_one_counter_placement_history_version"


class TurnCounterPlacementHistoryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "turn-counter-placement-history.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/turn-counter-placement-history-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Turn counter placement witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def live(self, game, card):
        return game.state.cards[card.object_id]

    def has_keyword(self, game, card, keyword):
        data = game.engine._effective_card_data(self.live(game, card))
        return keyword.casefold() in {value.casefold() for value in data.get("keywords", ())}

    def placements(self, game, actor):
        return matching_public_history_events(game.state, actor, current_plus_one_counter_placement_query())

    def scene(self, seed, *, beast=False):
        game = self.session(seed)
        loki = self.add(game.engine, LOKI)
        paladin = self.add(game.engine, PALADIN)
        source = self.add(game.engine, BEAST) if beast else None
        return game, loki, paladin, source

    def cast_resolve(self, game, seat, spell, *targets, reserve=None):
        self.cast_for(game, seat, spell, list(targets), reserve=reserve)
        self.assertIsNone(self.resolve(game))

    def test_complete_originals_keep_distinct_history_scopes_and_require_the_authority(self):
        game, loki, paladin, beast = self.scene(122601, beast=True)
        self.assertEqual(1, game.state.turn_history.to_dict()[SCHEMA])
        for card in (loki, paladin, beast):
            self.assert_closed(game, card)
            programs = game.engine.semantics.programs_for_oracle(card.oracle_id)
            program, = [value for value in programs if COUNTER_PLACEMENT_HISTORY_CAPABILITY in value.capability_dependencies]
            deps = tuple(key for key in program.capability_dependencies if key != COUNTER_PLACEMENT_HISTORY_CAPABILITY)
            withdrawn = replace(program, capability_dependencies=deps,
                                capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            result = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + COUNTER_PLACEMENT_HISTORY_CAPABILITY, result["blockers"])
            if card is loki:
                self.assertIn("counter-placement-keyword-grant", program.handlers[0]["handler_id"])
            else:
                raw = program.handlers[0]["source_condition"]["history_query"]
                query = PublicHistoryQuerySpec.from_dict(raw)
                self.assertEqual((card is beast, ("creature",) if card is paladin else ()),
                                 (query.source_only, query.types_all))
                for changed in ({"schema_version": 1}, {"source_only": 1}, {"relation": "opponents"}):
                    with self.assertRaises(ValueError):
                        PublicHistoryQuerySpec.from_dict({**raw, **changed})

        original = self.db.lookup(BEAST, fuzzy=False)
        for text in (original.oracle_text.replace("on Beast this turn", "on a Beast this turn"),
                     original.oracle_text.replace("+1/+1 counters", "charge counters"),
                     original.oracle_text.replace("this turn, he has", "last turn, he has")):
            self.assertNotEqual(original.oracle_text, text)
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def test_paid_placements_follow_actor_incarnation_and_turn_in_multiplayer_replay(self):
        game, loki, paladin, beast = self.scene(122602, beast=True)
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        untouched = self.add(game.engine, "Grizzly Bears", ref="unplaced-friend")
        untouched.counters["+1/+1"] = 1
        their_spell = self.add(game.engine, "Burst of Strength", seat="B", zone="hand", ref="their-burst")
        our_spell = self.add(game.engine, "Burst of Strength", zone="hand", ref="our-foreign-burst")
        our_next_spell = self.add(game.engine, "Invigorating Surge", zone="hand", ref="our-self-surge")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        removal = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        private = self.add(game.engine, "Cloudshift", seat="D", zone="hand", ref="hidden-d-card")
        self.prepare(game, mana={"G": 2, "W": 1, "C": 2})
        game.state.players["B"].mana_pool.update({"G": 1, "R": 1})
        self.checkpoint(game)

        self.cast_resolve(game, "B", their_spell, beast.ref)
        self.assertEqual([beast.logical_object_id], [event.object_incarnation for event in self.placements(game, "B")])
        self.assertFalse(self.has_keyword(game, beast, "flying"))
        self.assertFalse(self.has_keyword(game, beast, "hexproof"))
        self.assertFalse(self.has_keyword(game, paladin, "lifelink"))
        self.assertFalse(self.has_keyword(game, untouched, "hexproof"))

        self.cast_resolve(game, "A", our_spell, target.ref)
        self.assertTrue(self.has_keyword(game, paladin, "trample"))
        self.assertTrue(self.has_keyword(game, paladin, "lifelink"))
        self.assertFalse(self.has_keyword(game, target, "hexproof"))
        self.assertFalse(self.has_keyword(game, beast, "flying"))
        self.cast_resolve(game, "A", our_next_spell, beast.ref, reserve={"W": 1})
        self.assertTrue(self.has_keyword(game, beast, "flying"))
        self.assertTrue(self.has_keyword(game, beast, "hexproof"))
        self.assertFalse(self.has_keyword(game, loki, "hexproof"))

        self.priority(game, "B")
        cast = self.action(game, "B", removal)
        self.assertIn(untouched.ref, cast["target_schema"]["legal_refs"])
        self.assertNotIn(beast.ref, cast["target_schema"]["legal_refs"])
        command = {"action_id": cast["id"], "targets": [beast.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {**command, "targets": [untouched.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.live(game, untouched).zone)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))

        original_incarnation = self.live(game, beast).logical_object_id
        self.cast_resolve(game, "A", blink, beast.ref)
        self.assertNotEqual(original_incarnation, self.live(game, beast).logical_object_id)
        self.assertFalse(self.has_keyword(game, beast, "flying"))
        self.assertFalse(self.has_keyword(game, beast, "hexproof"))
        self.assertTrue(self.has_keyword(game, paladin, "lifelink"))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((), self.placements(game, "A"))
        self.assertFalse(self.has_keyword(game, paladin, "lifelink"))
        self.replay(game, load=True)

    def test_actual_entry_seals_committed_quantity_and_replacement_can_prevent_it(self):
        for offset, replacement, amount in ((0, "Doubling Season", 2), (1, "Solemnity", 0)):
            with self.subTest(replacement=replacement):
                game, _, paladin, _ = self.scene(122610 + offset)
                self.add(game.engine, replacement)
                hydra = self.add(game.engine, "Mossborn Hydra", zone="hand")
                self.prepare(game, mana={"G": 1, "C": 2})
                self.checkpoint(game)
                self.cast_resolve(game, "A", hydra)
                hydra = self.live(game, hydra)
                self.assertEqual(amount, hydra.counters.get("+1/+1", 0))
                events = self.placements(game, "A")
                self.assertEqual([(hydra.logical_object_id, amount)] if amount else [],
                                 [(event.object_incarnation, event.amount) for event in events])
                self.assertEqual(bool(amount), self.has_keyword(game, paladin, "lifelink"))
                self.assertEqual(bool(amount), self.has_keyword(game, hydra, "hexproof"))
                self.replay(game, load=True)

    def test_as_placed_type_and_current_recipient_type_are_independent(self):
        game, _, paladin, _ = self.scene(122620)
        artifact = self.add(game.engine, "Darksteel Citadel")
        artifact.counters["+1/+1"] = 1
        proliferate = self.add(game.engine, "Contentious Plan", zone="hand")
        aura = self.add(game.engine, "Ensoul Artifact", zone="hand")
        grow = self.add(game.engine, "Invigorating Surge", zone="hand")
        remove = self.add(game.engine, "Disenchant", zone="hand")
        self.prepare(game, mana={"U": 2, "G": 1, "W": 1, "C": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", proliferate)
        self.until(game, lambda current: current.state.pending_decision.kind != "priority")
        self.replay(game, load=True)
        chosen = game.act("pilot:A", {"action_id": "choose", "objects": [artifact.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        event, = self.placements(game, "A")
        self.assertEqual({"artifact", "land"}, set(event.types))
        self.assertFalse(self.has_keyword(game, artifact, "hexproof"))
        self.assertFalse(self.has_keyword(game, paladin, "lifelink"))

        self.cast_resolve(game, "A", aura, artifact.ref)
        self.assertTrue(self.has_keyword(game, artifact, "hexproof"))
        self.assertFalse(self.has_keyword(game, paladin, "lifelink"))
        self.cast_resolve(game, "A", grow, artifact.ref)
        self.assertTrue(self.has_keyword(game, paladin, "lifelink"))
        self.cast_resolve(game, "A", remove, aura.ref)
        self.assertFalse(self.has_keyword(game, artifact, "hexproof"))
        self.assertTrue(self.has_keyword(game, paladin, "lifelink"))
        self.replay(game, load=True)

    def test_legacy_absence_and_invalid_history_round_trip_without_inventing_placements(self):
        game, _, paladin, beast = self.scene(122630, beast=True)
        spell = self.add(game.engine, "Invigorating Surge", zone="hand")
        history = game.state.turn_history.to_dict()
        for value in (True, 0, 2, "1"):
            with self.assertRaises(ValueError):
                TurnHistory.from_dict({**history, SCHEMA: value})
        game.state.turn_history = TurnHistory.from_dict({key: value for key, value in history.items() if key != SCHEMA})
        snapshot = game.state.to_dict()
        self.assertEqual(snapshot, GameState.from_dict(snapshot).to_dict())
        self.assertNotIn(SCHEMA, snapshot["turn_history"])
        self.prepare(game, mana={"G": 1, "C": 2})
        self.checkpoint(game)
        self.cast_resolve(game, "A", spell, beast.ref)
        self.assertGreater(self.live(game, beast).counters["+1/+1"], 0)
        self.assertIsNone(self.placements(game, "A"))
        self.assertFalse(self.has_keyword(game, beast, "flying"))
        self.assertFalse(self.has_keyword(game, paladin, "trample"))
        self.assertFalse(self.has_keyword(game, beast, "hexproof"))
        self.assertNotIn(SCHEMA, game.state.turn_history.to_dict())
        self.replay(game, load=True)

    def test_omitting_live_history_recipients_is_killed_by_the_original_gameplay(self):
        def witness(seed):
            game, _, _, _ = self.scene(seed)
            target = self.add(game.engine, "Grizzly Bears")
            growth = self.add(game.engine, "Invigorating Surge", zone="hand")
            self.prepare(game, mana={"G": 1, "C": 2})
            self.checkpoint(game)
            self.cast_resolve(game, "A", growth, target.ref)
            self.assertTrue(self.has_keyword(game, target, "hexproof"), "Live placement recipient omitted")

        witness(122640)
        with patch("quorune.card_programs.runtime._counter_placement_recipient_identities", return_value=()):
            with self.assertRaisesRegex(AssertionError, "Live placement recipient omitted"):
                witness(122641)

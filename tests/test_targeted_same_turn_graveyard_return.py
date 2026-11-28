"""Brought Back uses the same current-arrival predicate for offers, commands and revalidation."""

from dataclasses import replace
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
from quorune.public_zone_moves import TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class TargetedSameTurnGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-same-turn-graveyard-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/targeted-same-turn-graveyard-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted previous battlefield arrivals", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_two_optional_targets_to_the_tapped_return_set(self):
        admitted, semantics = register_card(self.db, self.registry, "Brought Back")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Brought Back", fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        schema = program.target_schema
        effect, = program.effects
        spec = effect["set"]
        self.assertEqual((2, "you", True), (schema["up_to"], schema["owner_relation"],
                                           schema["graveyard_from_battlefield_this_turn"]))
        self.assertEqual((TARGETED_SAME_TURN_GRAVEYARD_RETURN_VERSION, True, "$targets"),
                         (spec["schema_version"], spec["battlefield_tapped"], spec["target_refs"]))
        for altered in (
            replace(program, effects=[{**effect, "set": {**spec, "target_refs": "$controller"}}]),
            replace(program, target_schema={**schema, "graveyard_from_battlefield_this_turn": False}),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("up to two", "up to three"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:" + seat, command)
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_legal_offer_filters_arrivals_and_the_original_surviving_target_returns_tapped(self):
        game = self.session(60824001)
        spell = self.add(game.engine, "Brought Back", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        removal = self.add(game.engine, "Cremate", seat="B", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="brought-returns")
        second = self.add(game.engine, "Grizzly Bears", ref="brought-exiled-in-response")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="brought-foreign-current-arrival")
        older = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="brought-previous-arrival")
        for card in (spell, wrath, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 4})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", wrath, reserve={"W": 2})
        self.resolve_top(game)
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        self.assertEqual({first.ref, second.ref}, set(offer["target_schema"]["legal_refs"]))
        for target in (foreign, older):
            self.reject(game, {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.cast_for(game, "A", spell, [first.ref, second.ref])
        self.cast_for(game, "B", removal, [second.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A", True),
                         (self.current(game, first).zone, self.current(game, first).controller, self.current(game, first).tapped))
        self.assertEqual(["exile", "graveyard", "graveyard"],
                         [self.current(game, card).zone for card in (second, foreign, older)])
        self.replay(game, load=True)

    def test_actual_zero_targets_remains_legal_with_no_arrival_and_does_not_return_an_old_card(self):
        game = self.session(60824002)
        spell = self.add(game.engine, "Brought Back", zone="hand")
        old = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"W": 2})
        self.checkpoint(game)
        offer = self.action(game, "A", spell)
        self.assertEqual([], offer["target_schema"]["legal_refs"])
        self.reject(game, {"action_id": offer["id"], "targets": [old.ref], "pay": "auto"})
        self.cast_for(game, "A", spell, [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, old).zone)
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_brought_back_uses_aura_entry_and_returns_both_original_targets_tapped(self):
        game = self.session(60824003)
        spell = self.add(game.engine, "Brought Back", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        army = self.add(game.engine, "Raise the Alarm", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        aura = self.add(game.engine, "Pacifism")
        attach_objects(game.state.cards, aura, creature, source_timestamp=aura.zone_timestamp, players=game.state.players)
        for card in (spell, wrath, army, aura):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "W": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", wrath, reserve={"C": 1, "W": 3})
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", army, reserve={"W": 2})
        self.resolve_top(game)
        existing = next(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.cast_for(game, "A", spell, [aura.ref, creature.ref])
        pending = self.resolve(game)
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        self.reject(game, {"action_id": "choose", "aura_target": creature.ref})
        self.assertEqual(["graveyard"] * 2, [self.current(game, card).zone for card in (aura, creature)])
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "aura_target": existing.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([("battlefield", True)] * 2,
                         [(self.current(game, card).zone, self.current(game, card).tapped) for card in (aura, creature)])
        self.assertEqual(existing.object_id, self.current(game, aura).attached_to)
        self.replay(game, load=True)

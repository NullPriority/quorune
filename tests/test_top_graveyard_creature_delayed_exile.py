"""Native ordered graveyard return, haste, delayed exile, and replay."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.replacement.operations import SetField
from quorune.replacement_effects import ReplacementClass, ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import zone_replacements
from scripts.build_test_database import build_fixture_database


class TopGraveyardCreatureDelayedExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordered-graveyard-delayed-exile.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [
                fixtures / "top-graveyard-creature-delayed-exile-cards.json",
                fixtures / "reanimation-delayed-sacrifice-cards.json",
                fixtures / "linked-library-imprint-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Ordered graveyard witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def ready(self, game, *cards, mana=None, opponent_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        # Leave room for the first natural draw without an unrelated cleanup choice.
        for player in game.state.players.values():
            for object_id in list(player.zones["hand"])[5:]:
                if game.state.cards[object_id].printed_name == "Plains":
                    game.engine.move_card(object_id, "library", position="bottom", log=False)
        self.prepare(game, mana=mana or {"B": 1, "C": 1})
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def graveyard(self, game, seat="A"):
        return list(game.state.players[seat].zones["graveyard"])

    def pending_exiles(self, game):
        return [
            delay for delay in game.state.delayed_triggers
            if delay.active and delay.label == "Delayed exile"
        ]

    def resolve_next_exile(self, game, active):
        self.until(game, lambda current: (
            current.state.step == "end_step"
            and any(item.label == "Delayed exile" for item in current.state.stack)
        ))
        self.assertEqual(active, game.state.active_player)
        item, = (item for item in game.state.stack if item.label == "Delayed exile")
        self.assertEqual(("A", []), (item.controller, item.targets))
        self.resolve_top(game)
        self.assertEqual([], self.pending_exiles(game))

    def assert_hasted_arrival(self, game, card):
        entered = self.current(game, card)
        self.assertEqual(("battlefield", "A", False), (entered.zone, entered.controller, entered.tapped))
        self.assertIn("haste", game.engine._combat_keywords(entered))
        delay, = self.pending_exiles(game)
        self.assertEqual(("A", True), (delay.controller, delay.once))
        return entered

    def test_actual_shallow_grave_skips_noncreatures_and_grants_a_legal_attack(self):
        game = self.session(404603701)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        bottom = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="lower-creature")
        chosen = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="highest-creature")
        artifact = self.add(game.engine, "Sol Ring", zone="graveyard")
        land = self.add(game.engine, "Island", zone="graveyard")
        other_seat = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard", ref="opponent-creature")
        self.ready(game, spell)
        self.assertEqual(
            [bottom.object_id, chosen.object_id, artifact.object_id, land.object_id],
            self.graveyard(game),
        )

        offered = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:A", {"action_id": offered["id"], "targets": [bottom.ref], "pay": "auto"})
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.resolve_top(game)
        returned = self.assert_hasted_arrival(game, chosen)
        self.assertEqual(
            [bottom.object_id, artifact.object_id, land.object_id, spell.object_id], self.graveyard(game),
        )
        self.assertEqual([other_seat.object_id], self.graveyard(game, "C"))
        game = self.replay(game, load=True)

        life_before = game.state.players["B"].life
        self.attack(game, [(returned, "B")])
        self.resolve_next_exile(game, "A")
        self.assertEqual(life_before - 2, game.state.players["B"].life)
        self.assertEqual("exile", self.current(game, chosen).zone)
        self.assertEqual("graveyard", self.current(game, bottom).zone)
        self.replay(game, load=True)

    def test_actual_response_changes_the_highest_creature_before_resolution(self):
        game = self.session(404603702)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        previous_top = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="initial-creature")
        noncreature = self.add(game.engine, "Sol Ring", zone="graveyard")
        later_top = self.add(game.engine, "Grizzly Bears", ref="creature-killed-in-response")
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.ready(game, spell, removal, opponent_mana={"B": 2, "C": 1})

        self.cast_for(game, "A", spell)
        self.assertEqual([previous_top.object_id, noncreature.object_id], self.graveyard(game))
        self.cast_for(game, "B", removal, [later_top.ref])
        self.resolve_top(game)
        self.assertEqual(
            [previous_top.object_id, noncreature.object_id, later_top.object_id], self.graveyard(game),
        )
        self.assertEqual([spell.object_id], [item.card_object_id for item in game.state.stack])
        self.resolve_top(game)
        self.assert_hasted_arrival(game, later_top)
        self.assertEqual("graveyard", self.current(game, previous_top).zone)
        self.resolve_next_exile(game, "A")
        self.assertEqual("exile", self.current(game, later_top).zone)
        self.replay(game, load=True)

    def test_destination_replacement_redirects_only_the_selected_card(self):
        game = self.session(404603703)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        lower = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="redirect-lower-creature")
        chosen = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="redirect-selected-creature")
        self.ready(game, spell)

        redirect = ReplacementEffect(
            effect_id="test:redirect-return-destination",
            source_id="test:entry-replacement",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "object_ref": {"eq": chosen.ref},
                "origin": {"eq": "graveyard"},
                "destination": {"eq": "battlefield"},
            },
            operations=(SetField("destination", "exile"),),
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect_with_redirect):
            self.cast_for(game, "A", spell)
            self.resolve_top(game)
            redirected = self.current(game, chosen)
            incarnation = redirected.logical_object_id
            self.assertEqual(("exile", False), (redirected.zone, redirected.face_down))
            self.assertNotIn("haste", game.engine._combat_keywords(redirected))
            self.assertEqual("graveyard", self.current(game, lower).zone)
            self.assertTrue(any(
                event.code == "replacement.apply" and event.details.get("destination") == "exile"
                and event.details.get("object") == chosen.ref
                for event in game.state.events
            ))
            delay, = self.pending_exiles(game)
            self.assertEqual([], delay.stack_template["context"]["dynamic_effects"])
            game = self.replay(game, load=True)
            self.resolve_next_exile(game, "A")
            self.assertEqual(
                ("exile", incarnation),
                (self.current(game, chosen).zone, self.current(game, chosen).logical_object_id),
            )
            self.replay(game, load=True)

    def test_blinked_creature_loses_haste_and_escapes_the_original_delay(self):
        game = self.session(404603704)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.ready(game, spell, blink, mana={"B": 1, "C": 1, "W": 1})
        self.cast_for(game, "A", spell, reserve={"W": 1})
        self.resolve_top(game)
        original = self.assert_hasted_arrival(game, creature).logical_object_id

        self.cast_for(game, "A", blink, [creature.ref])
        self.resolve_top(game)
        current = self.current(game, creature)
        reentered = current.logical_object_id
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, reentered)
        self.assertNotIn("haste", game.engine._combat_keywords(current))
        game = self.replay(game, load=True)
        self.resolve_next_exile(game, "A")
        self.assertEqual(
            ("battlefield", reentered),
            (self.current(game, creature).zone, self.current(game, creature).logical_object_id),
        )
        self.replay(game, load=True)

    def test_after_end_step_begins_haste_expires_and_next_opponent_end_step_exiles(self):
        game = self.session(404603705)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.ready(game, spell)
        self.until(game, lambda current: (
            current.state.active_player == "A" and current.state.step == "end_step"
        ))
        # Precombat mana emptied while advancing. Seed the instant's mana before
        # the checkpoint so every cast, pass, and triggered move remains replayable.
        game.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        self.assert_hasted_arrival(game, creature)
        self.assertEqual([], game.state.stack)
        game = self.replay(game, load=True)

        self.until(game, lambda current: current.state.active_player == "B")
        arrived = self.current(game, creature)
        self.assertEqual("battlefield", arrived.zone)
        self.assertNotIn("haste", game.engine._combat_keywords(arrived))
        self.assertEqual(1, len(self.pending_exiles(game)))
        self.resolve_next_exile(game, "B")
        self.assertEqual("exile", self.current(game, creature).zone)
        self.replay(game, load=True)

    def test_no_creature_is_a_legal_cast_and_leaves_an_inert_delay(self):
        game = self.session(404603706)
        spell = self.add(game.engine, "Shallow Grave", zone="hand")
        object_in_graveyard = self.add(game.engine, "Sol Ring", zone="graveyard")
        foreign_creature = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard")
        self.ready(game, spell)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        delay, = self.pending_exiles(game)
        self.assertEqual([], delay.stack_template["context"]["dynamic_effects"])
        self.resolve_next_exile(game, "A")
        self.assertEqual("graveyard", self.current(game, object_in_graveyard).zone)
        self.assertEqual("graveyard", self.current(game, foreign_creature).zone)
        self.replay(game, load=True)

    def test_actual_corpse_dance_buyback_returns_the_spell_and_retains_the_exile(self):
        game = self.session(404603707)
        spell = self.add(game.engine, "Corpse Dance", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        payment = {"B": 1, "C": 4}
        self.ready(game, spell, mana=payment)

        offered = self.action(game, "A", spell)
        self.assertIn("buyback", {option["id"] for option in offered["cost_options"]})
        result = game.act("pilot:A", {
            "action_id": offered["id"], "pay": "manual", "payment": payment,
            "cost_option": "buyback", "targets": [],
        })
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, spell).zone)
        self.assert_hasted_arrival(game, creature)
        game = self.replay(game, load=True)
        self.resolve_next_exile(game, "A")
        self.assertEqual("exile", self.current(game, creature).zone)
        self.assertEqual("hand", self.current(game, spell).zone)
        self.replay(game, load=True)

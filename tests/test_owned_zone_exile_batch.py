"""One owned-zone exile occurrence from actual attack, payment and cascade paths."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import OWNED_ZONE_EXILE_CAPABILITY, OWNED_ZONE_EXILE_EVENT
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


CARD = "Laelia, the Blade Reforged"
BASIC = "Generic Bound Plains"


class OwnedZoneExileBatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        selections = {
            "bound-effect-program-cards.json": {BASIC, "Generic Bound Body", "Generic Bound Commander"},
            "cascade-cards.json": {"Maelstrom Colossus"},
            "casting-payment-keywords-cards.json": {"Gurmag Angler"},
        }
        controls = []
        for filename, names in selections.items():
            fixture = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            fixture["cards"] = [row for row in fixture["cards"] if row["name"] in names]
            ids = {row["oracle_id"] for row in fixture["cards"]}
            fixture["rulings"] = [ruling for ruling in fixture["rulings"] if ruling["oracle_id"] in ids]
            self_contained = folder / filename
            self_contained.write_text(json.dumps(fixture), encoding="utf-8")
            controls.append(self_contained)
        database = folder / "owned-zone-exile.sqlite3"
        build_fixture_database([*controls, fixtures / "laelia-own-exile-occurrence-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owned zone exile", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(BASIC, 30),
        ], ["Generic Bound Commander"])

    def counter_items(self, game, source):
        return tuple(item for item in game.state.stack if item.source_object_id == source.object_id
                     and item.context.get("event") == OWNED_ZONE_EXILE_EVENT)

    def counters(self, game, source):
        return game.state.cards[source.object_id].counters.get("+1/+1", 0)

    def test_actual_attack_places_exile_permission_and_exact_counters_with_replay(self):
        game = self.session(60311044)
        source = self.add(game.engine, CARD)
        top = self.add(game.engine, BASIC, zone="library", ref="laelia-attack-top")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[top.object_id].zone)
        self.assertFalse(game.state.cards[top.object_id].face_down)
        permission = game.state.cards[top.object_id].annotations["temporary_play_permission"]
        self.assertEqual(("A", "until_end_of_turn"), (permission["player"], permission["duration"]))
        self.assertEqual(1, len(self.counter_items(game, source)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.counters(game, source))
        self.replay(game, load=True)

    def test_actual_delve_cost_exiles_six_cards_as_one_trigger_and_replays(self):
        game = self.session(60311045)
        source = self.add(game.engine, CARD)
        spell = self.add(game.engine, "Gurmag Angler", zone="hand")
        paid = [self.add(game.engine, BASIC, zone="graveyard", ref=f"delve-{index}") for index in range(6)]
        foreign = self.add(game.engine, BASIC, seat="B", zone="graveyard", ref="foreign-delve")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        option = self.action(game, "A", spell)
        response = {"action_id": option["id"], "pay": "auto", "delve_cards": [card.ref for card in paid]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**response, "delve_cards": [foreign.ref, *response["delve_cards"][1:]]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(self.counter_items(game, source)))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in paid))
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.counters(game, source))
        self.replay(game, load=True)

    def test_actual_cascade_seals_each_exile_separately_before_old_bottom_order(self):
        game = self.session(60311046)
        source = self.add(game.engine, CARD)
        cascade = self.add(game.engine, "Maelstrom Colossus", zone="hand")
        candidate = self.add(game.engine, "Generic Bound Body", zone="library", ref="cascade-stop")
        lands = [self.add(game.engine, BASIC, zone="library", ref=f"cascade-land-{index}") for index in range(2)]
        self.assert_closed(game, source)
        self.assert_closed(game, cascade)
        self.prepare(game, mana={"C": 8})
        self.checkpoint(game)
        self.cast_for(game, "A", cascade)
        pending = self.resolve(game)
        self.assertEqual("selection.exile_cast", pending.kind)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in (candidate, *lands)))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: not current.state.stack and self.counters(current, source) == 3)
        self.assertEqual({card.object_id for card in (candidate, *lands)}, set(game.state.players["A"].zones["library"][:3]))
        self.replay(game, load=True)

    def test_committed_mixed_owned_batch_filters_origins_and_freezes_one_trigger(self):
        game = self.session(60311047)
        source = self.add(game.engine, CARD)
        library = self.add(game.engine, BASIC, zone="library", ref="owned-library")
        grave = self.add(game.engine, BASIC, zone="graveyard", ref="owned-grave")
        hand = self.add(game.engine, BASIC, zone="hand", ref="excluded-hand")
        other = self.add(game.engine, BASIC, seat="B", zone="graveyard", ref="excluded-owner")
        self.assert_closed(game, source)
        self.prepare(game)
        owner = ZoneTransitionOwner(game.engine)
        moved = owner.move_cards_simultaneously([
            (library.object_id, "exile"), (grave.object_id, "exile"),
            (hand.object_id, "exile"), (other.object_id, "exile"),
        ], reason="Mixed owner-zone witness")
        self.assertTrue(all(card.zone == "exile" for card in moved))
        pending = (
            item for batch in game.state.pending_trigger_batches for group in batch.groups for item in group.items
            if item.source_object_id == source.object_id and item.normalized_event_id == OWNED_ZONE_EXILE_EVENT
        )
        exact, = pending
        self.assertFalse(self.counter_items(game, source))
        self.assertEqual("A", exact.controller)
        self.assertFalse(any(ref in str(exact.event_facts) for ref in (library.ref, grave.ref, hand.ref, other.ref)))
        self.checkpoint(game)
        self.until(game, lambda current: self.counters(current, source) >= 1)
        self.assertEqual(1, self.counters(game, source))
        self.replay(game, load=True)

    def test_trigger_authority_requires_owner_relation_and_simultaneous_group_scope(self):
        game = self.session(60311048)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = (item for item in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if item.event == OWNED_ZONE_EXILE_EVENT)
        self.assertIn(OWNED_ZONE_EXILE_CAPABILITY, program.capability_dependencies)
        for mutant in (
            replace(program, event_condition={"field": "owner", "op": "eq", "value": "A"}),
            replace(program, coverage=tuple(value for value in program.coverage if value != "one_or_more_event_batch")),
        ):
            binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry,
                                                   profile="commander_review")
            self.assertFalse(binding["strict"], binding)

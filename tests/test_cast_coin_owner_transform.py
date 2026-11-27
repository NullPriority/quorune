"""Native Ral cast coin and transformed-entry-loyalty rules."""

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
from quorune.entry_counter_model import INSTANT_SORCERY_LOYALTY_ENTRY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_coin_transform import CAPABILITY, OUTCOME_FIELD
from quorune.rules.random_selection import flip_fixed_coin
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"
FRONT = "Ral, Monsoon Mage"
BACK = "Ral, Leyline Prodigy"


class RalCoinAndEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
            "additional-conditional-modifier-cards.json": {"Divination"},
            "flashback-cards.json": {"Think Twice"},
            "activated-self-transform-cards.json": {"Unsummon"},
            "all-colors-cards.json": {"Clone"},
        }
        controls = []
        for filename, names in selected.items():
            fixture = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in names]
            assert {card["name"] for card in fixture["cards"]} == names
            card_ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [ruling for ruling in fixture["rulings"] if ruling["oracle_id"] in card_ids]
            path = folder / filename
            path.write_text(json.dumps(fixture), encoding="utf-8")
            controls.append(path)
        database = folder / "cast-coin-owner-transform.sqlite3"
        build_fixture_database([*controls, fixtures / "cast-optional-coin-transform-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source coin and transformed entry", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def add_ral(self, game, *, owner="A", controller="A"):
        if owner == controller:
            card = self.add(game.engine, RAL, seat=owner)
            card.active_face = FRONT
            return card
        card = self.add(game.engine, RAL, seat=owner, zone="hand")
        return game.engine.move_card(card.object_id, "battlefield", controller=controller, reason="Borrowed Ral setup")

    def trigger(self, game, source):
        return next((item for item in game.state.stack
                     if item.kind == "triggered_ability" and item.source_object_id == source.object_id
                     and item.context.get("event") == "spell.cast"), None)

    def flip_events(self, game):
        return [event for event in game.state.events if event.code == "coin.flip"]

    def game_with_flip(self, scene, *, won):
        for seed in range(61509800, 61509816):
            game, source, witness = scene(seed)
            trigger = self.trigger(game, source)
            self.assertIsNotNone(trigger)
            if flip_fixed_coin(seed=seed, instruction_key=f"{trigger.ref}|0", seat=trigger.controller) is won:
                return game, source, witness
        self.fail("No deterministic native seed selected the required coin outcome")

    def ordinary_scene(self, seed):
        game = self.session(seed)
        source = self.add_ral(game)
        draw = self.add(game.engine, "Divination", zone="hand")
        answer = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", draw)
        return game, source, answer

    def borrowed_scene(self, seed):
        game = self.session(seed)
        source = self.add_ral(game, controller="B")
        self.assertEqual(("A", "B"), (source.owner, source.controller))
        owned_instants = [self.add(game.engine, "Think Twice", zone="hand", ref=f"owner-instant-{index}")
                          for index in range(2)]
        borrowed_instant = self.add(game.engine, "Think Twice", seat="B", zone="hand", ref="borrower-instant")
        self.prepare(game, active="B", actor="A", mana={"C": 2, "U": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        for spell in owned_instants:
            self.cast_for(game, "A", spell)
            self.assertIsNone(self.trigger(game, source))
        self.cast_for(game, "B", borrowed_instant)
        return game, source, borrowed_instant

    def test_loss_deals_one_from_original_source_after_departure_and_replays(self):
        game, source, answer = self.game_with_flip(self.ordinary_scene, won=False)
        before_life = game.state.players["A"].life
        self.cast_for(game, "B", answer, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(before_life - 1, game.state.players["A"].life)
        result, = self.flip_events(game)
        self.assertFalse(result.details["won"])
        self.assertEqual("A", result.details["player"])
        self.replay(game, load=True)

    def test_winning_borrower_choice_returns_to_owner_and_counts_that_players_casts(self):
        game, source, _ = self.game_with_flip(self.borrowed_scene, won=True)
        old = game.state.cards[source.object_id].logical_object_id
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["pilot:B"], list(game.pending_principals()))
        self.assertTrue(self.trigger(game, source).context[OUTCOME_FIELD]["won"])
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        returned = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", "A", BACK), (returned.zone, returned.controller, returned.active_face))
        self.assertEqual(4, returned.counters["loyalty"])
        self.assertNotEqual(old, returned.logical_object_id)
        self.assertEqual(1, len(self.flip_events(game)))
        self.replay(game, load=True)

    def test_winning_player_can_decline_and_stale_source_offers_no_choice(self):
        game, source, _ = self.game_with_flip(self.ordinary_scene, won=True)
        incarnation = game.state.cards[source.object_id].logical_object_id
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        declined = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", FRONT, incarnation), (current.zone, current.active_face, current.logical_object_id))
        self.assertEqual(1, len(self.flip_events(game)))
        self.replay(game, load=True)

        game, source, answer = self.game_with_flip(self.ordinary_scene, won=True)
        self.cast_for(game, "B", answer, [source.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertTrue(self.flip_events(game)[0].details["won"])
        self.assertEqual(1, len(self.flip_events(game)))
        self.replay(game, load=True)

    def clone_scene(self, seed):
        game = self.session(seed)
        original = self.add_ral(game, owner="B", controller="B")
        clone = self.add(game.engine, "Clone", zone="hand")
        draw = self.add(game.engine, "Divination", zone="hand")
        self.prepare(game, mana={"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", clone)
        pending = self.resolve(game)
        self.assertEqual("replacement.order", pending.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option = next(row for row in options if original.ref in row["id"] and not row["id"].startswith("decline:"))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": option["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(FRONT, game.engine.display_name(clone.object_id))
        self.cast_for(game, "A", draw)
        return game, clone, original

    def test_winning_single_faced_copy_remains_exiled_after_the_original_offer(self):
        game, clone, original = self.game_with_flip(self.clone_scene, won=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(accepted.ok, accepted.summary)
        exiled = game.state.cards[clone.object_id]
        self.assertEqual(("exile", "Clone"), (exiled.zone, exiled.printed_name))
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertEqual(1, len(self.flip_events(game)))
        self.replay(game, load=True)

    def test_exact_envelope_rejects_changed_controller_turn_and_scaled_entry(self):
        game = self.session(61509900)
        source = self.add_ral(game)
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        coin, = (program for program in programs if CAPABILITY in program.capability_dependencies)
        entry, = (program for program in programs if INSTANT_SORCERY_LOYALTY_ENTRY_CAPABILITY in program.capability_dependencies)
        for program in (coin, entry):
            self.assertEqual("trusted", program.trust_level)
            binding = bind_semantic_program_runtime(program, capability_registry=self.registry, profile="commander_review")
            self.assertTrue(binding["strict"], binding)
        mutant = replace(coin, event_condition={"field": "controller", "op": "eq", "value": "$source.controller"})
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"], binding)
        descriptor, = entry.handlers
        scaled = {**descriptor, "amount_spec": {**descriptor["amount_spec"], "coefficient": 2}}
        with self.assertRaisesRegex(SemanticNodeError, "unscaled entering-controller fact"):
            replace(entry, handlers=(scaled,))

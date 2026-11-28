"""Native return of the graveyard arrivals from one qualified death."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.rules.death_battlefield_return import (
    DEATH_BATTLEFIELD_ARRIVALS, DEATH_BATTLEFIELD_RETURN_CAPABILITY, DeathBattlefieldArrival,
    controlled_nonflying_death_condition, death_battlefield_return_effect,
    death_battlefield_return_runtime_dependencies, validate_death_battlefield_return,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class FlyingCounterDeathReturnTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Gleamwing Caretaker"
    profile = "commander_review"

    @classmethod
    def use_database(cls, path, commander, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Flying counter return", [DeckEntry(commander, 1, "commander"), DeckEntry(land, 30)], [commander],
        )

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "flying-counter-death-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "counter-qualified-zone-cards.json", "manifest-cards.json",
            "any-number-target-cards.json", "mutate-casting-cards.json", "additional-token-definition-cards.json",
            "activated-self-transform-cards.json", "announced-x-activation-cards.json",
            "flying-counter-death-return-cards.json",
        )], path)
        cls.use_database(path, "Generic Bound Commander", "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def add_source(self, game):
        source = self.add(game.engine, self.source_name)
        self.assert_closed(game, source)
        return source

    def pending_return(self, game, *arrivals):
        item, = [item for item in game.state.stack if DEATH_BATTLEFIELD_ARRIVALS in item.context]
        self.assertEqual("A", item.controller)
        self.assertEqual(
            {card.object_id for card in arrivals},
            {row["object_id"] for row in item.context[DEATH_BATTLEFIELD_ARRIVALS]},
        )
        return item

    def choose(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        prior = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(prior, authoritative_state_hash(game.state))

    def test_last_controller_can_trigger_return_under_a_different_owner(self):
        game = self.session(60310091)
        self.add_source(game)
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        gain = self.add(game.engine, "Act of Treason", zone="hand")
        murder = self.add(game.engine, "Murder", zone="hand", ref="controlled-death")
        self.assert_closed(game, gain)
        self.prepare(game, mana={"B": 2, "R": 1, "C": 3})
        self.checkpoint(game)
        self.cast(game, "A", gain, [bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[bear.object_id].controller)
        self.cast(game, "A", murder, [bear.ref])
        self.resolve_top(game)
        self.pending_return(game, bear)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[bear.object_id]
        self.assertEqual(("battlefield", "B", {"flying": 1}), (returned.zone, returned.controller, returned.counters))
        self.assertNotIn(bear.object_id, game.state.players["A"].zones["battlefield"])
        self.replay(game, load=True)

    def test_simultaneous_source_death_retains_its_trigger_and_excludes_flying_and_opponents(self):
        game = self.session(60310092)
        source = self.add_source(game)
        bear = self.add(game.engine, "Grizzly Bears", ref="grounded")
        flying = self.add(game.engine, "Wind Drake")
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-bear")
        wrath = self.add(game.engine, "Day of Judgment", zone="hand", ref="simultaneous-deaths")
        self.prepare(game, mana={"W": 2, "C": 2})
        self.checkpoint(game)
        self.cast(game, "A", wrath)
        self.resolve_top(game)
        item = self.pending_return(game, bear)
        self.assertEqual(source.object_id, item.source_object_id)
        for card in (source, flying, opponent):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[bear.object_id].counters["flying"])
        self.replay(game, load=True)

        repeat = self.session(60310093)
        self.add_source(repeat)
        bear = self.add(repeat.engine, "Grizzly Bears", ref="repeat-flier")
        for name in ("first-kill", "second-kill"):
            self.add(repeat.engine, "Murder", zone="hand", ref=name)
        self.prepare(repeat, mana={"B": 4, "C": 2})
        self.checkpoint(repeat)
        first, second = (next(card for card in repeat.state.cards.values() if card.ref == ref)
                         for ref in ("first-kill", "second-kill"))
        self.cast(repeat, "A", first, [bear.ref])
        self.assertIsNone(self.resolve(repeat))
        self.assertEqual(1, repeat.state.cards[bear.object_id].counters["flying"])
        self.cast(repeat, "A", second, [bear.ref])
        self.assertIsNone(self.resolve(repeat))
        self.assertEqual("graveyard", repeat.state.cards[bear.object_id].zone)
        self.replay(repeat, load=True)

    def test_token_death_offers_one_trigger_with_no_returnable_card(self):
        game = self.session(60310094)
        self.add_source(game)
        generate = self.add(game.engine, "Raise the Alarm", zone="hand")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.prepare(game, mana={"C": 2, "W": 1, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", generate)
        self.assertIsNone(self.resolve(game))
        tokens = sorted((card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"),
                        key=lambda card: card.object_id)
        self.assertEqual(2, len(tokens))
        self.cast(game, "A", kill, [tokens[0].ref])
        self.resolve_top(game)
        self.pending_return(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([tokens[1].object_id], [card.object_id for card in game.state.cards.values()
                                                if card.is_token and card.zone == "battlefield"])
        self.replay(game, load=True)

    def test_merged_death_returns_each_physical_card_unless_that_card_has_left(self):
        for remove_part in (False, True):
            with self.subTest(remove_part=remove_part):
                game = self.session(60310095 + remove_part)
                self.add_source(game)
                root = self.add(game.engine, "Grizzly Bears", ref="merge-root")
                part = self.add(game.engine, "Gemrazer", zone="hand", ref="mutating-card")
                kill = self.add(game.engine, "Murder", zone="hand", ref="merged-death")
                exile = self.add(game.engine, "Cremate", seat="B", zone="hand", ref="between-death-and-return")
                self.assert_closed(game, part)
                self.prepare(game, mana={"G": 2, "C": 2, "B": 2})
                game.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(game)
                action = self.action(game, "A", part)
                cost, = [row["id"] for row in action["cost_options"] if row["id"].startswith("mutate")]
                self.choose(game, "A", {
                    "action_id": action["id"], "cost_option": cost, "targets": [root.ref], "pay": "auto",
                })
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.choose(game, "A", {"action_id": "choose", "choice": "bottom"})
                self.assertIsNone(self.resolve(game))
                self.assertIsNotNone(game.state.cards[root.object_id].permanent_composition)
                self.cast(game, "A", kill, [root.ref])
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                order = {"action_id": "choose", "cards": [part.ref, root.ref]}
                self.reject(game, "B", order)
                self.choose(game, "A", order)
                self.pending_return(game, root, part)
                game = self.replay(game, load=True)
                if remove_part:
                    self.cast(game, "B", exile, [part.ref])
                    self.resolve_top(game)
                    self.pending_return(game, root, part)
                self.assertIsNone(self.resolve(game))
                returned = game.state.cards[root.object_id]
                later = game.state.cards[part.object_id]
                self.assertEqual(("battlefield", {"flying": 1}), (returned.zone, returned.counters))
                self.assertIsNone(returned.permanent_composition)
                self.assertEqual(("exile", {}) if remove_part else ("battlefield", {"flying": 1}),
                                 (later.zone, later.counters))
                self.replay(game, load=True)

    def manifested_departure(self, card_name, seed, *, anchor=False):
        game = self.session(seed)
        self.add_source(game)
        spell = self.add(game.engine, "Soul Summons", zone="hand")
        card = self.add(game.engine, card_name, zone="library", ref="manifested-death")
        kill = self.add(game.engine, "Murder", zone="hand")
        if anchor:
            self.add(game.engine, "Grizzly Bears", seat="B", ref="aura-recipient")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"W": 1, "C": 2, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[card.object_id].face_down)
        for seat in ("B", "C", "D"):
            self.assertNotIn(card_name, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast(game, "A", kill, [card.ref])
        self.resolve_top(game)
        self.pending_return(game, card)
        return game, card

    def test_manifested_aura_uses_the_owners_nonspell_attachment_choice(self):
        game, aura = self.manifested_departure("Pacifism", 60310097, anchor=True)
        decision = self.resolve(game)
        self.assertEqual(("aura.entry", ["A"]), (decision.kind, decision.actors))
        command = {"action_id": "choose", "aura_target": "aura-recipient"}
        self.reject(game, "B", command)
        game = self.replay(game, load=True)
        self.choose(game, "A", command)
        self.assertIsNone(self.resolve(game))
        restored = game.state.cards[aura.object_id]
        receiver = next(card for card in game.state.cards.values() if card.ref == "aura-recipient")
        self.assertEqual(("battlefield", False, 1, receiver.object_id),
                         (restored.zone, restored.face_down, restored.counters["flying"], restored.attached_to))
        self.replay(game, load=True)

    def test_manifested_instant_is_ineligible_to_enter_and_stays_in_graveyard(self):
        game, card = self.manifested_departure("Lightning Bolt", 60310098)
        before = game.state.cards[card.object_id].logical_object_id
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", before, {}), (
            game.state.cards[card.object_id].zone, game.state.cards[card.object_id].logical_object_id,
            game.state.cards[card.object_id].counters,
        ))
        self.replay(game, load=True)

    def test_withdrawal_and_different_printed_scopes_fail_closed(self):
        record = self.db.lookup(self.source_name, fuzzy=False)
        supported = compile_oracle_card(record, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", supported.status, supported.material_residuals)
        for before, after in (("without flying", "without reach"), ("you control", "an opponent controls"),
                              ("a flying counter", "a reach counter")):
            nearby = replace(record, oracle_text=record.oracle_text.replace(before, after))
            result = compile_oracle_card(nearby, capability_registry=self.registry, capability_profile=self.profile)
            self.assertNotEqual("exact", result.status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == DEATH_BATTLEFIELD_RETURN_CAPABILITY).update(
            status="blocked", blockers=["sealed creature death return authority withdrawn"],
        )
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified(self.registry.evidence_fingerprint)
        result = compile_oracle_card(record, capability_registry=withdrawn, capability_profile=self.profile)
        self.assertNotEqual("exact", result.status)
        self.assertTrue(any(DEATH_BATTLEFIELD_RETURN_CAPABILITY in blocker
                            for residual in result.material_residuals for blocker in residual.blockers))
        descriptor = death_battlefield_return_effect()
        resolved = {**descriptor, "source": "stack:return", DEATH_BATTLEFIELD_ARRIVALS: []}
        self.assertEqual((), validate_death_battlefield_return(resolved))
        one = DeathBattlefieldArrival("card:one", "one", 1, "A").to_dict()
        with self.assertRaisesRegex(ValueError, "repeat"):
            validate_death_battlefield_return({**resolved, DEATH_BATTLEFIELD_ARRIVALS: [one, one]})
        wrong_event = SimpleNamespace(active_zone="battlefield", event="permanent.enter", target_schema=None,
                                      handlers=(), effects=(descriptor,), event_condition=controlled_nonflying_death_condition())
        with self.assertRaisesRegex(ValueError, "controlled nonflying creature death"):
            death_battlefield_return_runtime_dependencies(wrong_event)

    def test_omitting_incarnation_revalidation_is_detected(self):
        arrival = DeathBattlefieldArrival("card:one", "one", 2, "A")
        new_card = SimpleNamespace(ref="one", zone="graveyard", zone_change_counter=4,
                                   owner="A", is_card_object=True, is_token=False)

        def witness():
            self.assertFalse(arrival.matches(new_card), "Earlier death must never return a later incarnation")

        witness()
        with patch.object(DeathBattlefieldArrival, "matches", return_value=True):
            with self.assertRaisesRegex(AssertionError, "Earlier death"):
                witness()

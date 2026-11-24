"""Native mandatory counter payment evidence for original triggered abilities."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Clockwork Hydra", "Noosegraf Mob")


class MandatorySourceCounterFollowupTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "mandatory-source-counter-followup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/mandatory-source-counter-followup-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mandatory source counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(card.oracle_id)

    def test_complete_originals_keep_entry_and_tap_growth_beside_the_required_payment(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                programs = self.programs(name)
                self.assertIn("zone.change", {program.event for program in programs})
                expected = "creature.attacks" if name == "Clockwork Hydra" else "spell.cast"
                trigger, = (program for program in programs if program.event == expected)
                self.assertEqual(name == "Clockwork Hydra", bool(trigger.target_schema))
                self.assertIn("cost.counter.source_fixed_resolution", trigger.capability_dependencies)
                self.assertEqual(3 if name == "Clockwork Hydra" else 2, len(programs))

    def test_raw_required_payment_needs_the_original_cost_target_and_trigger(self):
        trigger, = [program for program in self.programs("Clockwork Hydra")
                    if program.event == "creature.attacks"]
        wrapper, = trigger.effects
        for altered in (
            replace(trigger, event_condition=None),
            replace(trigger, active_zone="graveyard"),
            replace(trigger, target_schema=None),
            replace(trigger, effects=({**wrapper, "schema_version": 2},)),
            replace(trigger, effects=({**wrapper, "payment": {**wrapper["payment"], "source_ref": "$source"}},)),
            replace(trigger, effects=({**wrapper, "payment": {**wrapper["payment"], "amount": True}},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_hydra_commits_one_counter_only_for_a_still_legal_target(self):
        for response in ("none", "target_leaves", "source_reenters"):
            with self.subTest(response=response):
                game = self.session(11812001 + len(response))
                hydra = self.add(game.engine, "Clockwork Hydra", zone="hand")
                fervor = self.add(game.engine, "Fervor")
                bear = self.add(game.engine, "Grizzly Bears", seat="B")
                bounce = None
                blink = None
                if response == "target_leaves":
                    bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B")
                elif response == "source_reenters":
                    blink = self.add(game.engine, "Cloudshift", zone="hand")
                    self.add(game.engine, "Plains")
                for card in (hydra, fervor, bear, bounce, blink):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 5})
                self.checkpoint(game)
                self.cast_for(game, "A", hydra)
                self.resolve_top(game)
                self.assertEqual(4, game.state.cards[hydra.object_id].counters["+1/+1"])
                self.attack(game, [(hydra, "B")])
                self.target(game, "A", [bear.ref] if bounce is not None else ["C"])
                game = self.replay(game, load=True)
                if bounce is not None:
                    self.cast_for(game, "B", bounce, [bear.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[bear.object_id].zone)
                elif blink is not None:
                    old = game.state.cards[hydra.object_id].logical_object_id
                    self.cast_for(game, "A", blink, [hydra.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(old, game.state.cards[hydra.object_id].logical_object_id)
                self.resolve_top(game)
                self.assertEqual(3 if response == "none" else 4,
                                 game.state.cards[hydra.object_id].counters["+1/+1"])
                self.assertEqual(39 if response == "none" else 40, game.state.players["C"].life)
                self.replay(game, load=True)

    def test_actual_block_cost_and_tap_growth_use_the_current_hydra(self):
        game = self.session(11812031)
        hydra = self.add(game.engine, "Clockwork Hydra", seat="B")
        hydra.counters["+1/+1"] = 4
        bear = self.add(game.engine, "Grizzly Bears")
        fervor = self.add(game.engine, "Fervor", seat="B")
        for card in (hydra, bear, fervor):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(bear, "B")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {hydra.ref: bear.ref}})
        self.assertTrue(result.ok, result.summary)
        self.target(game, "B", ["C"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(3, game.state.cards[hydra.object_id].counters["+1/+1"])
        self.assertEqual(39, game.state.players["C"].life)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith(f"activate:{hydra.ref}:")]
        applied = game.act("pilot:B", {"action_id": action, "pay": "auto"})
        self.assertTrue(applied.ok, applied.summary)
        self.resolve_top(game)
        self.assertTrue(game.state.cards[hydra.object_id].tapped)
        self.assertEqual(4, game.state.cards[hydra.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_mob_pays_its_last_counter_and_creates_the_token_before_the_cast_spell(self):
        game = self.session(11812041)
        mob = self.add(game.engine, "Noosegraf Mob")
        mob.counters["+1/+1"] = 1
        bolt = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        for card in (mob, bolt):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "B", bolt, ["C"])
        spell, trigger = game.state.stack
        self.assertEqual(bolt.object_id, spell.card_object_id)
        self.assertEqual(mob.object_id, trigger.source_object_id)
        game = self.replay(game, load=True)
        before = game.state.event_sequence
        self.resolve_top(game)
        tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        token, = tokens
        self.assertEqual("A", token.controller)
        data = game.engine._effective_card_data(token)
        self.assertEqual(["B"], list(data["colors"]))
        self.assertIn("zombie", data["type_line"].casefold())
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(token.object_id, stat) for stat in ("power", "toughness")))
        self.assertEqual("graveyard", game.state.cards[mob.object_id].zone)
        self.assertEqual(40, game.state.players["C"].life)
        self.assertEqual([bolt.object_id], [item.card_object_id for item in game.state.stack])
        events = [event for event in game.state.events if event.event_id > before]
        removed = next(event.event_id for event in events if event.code == "permanent.counter"
                       and event.details.get("delta") == -1 and mob.object_id in event.changed_objects)
        created = next(event.event_id for event in events if event.code == "token.create"
                       and token.object_id in event.changed_objects)
        death = next(event.event_id for event in events if event.code == "state.creatures_died"
                     and mob.object_id in event.changed_objects)
        self.assertLess(removed, created)
        self.assertLess(created, death)
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["C"].life)
        self.replay(game, load=True)

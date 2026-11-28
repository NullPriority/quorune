"""Actual scheduled upkeep sacrifice using its original source's soot counters."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.upkeep_source_counter_sacrifice import (
    UPKEEP_SOURCE_COUNTER_SACRIFICE_CAPABILITY, upkeep_source_counter_sacrifice_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class UpkeepSourceCounterSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "counter-upkeep.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/upkeep-source-counter-sacrifice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source counter upkeep", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, source, *, active):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.prepare(game, actor=active, active=active)
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def upkeep(self, game, seat):
        self.until(game, lambda state: state.state.active_player == seat and state.state.step == "upkeep"
                   and bool(state.state.stack or state.state.pending_trigger_batches))

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def pending_sacrifice(self, game, seat, *, count, options):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        view = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(count, view["count"])
        self.assertEqual({card.ref for card in options}, set(view["options"]))

    def add_soot(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.act(game, "A", {"action_id": "choose", "choice": "put"})

    def test_actual_own_upkeep_order_changes_the_live_count_before_a_public_sacrifice(self):
        for add_first in (True, False):
            with self.subTest(add_first=add_first):
                game = self.session(708011100 + add_first)
                source = self.add(game.engine, "Smokestack")
                source.counters.update(soot=1, charge=5)
                bear = self.add(game.engine, "Grizzly Bears")
                land = self.add(game.engine, "Plains", ref="upkeep-counter-own-land")
                game.state.last_normal_turn_player = "D"
                self.ready(game, source, active="D")
                self.upkeep(game, "A")
                self.assertEqual("trigger.order", game.state.pending_decision.kind)
                triggers = [item for batch in game.state.pending_trigger_batches for item in batch.items]
                sacrifice, = (item for item in triggers if "sacrifices a permanent" in item.label)
                addition, = (item for item in triggers if "you may put a soot counter" in item.label)
                order = [sacrifice.ref, addition.ref] if add_first else [addition.ref, sacrifice.ref]
                self.act(game, "A", {"action_id": "order", "triggers": order})
                if add_first:
                    self.add_soot(game)
                    self.assertEqual(2, game.state.cards[source.object_id].counters["soot"])
                options = [source, bear, land]
                self.pending_sacrifice(game, "A", count=2 if add_first else 1, options=options)
                selected = [source, bear] if add_first else [bear]
                self.act(game, "A", {"action_id": "choose", "cards": [card.ref for card in selected]})
                if not add_first:
                    self.add_soot(game)
                    self.assertEqual(2, game.state.cards[source.object_id].counters["soot"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard" if add_first else "battlefield", self.zone(game, source))
                self.assertEqual(("graveyard", "battlefield"), (self.zone(game, bear), self.zone(game, land)))
                self.replay(game, load=True)

    def test_opponent_upkeep_uses_original_destroyed_or_phased_source_and_keeps_their_choice(self):
        for phase in (False, True):
            with self.subTest(phase=phase):
                game = self.session(708011110 + phase)
                source = self.add(game.engine, "Smokestack")
                source.counters.update(soot=2, charge=5)
                spell = (self.add(game.engine, "Reality Ripple", seat="B", zone="hand") if phase else
                         self.add(game.engine, "Disenchant", seat="B", zone="hand"))
                bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="upkeep-counter-b-body")
                plains = self.add(game.engine, "Plains", seat="B", ref="upkeep-counter-b-plains")
                island = self.add(game.engine, "Island", seat="B", ref="upkeep-counter-b-island")
                observer = self.add(game.engine, "Grizzly Bears", seat="D", ref="upkeep-counter-unchosen")
                hidden = self.add(game.engine, "Island", seat="C", zone="hand", ref="upkeep-counter-private")
                self.ready(game, source, active="A")
                self.upkeep(game, "B")
                self.assertEqual(1, len(game.state.stack))
                self.cast(game, "B", spell, [source.ref])
                self.resolve_top(game)
                original = game.state.cards[source.object_id]
                self.assertEqual("battlefield" if phase else "graveyard", original.zone)
                self.assertEqual(phase, original.phased_out)
                self.pending_sacrifice(game, "B", count=2, options=[bear, plains, island])
                for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
                    self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
                self.reject(game, "A", {"action_id": "choose", "cards": [bear.ref, plains.ref]})
                self.reject(game, "B", {"action_id": "choose", "cards": [bear.ref, observer.ref]})
                game = self.replay(game, load=True)
                self.act(game, "B", {"action_id": "choose", "cards": [bear.ref, plains.ref]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(("graveyard", "graveyard", "battlefield", "battlefield"),
                                 tuple(self.zone(game, card) for card in (bear, plains, island, observer)))
                self.replay(game, load=True)

    def test_zero_skips_the_choice_and_a_short_player_sacrifices_every_available_permanent(self):
        for count in (0, 3):
            with self.subTest(count=count):
                game = self.session(708011120 + count)
                source = self.add(game.engine, "Smokestack")
                source.counters.update(charge=6)
                if count:
                    source.counters["soot"] = count
                land = self.add(game.engine, "Plains", seat="B", ref="upkeep-counter-only-land")
                self.ready(game, source, active="A")
                self.upkeep(game, "B")
                if count:
                    self.pending_sacrifice(game, "B", count=1, options=[land])
                    self.act(game, "B", {"action_id": "choose", "cards": [land.ref]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard" if count else "battlefield", self.zone(game, land))
                self.replay(game, load=True)

    def test_exact_original_keeps_both_triggers_and_fails_closed_without_the_scheduled_choice(self):
        card = self.db.lookup("Smokestack", fuzzy=False)
        for profile in ("traditional", "commander_duel", PROFILE):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            addition, sacrifice = ir.faces[0].nodes
            self.assertEqual(tuple(card.oracle_text.splitlines()),
                             tuple(card.oracle_text[n.span.start:n.span.end] for n in (addition, sacrifice)))
            self.assertTrue(any(effect["op"] == "offer_optional_counter_placement" for effect in addition.effects))
            self.assertEqual((upkeep_source_counter_sacrifice_effect(),), tuple(sacrifice.effects))
            self.assertIsNone(sacrifice.target_schema)
        report, semantics = register_card(self.db, self.registry, "Smokestack")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        cap = UPKEEP_SOURCE_COUNTER_SACRIFICE_CAPABILITY
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies)
        deps = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        choice, = program.effects
        malformed = replace(program, effects=({**choice, "count": {**choice["count"], "coefficient": True}},))
        invalid = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(invalid["strict"])
        for before, after in (("each player's upkeep", "each opponent's upkeep"), ("may put", "may gain a life and put"),
                              ("permanent of their choice", "creature of their choice"),
                              ("each soot counter", "each charge counter"), ("this artifact.", "this artifact. Rewrite history.")):
            altered = replace(card, oracle_text=card.oracle_text.replace(before, after, 1))
            self.assertNotEqual("exact", compile_oracle_card(altered, capability_registry=self.registry).status)

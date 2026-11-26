"""Native activation, delayed recognition, and replay for Together Forever."""

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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CAPABILITY = "trigger.delayed.target_death_return_to_hand"
PROFILE = "commander_review"


class TogetherForeverNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "together-forever.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "together-forever-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-public-state-cards.json",
            fixtures / "bound-static-declaration-cards.json",
            fixtures / "same-turn-graveyard-return-sets-cards.json",
            fixtures / "delayed-object-action-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Together Forever rules fixture",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def start(self, game, *cards, mana, other_mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        for seat, pool in (other_mana or {}).items():
            game.state.players[seat].mana_pool.update(pool)
        self.checkpoint(game)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activation_offer(self, game, seat, source):
        self.priority(game, seat)
        prefix = "activate:" + self.current(game, source).ref + ":"
        legal = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]
        offer, = (action for action in legal["actions"] if action["id"].startswith(prefix))
        return offer

    def watched_scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Together Forever")
        watched = self.add(game.engine, "Grizzly Bears", seat="B", ref="watched-creature")
        watched.counters["charge"] = 1
        return game, source, watched

    def arm(self, game, source, watched):
        offer = self.activation_offer(game, "A", source)
        self.accept(game, "A", {"action_id": offer["id"], "targets": [watched.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, sum(trigger.active for trigger in game.state.delayed_triggers))

    def test_complete_original_binds_support_and_the_counter_targeted_activation(self):
        admitted, semantics = register_card(self.db, self.registry, "Together Forever")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        self.assertEqual("capability_closed", admitted["trust_basis"])
        card = self.db.lookup("Together Forever", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        activation, = (program for program in programs if CAPABILITY in program.capability_dependencies)
        support, = (program for program in programs if program is not activation)
        support_effect, = support.effects
        self.assertEqual(("place_counters_on_targets", "+1/+1", 1, 2), (
            support_effect["op"], support_effect["counter"], support_effect["amount"], support_effect["maximum_targets"],
        ))
        deps = tuple(cap for cap in activation.capability_dependencies if cap != CAPABILITY)
        missing_capability = replace(
            activation, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict(),
        )
        binding = bind_semantic_program_runtime(missing_capability, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])

        changed = replace(card, oracle_text=card.oracle_text.replace("this turn", "this game"))
        compiler = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", compiler.status)

    def test_actual_support_offers_exact_targets_and_the_watch_returns_to_the_owner(self):
        game = self.session(60377001)
        source = self.add(game.engine, "Together Forever", zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="support-own-creature")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="support-opponent-creature")
        watched = self.add(game.engine, "Grizzly Bears", seat="B", ref="watched-creature")
        watched.counters["charge"] = 1
        counterless = self.add(game.engine, "Grizzly Bears", ref="counterless-creature")
        land = self.add(game.engine, "Plains", ref="countered-land")
        land.counters["charge"] = 1
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="unrelated-private-card")
        remover = self.add(game.engine, "Vampire Hexmage", seat="B")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        disenchant = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        murder = self.add(game.engine, "Murder", seat="C", zone="hand")
        self.start(game, source, remover, theft, disenchant, murder, mana={"W": 2, "C": 2, "R": 1},
                   other_mana={"B": {"C": 1, "W": 1}, "C": {"C": 1, "B": 2}})

        self.cast_for(game, "A", source, reserve={"C": 2, "R": 1})
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.reject(game, "B", {"action_id": "choose", "targets": [own.ref, other.ref]})
        self.reject(game, "A", {"action_id": "choose", "targets": [own.ref, own.ref]})
        self.accept(game, "A", {"action_id": "choose", "targets": [own.ref, other.ref]})
        self.assertIsNone(self.resolve(game))
        for card in (own, other):
            self.assertEqual({"+1/+1": 1}, self.current(game, card).counters)
        self.assertEqual({"charge": 1}, self.current(game, watched).counters)

        offer = self.activation_offer(game, "A", source)
        self.assertEqual({own.ref, other.ref, watched.ref}, set(offer["target_schema"]["legal_refs"]))
        command = {"action_id": offer["id"], "targets": [watched.ref], "pay": "auto", "reserve": {"C": 1, "R": 1}}
        for target in (counterless, land):
            self.reject(game, "A", {**command, "targets": [target.ref]})
        self.reject(game, "A", {**command, "targets": []})
        self.reject(game, "B", command)
        self.accept(game, "A", command)
        remaining_mana = {key: amount for key, amount in game.state.players["A"].mana_pool.items() if amount}
        self.assertEqual({"C": 1, "R": 1}, remaining_mana)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, sum(trigger.active for trigger in game.state.delayed_triggers))
        game = self.replay(game, load=True)

        offer = self.activation_offer(game, "B", remover)
        self.accept(game, "B", {"action_id": offer["id"], "targets": [watched.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.current(game, watched).counters)

        self.cast(game, "A", theft, [watched.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "B"), (self.current(game, watched).controller, self.current(game, watched).owner))
        self.cast(game, "B", disenchant, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, source).zone)

        self.cast(game, "C", murder, [watched.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, watched).zone)
        delayed, = game.state.stack
        self.assertEqual(("triggered_ability", "A"), (delayed.kind, delayed.controller))
        self.assertFalse(delayed.targets)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertIn(watched.object_id, game.state.players["B"].zones["hand"])
        self.assertNotIn(watched.object_id, game.state.players["A"].zones["hand"])
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_removal_or_death_before_activation_resolves_never_arms_the_watch(self):
        for response in ("counter removal", "death"):
            with self.subTest(response=response):
                game, source, watched = self.watched_scene(60377010 + int(response == "death"))
                remover = self.add(game.engine, "Vampire Hexmage", seat="B")
                murder = self.add(game.engine, "Murder", seat="C", zone="hand")
                self.start(game, source, remover, murder, mana={"C": 1}, other_mana={"C": {"C": 1, "B": 2}})
                offer = self.activation_offer(game, "A", source)
                self.accept(game, "A", {"action_id": offer["id"], "targets": [watched.ref], "pay": "auto"})

                if response == "counter removal":
                    offer = self.activation_offer(game, "B", remover)
                    self.accept(game, "B", {"action_id": offer["id"], "targets": [watched.ref], "pay": "auto"})
                else:
                    self.cast(game, "C", murder, [watched.ref])
                self.resolve_top(game)
                self.assertFalse(self.current(game, watched).counters)
                activation, = game.state.stack
                self.assertEqual("activated_ability", activation.kind)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.delayed_triggers)

                if response == "counter removal":
                    self.assertEqual("battlefield", self.current(game, watched).zone)
                    self.cast(game, "C", murder, [watched.ref])
                    self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", self.current(game, watched).zone)
                self.assertFalse(game.state.stack)
                self.replay(game, load=True)

    def test_actual_same_turn_cleanup_death_still_returns_the_card(self):
        game, source, watched = self.watched_scene(60377030)
        watched.counters["-1/-1"] = 1
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        transmutation = self.add(game.engine, "Kasmina's Transmutation", zone="hand")
        self.start(game, source, growth, transmutation, mana={"C": 2, "G": 1, "U": 1})
        self.cast_for(game, "A", growth, [watched.ref], reserve={"C": 2, "U": 1})
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", transmutation, [watched.ref], reserve={"C": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(watched.object_id, "toughness"))
        self.arm(game, source, watched)

        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.step == "cleanup"
                   and self.current(current, watched).zone == "graveyard")
        self.assertEqual(turn, game.state.turn_sequence)
        delayed, = game.state.stack
        self.assertEqual(("triggered_ability", "A"), (delayed.kind, delayed.controller))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertIn(watched.object_id, game.state.players["B"].zones["hand"])
        self.replay(game, load=True)


    def test_actual_watch_expires_and_rejects_stale_battlefield_or_graveyard_incarnations(self):
        for index, boundary in enumerate(("next turn", "battlefield", "graveyard")):
            with self.subTest(boundary=boundary):
                game, source, watched = self.watched_scene(60377020 + index)
                murder = self.add(game.engine, "Murder", seat="C", zone="hand")
                for land_index in range(3):
                    self.add(game.engine, "Swamp", seat="C", ref=f"death-spell-mana-{land_index}")
                if boundary == "battlefield":
                    blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
                    self.start(game, source, murder, blink, mana={"C": 1}, other_mana={"B": {"W": 1}})
                elif boundary == "graveyard":
                    self.add(game.engine, "Vedalken Orrery", seat="B")
                    regrowth = self.add(game.engine, "Regrowth", seat="B", zone="hand")
                    discard = self.add(game.engine, "Thrill of Possibility", seat="B", zone="hand")
                    self.start(game, source, murder, regrowth, discard, mana={"C": 1},
                               other_mana={"B": {"C": 2, "G": 1, "R": 1}})
                else:
                    self.start(game, source, murder, mana={"C": 1})
                self.arm(game, source, watched)

                if boundary == "next turn":
                    original_turn = game.state.turn_sequence
                    self.until(game, lambda current: current.state.turn_sequence > original_turn)
                    self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
                elif boundary == "battlefield":
                    original = self.current(game, watched).logical_object_id
                    self.cast(game, "B", blink, [watched.ref])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("battlefield", self.current(game, watched).zone)
                    self.assertNotEqual(original, self.current(game, watched).logical_object_id)

                self.cast(game, "C", murder, [watched.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", self.current(game, watched).zone)
                if boundary == "graveyard":
                    self.assertEqual(1, len(game.state.stack))
                    arrival = self.current(game, watched).logical_object_id
                    self.cast_for(game, "B", regrowth, [watched.ref], reserve={"C": 1, "R": 1})
                    self.resolve_top(game)
                    self.assertEqual("hand", self.current(game, watched).zone)
                    self.priority(game, "B")
                    offer = self.action(game, "B", discard)
                    self.accept(game, "B", {
                        "action_id": offer["id"], "discard_cards": [watched.ref], "pay": "auto",
                    })
                    self.resolve_top(game)
                    self.assertEqual("graveyard", self.current(game, watched).zone)
                    self.assertNotEqual(arrival, self.current(game, watched).logical_object_id)
                    game = self.replay(game, load=True)
                    self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.stack)
                self.assertEqual("graveyard", self.current(game, watched).zone)
                self.replay(game, load=True)

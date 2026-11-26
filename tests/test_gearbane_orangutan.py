from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_effect_payment import MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Gearbane Orangutan"
PROFILE = "commander_review"


class GearbaneOrangutanTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "gearbane-orangutan.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/gearbane-orangutan-cards.json"], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Optional target and mandatory artifact payment", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start_trigger(self, seed, *, controlled=True, response=None, replacement=False):
        game = self.session(seed)
        host = game.engine
        source = self.add(host, SOURCE, zone="hand", ref="gearbane")
        mine = self.add(host, "Sol Ring", ref="gearbane-own-artifact") if controlled else None
        other = self.add(host, "Sol Ring", seat="B", ref="gearbane-other-artifact")
        bear = self.add(host, "Grizzly Bears", seat="B", ref="gearbane-other-creature")
        reply = self.add(host, response, seat="C", zone="hand", ref="gearbane-reply") if response else None
        if replacement:
            self.add(host, "Rest in Peace", seat="D", ref="gearbane-destination-replacement")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "R": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        decision = self.resolve(game)
        self.assertEqual(("semantic.target", ("A",)), (decision.kind, tuple(decision.actors)))
        return game, source, mine, other, bear, reply

    def rejects(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        count = len(game.commands)
        reply = game.act(f"pilot:{seat}", command)
        self.assertFalse(reply.ok, reply.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(count, len(game.commands))

    def choose_mode(self, game, mode, targets=()):
        command = {"action_id": "choose", "modes": [mode], "targets": list(targets)}
        self.rejects(game, "D", command)
        reply = game.act("pilot:A", command)
        self.assertTrue(reply.ok, reply.summary)
        self.assertEqual([mode], list(game.state.stack[-1].modes))

    def test_destroy_mode_accepts_zero_or_one_exact_artifact_and_revalidates(self):
        for index, behavior in enumerate(("zero", "destroy", "stale")):
            with self.subTest(behavior=behavior):
                game, source, mine, other, bear, reply = self.start_trigger(
                    7302810 + index, response="Disenchant" if behavior == "stale" else None,
                )
                bad = {"action_id": "choose", "modes": ["mode_1"]}
                self.rejects(game, "A", {**bad, "targets": [bear.ref]})
                self.rejects(game, "A", {**bad, "targets": [mine.ref, other.ref]})
                self.choose_mode(game, "mode_1", () if behavior == "zero" else (other.ref,))
                if reply:
                    self.cast_for(game, "C", reply, [other.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                start = game.state.event_sequence
                self.resolve_top(game)
                self.assertFalse(game.state.stack)
                self.assertEqual("battlefield", game.state.cards[mine.object_id].zone)
                self.assertEqual("battlefield" if behavior == "zero" else "graveyard",
                                 game.state.cards[other.object_id].zone)
                self.assertEqual({}, game.state.cards[source.object_id].counters)
                events = [event for event in game.state.events if event.event_id > start and event.code == "permanent.destroyed"]
                self.assertEqual(1 if behavior == "destroy" else 0, len(events))
                self.replay(game, load=True)

    def test_artifact_payment_is_mandatory_uses_current_control_and_survives_replay(self):
        for index, (behavior, replacement) in enumerate((
            ("paid", False), ("paid", True), ("no-artifact", False), ("source-left", False),
        )):
            with self.subTest(behavior=behavior, replacement=replacement):
                game, source, mine, other, bear, bounce = self.start_trigger(
                    7302820 + index, controlled=behavior != "no-artifact", replacement=replacement,
                    response="Unsummon" if behavior == "source-left" else None,
                )
                self.rejects(game, "A", {"action_id": "choose", "modes": ["mode_2"], "targets": [mine.ref if mine else other.ref]})
                self.choose_mode(game, "mode_2")
                if bounce:
                    self.cast_for(game, "C", bounce, [source.ref])
                    self.resolve_top(game)
                decision = self.resolve(game)
                if mine:
                    self.assertEqual(("semantic.choice", ("A",)), (decision.kind, tuple(decision.actors)))
                    game = self.replay(game, load=True)
                    self.rejects(game, "A", {"action_id": "choose", "cards": [], "pay": False})
                    self.rejects(game, "A", {"action_id": "choose", "cards": [other.ref]})
                    self.rejects(game, "A", {"action_id": "choose", "cards": [bear.ref]})
                    with patch.object(game.engine, "move_objects_simultaneously_intent",
                                      wraps=game.engine.move_objects_simultaneously_intent) as moves:
                        result = game.act("pilot:A", {"action_id": "choose", "cards": [mine.ref]})
                    self.assertTrue(result.ok, result.summary)
                    paid, = [call.args[0] for call in moves.call_args_list
                             if call.args[0].transition_kind is ZoneTransitionKind.SACRIFICE]
                    self.assertEqual((mine.ref,), paid.object_refs)
                    self.assertEqual("exile" if replacement else "graveyard", game.state.cards[mine.object_id].zone)
                else:
                    self.assertIsNone(decision)
                self.assertFalse(game.state.stack)
                self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
                current = game.state.cards[source.object_id]
                self.assertEqual("hand" if bounce else "battlefield", current.zone)
                self.assertEqual({"+1/+1": 2} if behavior == "paid" else {}, current.counters)
                self.replay(game, load=True)

    def test_modal_admission_requires_the_mandatory_artifact_owner_and_exact_counter_result(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (ability for ability in semantics.programs_for_oracle(record.oracle_id)
                    if ability.target_schema and "modes" in ability.target_schema)
        self.assertIn(MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY, program.capability_dependencies)
        schema = deepcopy(program.target_schema)
        schema["modes"]["mode_2"]["effects"][0]["effects"][0]["amount"] = 1
        mutated = bind_semantic_program_runtime(replace(program, target_schema=schema),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(mutated["strict"], mutated)
        deps = tuple(dep for dep in program.capability_dependencies if dep != MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY, report["blockers"])
        with patch("quorune.compiler.fixed_effect_payment_templates._MANDATORY_ARTIFACT") as pattern:
            pattern.fullmatch.return_value = None
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)

"""Actual Saga chapters and the original source's continuous control interval."""

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
from quorune.phasing_model import PhasingRecord
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_control_phase_in_model import SOURCE_CONTROL_PHASE_OUT_CAPABILITY, source_control_phase_out_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class SourceControlledPhaseInRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-controlled-phase-in.sqlite3"
        fixture = ROOT / "tests/fixtures/source-controlled-phase-in-restriction-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Saga controlled phase-in", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def playing(self, seed):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"][3:]):
                game.engine.move_card(object_id, "library", log=False)
        return game

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 2, "U": 2})
        self.checkpoint(game)

    def add_plains(self, game, seat, number):
        return tuple(self.add(game.engine, "Plains", seat=seat, ref=f"phasing-price-{seat}-{index}")
                     for index in range(number))

    def choose_read_ahead(self, game, saga, chapter):
        self.cast(game, "A", saga)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = (value for value in options if value["id"].endswith(f":chapter:{chapter}"))
        result = game.act("pilot:A", {"action_id": "choose", "replacement": option["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(chapter, game.state.cards[saga.object_id].counters["lore"])
        self.until(game, lambda session: session.state.pending_decision is not None
                   and session.state.pending_decision.kind == "semantic.target")

    def assert_chapter_target(self, game, target, chapter):
        self.target(game, "A", [target.ref])
        item = game.state.stack[-1]
        semantic = game.engine.semantics.get(item.semantic_key)
        self.assertIsNotNone(semantic)
        self.assertEqual(f"saga.chapter.{chapter}", semantic.event)

    def upkeep(self, game, seat):
        self.until(game, lambda session: session.state.active_player == seat and session.state.step == "upkeep")

    def test_paid_both_chapters_hold_each_subject_and_its_attachments_until_ordinary_untap(self):
        game = self.playing(76346001)
        saga = self.add(game.engine, "The Phasing of Zhalfir", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        equipment = self.add(game.engine, "Bonesplitter", seat="B")
        second = self.add(game.engine, "Sol Ring", seat="C")
        concealment = self.add(game.engine, "Clever Concealment", zone="hand")
        own_lands = self.add_plains(game, "A", 4)
        attach_objects(game.state.cards, equipment, first,
                       source_timestamp=equipment.zone_timestamp, players=game.state.players)
        self.ready(game, saga, equipment, second, concealment)
        self.choose_read_ahead(game, saga, 1)
        initial = authoritative_state_hash(game.state)
        for rejected in (saga, own_lands[0]):
            result = game.act("pilot:A", {"action_id": "choose", "targets": [rejected.ref]})
            self.assertFalse(result.ok)
            self.assertEqual(initial, authoritative_state_hash(game.state))
        self.assert_chapter_target(game, first, 1)
        self.assertIsNone(self.resolve(game))
        phase = game.state.cards[first.object_id].phasing_record
        self.assertEqual("B", phase.return_seat)
        self.assertEqual(saga.logical_object_id, phase.source_control_restriction.source.logical_object_id)
        self.assertTrue(game.state.cards[equipment.object_id].phasing_record.indirect)
        self.assertIsNone(game.state.cards[equipment.object_id].phasing_record.source_control_restriction)
        game = self.replay(game, load=True)
        for seat in "BCDA":
            self.upkeep(game, seat)
        self.assertTrue(game.state.cards[first.object_id].phased_out)
        self.assertTrue(game.state.cards[equipment.object_id].phased_out)
        self.assert_chapter_target(game, second, 2)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[second.object_id].phased_out)
        self.cast(game, "A", concealment, [saga.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[saga.object_id].phased_out)
        self.assertTrue(all(game.state.cards[card.object_id].phased_out for card in (first, second, equipment)))
        self.upkeep(game, "B")
        self.assertFalse(game.state.cards[first.object_id].phased_out)
        self.assertFalse(game.state.cards[equipment.object_id].phased_out)
        self.assertEqual(first.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertTrue(game.state.cards[second.object_id].phased_out)
        self.upkeep(game, "C")
        self.assertFalse(game.state.cards[second.object_id].phased_out)
        for seat in "DA":
            self.upkeep(game, seat)
        self.assertFalse(game.state.cards[saga.object_id].phased_out)
        self.until(game, lambda session: session.state.cards[saga.object_id].counters.get("lore") == 3
                   and bool(session.state.stack))
        final = game.engine.semantics.get(game.state.stack[-1].semantic_key)
        self.assertEqual("saga.chapter.3", final.event)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[saga.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[second.object_id].zone)
        token, = (card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.assertEqual(("Phyrexian Token", "B", "B"), (token.printed_name, token.owner, token.controller))
        self.replay(game, load=True)

    def test_paid_source_control_round_trip_ends_the_old_restriction_even_after_reacquisition(self):
        for loss_before_resolution in (False, True):
            with self.subTest(loss_before_resolution=loss_before_resolution):
                game = self.playing(76346020 + loss_before_resolution)
                saga = self.add(game.engine, "The Phasing of Zhalfir", zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                theft = self.add(game.engine, "Steal Enchantment", seat="C", zone="hand")
                flash = self.add(game.engine, "Vedalken Orrery", seat="C")
                destroy = self.add(game.engine, "Disenchant", zone="hand")
                self.add_plains(game, "A", 4)
                game.state.players["C"].mana_pool["U"] = 2
                self.ready(game, saga, theft, flash, destroy)
                self.choose_read_ahead(game, saga, 2)
                self.assert_chapter_target(game, target, 2)
                current = game.state.cards[saga.object_id]
                original_identity = current.logical_object_id
                original_epoch = current.source_continuity.control_epoch
                if not loss_before_resolution:
                    self.resolve_top(game)
                    restriction = game.state.cards[target.object_id].phasing_record.source_control_restriction
                    self.assertEqual(original_epoch, restriction.continuity.control_epoch)
                self.cast(game, "C", theft, [saga.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[saga.object_id].controller)
                self.cast(game, "A", destroy, [theft.ref])
                self.resolve_top(game)
                source = game.state.cards[saga.object_id]
                self.assertEqual(("A", original_identity), (source.controller, source.logical_object_id))
                self.assertGreater(source.source_continuity.control_epoch, original_epoch)
                if loss_before_resolution:
                    self.assertIsNone(self.resolve(game))
                    self.assertIsNone(game.state.cards[target.object_id].phasing_record.source_control_restriction)
                self.assertTrue(game.state.cards[target.object_id].phased_out)
                game = self.replay(game, load=True)
                self.upkeep(game, "B")
                self.assertFalse(game.state.cards[target.object_id].phased_out)
                self.replay(game, load=True)

    def test_paid_original_chapter_phases_the_target_after_departure_without_starting_the_restriction(self):
        game = self.playing(76346003)
        saga = self.add(game.engine, "The Phasing of Zhalfir", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add_plains(game, "B", 2)
        self.ready(game, saga, destroy)
        self.choose_read_ahead(game, saga, 2)
        self.assert_chapter_target(game, target, 2)
        self.cast(game, "B", destroy, [saga.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[saga.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[target.object_id].phased_out)
        self.assertIsNone(game.state.cards[target.object_id].phasing_record.source_control_restriction)
        self.upkeep(game, "B")
        self.assertFalse(game.state.cards[target.object_id].phased_out)
        self.replay(game, load=True)

    def test_complete_saga_keeps_read_ahead_and_final_chapter_and_requires_the_duration_authority(self):
        cap = SOURCE_CONTROL_PHASE_OUT_CAPABILITY
        report, programs = register_card(self.db, self.registry, "The Phasing of Zhalfir")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        originals = programs.programs_for_oracle(report["oracle_id"])
        by_event = {row.event: row for row in originals if row.event.startswith("saga.chapter.")}
        self.assertEqual({"saga.chapter.1", "saga.chapter.2", "saga.chapter.3"}, set(by_event))
        self.assertTrue(any(row.provenance.get("template_id") == "read-ahead-saga-entry-choice-v1" for row in originals))
        self.assertEqual("destroy_all", by_event["saga.chapter.3"].effects[0]["op"])
        for chapter in (1, 2):
            program = by_event[f"saga.chapter.{chapter}"]
            self.assertEqual(source_control_phase_out_effect(), dict(program.effects[0]))
            self.assertTrue(program.target_schema["source_exclusion"])
            deps = tuple(key for key in program.capability_dependencies if key != cap)
            withdrawn = replace(program, capability_dependencies=deps,
                                capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        malformed = replace(by_event["saga.chapter.1"], event="saga.chapter.3")
        self.assertFalse(bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)["strict"])
        legacy = {"return_seat": "B", "attached_object": None, "attached_player": None,
                  "indirect": False, "departed_ready": False}
        self.assertEqual(legacy, PhasingRecord.from_dict(legacy).to_dict())
        original = self.db.lookup("The Phasing of Zhalfir", fuzzy=False)
        for before, after in (("Another target", "Target"), ("nonland permanent", "creature"),
                              ("for as long as you control this Saga", "until your next turn"),
                              ("this Saga.", "this Saga. Rewrite history.")):
            with self.subTest(unsupported=after):
                synthetic = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)

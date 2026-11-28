from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import CREATURE_SET_PHASE_HOLD_SUBJECT, PhasingRecord, validate_phase_out_effect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counted_creature_phase_hold import COUNTED_CREATURE_PHASE_HOLD_CAPABILITY, counted_creature_phase_hold_runtime_dependencies
from quorune.rules.phase_occurrence_model import PHASE_IN_EVENT
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "Out of Time"
KING = "King of the Oathbreakers"
PROFILE = "commander_review"


class CountedCreaturePhaseHoldTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "counted-creature-phase-hold-cards.json": {CARD},
            "spell-target-and-phase-in-cards.json": {KING},
            "ability-kind-cost-modifier-cards.json": {"Bonesplitter"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Disenchant"},
            "controlled-attacker-delayed-phase-out-cards.json": {"Time Warp"},
            "activation-public-state-cards.json": {"Raise the Alarm"},
            "announced-reinforce-cards.json": {"Solemnity"},
        }
        pieces = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [r for r in document["cards"] if r["name"] in names]
            assert {r["name"] for r in document["cards"]} == names, filename
            oracle_ids = {r["oracle_id"] for r in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "counted-creature-phase-hold.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Counted creature phase hold", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_whole_card_closes_original_entry_and_two_ordinary_vanishing_triggers(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"permanent.enter.self", "step.begin", "counter.removed"}, set(programs))
        phase = programs["permanent.enter.self"]
        self.assertIsNone(phase.target_schema)
        effect, = phase.effects
        self.assertEqual(CREATURE_SET_PHASE_HOLD_SUBJECT, validate_phase_out_effect(effect))
        self.assertIn(COUNTED_CREATURE_PHASE_HOLD_CAPABILITY, counted_creature_phase_hold_runtime_dependencies(phase))
        deps = tuple(value for value in phase.capability_dependencies if value != COUNTED_CREATURE_PHASE_HOLD_CAPABILITY)
        forged = replace(phase, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)["strict"])
        for changes in ({"schema_version": 11.0}, {"tapped_return": True}, {"count": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_phase_out_effect({**effect, **changes})
        source = ContinuousObjectIdentity("source", "source@2")
        for tapped in (False, True):
            held = PhasingRecord("A", battlefield_departure_hold=source, linked_phase_in_tapped=tapped)
            self.assertEqual(not tapped, "linked_phase_in_tapped" in held.to_dict())
            self.assertEqual(held, PhasingRecord.from_dict(held.to_dict()))
        with self.assertRaises(ValueError):
            PhasingRecord("A", linked_phase_in_tapped=False)
        for old, new in (("untap all creatures", "tap all creatures"),
                         ("creature that phased out this way", "permanent that phased out this way")):
            with self.subTest(new=new):
                altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(altered, capability_registry=self.registry).status)
        with patch("quorune.compiler.source_self_effect_templates._battlefield_departure_entry_phase_out", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

    def world(self, seed, *, with_king=False, with_warp=False, with_alarm=False, prevent_counters=False):
        game = self.session(seed)
        source = self.add(game.engine, CARD, zone="hand")
        answer = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        king = self.add(game.engine, KING) if with_king else None
        equipment = self.add(game.engine, "Bonesplitter")
        if king is not None:
            attach_objects(game.state.cards, equipment, king, source_timestamp=equipment.zone_timestamp, players=game.state.players)
            king.tapped = True
        warp = self.add(game.engine, "Time Warp", zone="hand") if with_warp else None
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand") if with_alarm else None
        prohibition = self.add(game.engine, "Solemnity", seat="B") if prevent_counters else None
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="response-plains-one")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="response-plains-two")
        for card in (source, answer, king, warp, alarm, prohibition):
            if card is not None:
                self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 6, "U": 4, "W": 4})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual([source.object_id], [item.source_object_id for item in game.state.stack])
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("time", 0))
        return game, source, answer, king, equipment, warp, alarm

    def test_actual_new_creature_tokens_supply_independent_phase_count_and_keep_tap_state(self):
        for prevent in (False, True):
            with self.subTest(prevent=prevent):
                game, source, answer, _, equipment, _, alarm = self.world(702630 + prevent, with_alarm=True, prevent_counters=prevent)
                self.cast(game, "A", alarm)
                self.resolve_top(game)
                tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
                self.assertEqual(2, len(tokens))
                original = {card.object_id: card.logical_object_id for card in tokens}
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0 if prevent else 2, game.state.cards[source.object_id].counters.get("time", 0))
                self.assertFalse(game.state.cards[equipment.object_id].phased_out)
                for token in tokens:
                    current = game.state.cards[token.object_id]
                    self.assertTrue(current.phased_out)
                    self.assertFalse(current.phasing_record.linked_phase_in_tapped)
                game = self.replay(game, load=True)
                self.cast(game, "B", answer, [source.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                for token in tokens:
                    current = game.state.cards[token.object_id]
                    self.assertEqual(("battlefield", False, False, original[token.object_id]),
                                     (current.zone, current.phased_out, current.tapped, current.logical_object_id))
                self.replay(game, load=True)

    def test_actual_vanishing_sacrifices_original_source_and_king_observes_untapped_return(self):
        game, source, _, king, equipment, warp, _ = self.world(702632, with_king=True, with_warp=True)
        identity = king.logical_object_id
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("time"))
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.assertFalse(game.state.cards[king.object_id].tapped)
        self.assertTrue(game.state.cards[equipment.object_id].phasing_record.indirect)
        self.cast(game, "A", warp, ["A"])
        self.assertIsNone(self.resolve(game))
        self.finish_turn(game)
        self.until(game, lambda g: bool(g.state.stack))
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.assertEqual("upkeep", game.state.step)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("time", 0))
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        current = game.state.cards[king.object_id]
        self.assertEqual((identity, False, False), (current.logical_object_id, current.phased_out, current.tapped))
        self.assertEqual(king.object_id, game.state.cards[equipment.object_id].attached_to)
        item, = game.state.stack
        self.assertEqual((king.object_id, PHASE_IN_EVENT), (item.source_object_id, item.context["event"]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        new, = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(("A", True), (new.controller, new.tapped))
        self.replay(game, load=True)

    def test_actual_departed_source_still_untaps_without_phasing_and_empty_group_has_no_counters(self):
        game, source, answer, king, _, _, _ = self.world(702633, with_king=True)
        self.cast(game, "B", answer, [source.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[king.object_id].tapped)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        current = game.state.cards[king.object_id]
        self.assertEqual(("battlefield", False, False), (current.zone, current.phased_out, current.tapped))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("time", 0))
        self.replay(game, load=True)
        empty, source, _, _, artifact, _, _ = self.world(702634)
        self.assertIsNone(self.resolve(empty))
        self.assertEqual(0, empty.state.cards[source.object_id].counters.get("time", 0))
        self.assertFalse(empty.state.cards[artifact.object_id].phased_out)
        self.assertEqual("battlefield", empty.state.cards[source.object_id].zone)
        self.replay(empty, load=True)

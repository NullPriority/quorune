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
from quorune.phasing_model import PhasingRecord, validate_battlefield_phase_out
from quorune.rules.battlefield_phase_hold import (
    BATTLEFIELD_PHASE_HOLD_CAPABILITY, BATTLEFIELD_PHASE_HOLD_SCHEMA,
    battlefield_phase_hold_runtime_dependencies,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.phase_occurrence_model import PHASE_IN_EVENT
from quorune.replacement.immutable import FrozenMap
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "Oubliette"
KING = "King of the Oathbreakers"
PROFILE = "commander_review"


class BattlefieldDeparturePhaseHoldTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "battlefield-departure-phase-hold-cards.json": {CARD},
            "spell-target-and-phase-in-cards.json": {KING},
            "ability-kind-cost-modifier-cards.json": {"Bonesplitter"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Disenchant"},
            "controlled-attacker-delayed-phase-out-cards.json": {"Time Warp"},
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
        path = folder / "battlefield-departure-phase-hold.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Battlefield departure phase hold", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_complete_entry_and_closed_phase_record_reject_changed_scope(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("permanent.enter.self", "battlefield"), (program.event, program.active_zone))
        self.assertEqual(BATTLEFIELD_PHASE_HOLD_SCHEMA, FrozenMap(program.target_schema))
        effect, = program.effects
        for change in ({"schema_version": True}, {"card": "$target.1"}, {"recipient_tapped": False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_battlefield_phase_out({**effect, **change})
        self.assertIn(BATTLEFIELD_PHASE_HOLD_CAPABILITY, battlefield_phase_hold_runtime_dependencies(program))
        deps = tuple(value for value in program.capability_dependencies if value != BATTLEFIELD_PHASE_HOLD_CAPABILITY)
        forged = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)["strict"])
        identity = ContinuousObjectIdentity("enchantment", "enchantment@2")
        held = PhasingRecord("A", battlefield_departure_hold=identity)
        self.assertEqual(held, PhasingRecord.from_dict(held.to_dict()))
        self.assertNotIn("battlefield_departure_hold", PhasingRecord("A").to_dict())
        with self.assertRaises(ValueError):
            PhasingRecord("A", attached_object=identity, indirect=True, battlefield_departure_hold=identity)
        with self.assertRaises(ValueError):
            PhasingRecord("A", departed_ready=True, battlefield_departure_hold=identity)
        for old, new in (("target creature", "target permanent"), ("leaves the battlefield", "changes controller"),
                         ("Tap that creature", "Untap that creature")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
        with patch("quorune.compiler.source_self_effect_templates._battlefield_departure_entry_phase_out", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

    def world(self, seed, *, time_warp=False):
        game = self.session(seed)
        king = self.add(game.engine, KING)
        source = self.add(game.engine, CARD, zone="hand")
        response = self.add(game.engine, "Disenchant", zone="hand", seat="B")
        equipment = self.add(game.engine, "Bonesplitter")
        attach_objects(game.state.cards, equipment, king, source_timestamp=equipment.zone_timestamp, players=game.state.players)
        king.counters["+1/+1"] = 1
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="response-plains-one")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="response-plains-two")
        warp = self.add(game.engine, "Time Warp", zone="hand") if time_warp else None
        for card in (king, source, response, *([warp] if warp is not None else [])):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 6, "U": 4, "B": 3})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.target(game, "A", [king.ref])
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual(source.object_id, game.state.stack[-1].source_object_id)
        return game, source, king, equipment, response, warp

    def test_actual_untap_skips_hold_then_source_departure_phases_in_tapped_with_king_replay(self):
        game, source, king, equipment, disenchant, warp = self.world(610401, time_warp=True)
        original = king.logical_object_id
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[king.object_id]
        self.assertTrue(live.phased_out)
        self.assertFalse(live.tapped)
        self.assertTrue(game.state.cards[equipment.object_id].phasing_record.indirect)
        self.assertEqual(ContinuousObjectIdentity(source.object_id, game.state.cards[source.object_id].logical_object_id),
                         live.phasing_record.battlefield_departure_hold)
        self.cast(game, "A", warp, ["A"])
        self.assertIsNone(self.resolve(game))
        self.finish_turn(game)
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.assertFalse(game.state.stack)
        game = self.replay(game, load=True)
        self.cast(game, "B", disenchant, [source.ref])
        with patch.object(game.engine, "_dispatch_semantic_event", wraps=game.engine._dispatch_semantic_event) as emitted:
            self.resolve_top(game)
        events = [call.args[0] for call in emitted.call_args_list]
        self.assertIn(PHASE_IN_EVENT, events)
        self.assertNotIn("permanent.tap", events)
        live = game.state.cards[king.object_id]
        self.assertEqual(("battlefield", False, True, original, 1),
                         (live.zone, live.phased_out, live.tapped, live.logical_object_id, live.counters["+1/+1"]))
        self.assertIsNone(live.phasing_record)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.cards[equipment.object_id].phased_out)
        self.assertFalse(game.state.cards[equipment.object_id].tapped)
        self.assertEqual(king.object_id, game.state.cards[equipment.object_id].attached_to)
        trigger, = game.state.stack
        self.assertEqual((king.object_id, PHASE_IN_EVENT), (trigger.source_object_id, trigger.context["event"]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        token, = [c for c in game.state.cards.values() if c.is_token and c.zone == "battlefield"]
        self.assertTrue(token.tapped)
        self.assertEqual("A", token.controller)
        self.replay(game, load=True)

    def test_actual_source_departure_before_trigger_leaves_original_target_unchanged(self):
        game, source, king, equipment, disenchant, _ = self.world(610402)
        initial = king.logical_object_id
        self.cast(game, "B", disenchant, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[king.object_id]
        self.assertEqual((initial, False, False), (live.logical_object_id, live.tapped, live.phased_out))
        self.assertIsNone(live.phasing_record)
        self.assertEqual(king.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertFalse(any(c.is_token for c in game.state.cards.values()))
        self.replay(game, load=True)

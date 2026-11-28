"""Native timing and legality for a selected opponent's next-turn protection."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_personal_turn_protection as retained

from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_state import available_life_for_payment
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.targeted_opponent_protection import CAPABILITY, opponent_target_schema
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SOURCE = "Teferi's Reproach"
PROTECTION = "protection_from_everything_until_next_turn"


class TargetedOpponentProtectionTests(InheritedNativeFixture, unittest.TestCase):
    actions = retained.NextPersonalTurnProtectionTests.actions
    until_following_turn = retained.NextPersonalTurnProtectionTests.until_following_turn
    reject = retained.NextPersonalTurnProtectionTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-protection.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/additional-beginning-originals-cards.json",
            ROOT / "tests/fixtures/next-personal-turn-protection-cards.json",
            ROOT / "tests/fixtures/static-step-skip-cards.json",
            ROOT / "tests/fixtures/targeted-opponent-protection-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target opponent protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_oracle_rejects_withdrawal_and_material_neighbors(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.to_dict()["faces"])
        ability, = ir.to_dict()["faces"][0]["nodes"]
        self.assertEqual(card.oracle_text, ability["text"])
        self.assertEqual(opponent_target_schema(), ability["target_schema"])
        for before, after in (
            ("target opponent", "target player"),
            ("that player's next turn", "your next turn"),
            (" and their life total can't change", ""),
            ("nonland permanents", "creatures"),
            ("Exile " + SOURCE, "Exile another spell"),
        ):
            with self.subTest(before=before):
                variant = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    variant, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Selected opponent protection authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
                            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(
            card, capability_registry=registry, capability_profile="commander_review",
        ).status)
        with patch("quorune.compiler.temporary_player_rule_templates._targeted_opponent_protection_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_actual_targeted_nonland_phase_batch_life_lock_and_next_turn_replay(self):
        game = self.session(70216144)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE, zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        creature.counters["+1/+1"] = 1
        ring = self.add(game.engine, "Sol Ring", seat="C")
        ring.tapped = True
        artifact_land = self.add(game.engine, "Ancient Den", seat="C")
        equipment = self.add(game.engine, "Bonesplitter", seat="B")
        other_ring = self.add(game.engine, "Sol Ring", ref="untouched-ring")
        attach_objects(game.state.cards, equipment, creature, source_timestamp=equipment.zone_timestamp,
                       players=game.state.players)
        recover = self.add(game.engine, "Revitalize", seat="C", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        for card in (source, recover, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool.update({"C": 2, "W": 1})
        self.checkpoint(game)
        for seat in "BCD":
            self.assertNotIn(source.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        cast = self.actions(game, "A")["cast:" + source.ref]
        self.assertEqual({"B", "C", "D"}, set(cast["target_schema"]["legal_refs"]))
        self.reject(game, "A", {"action_id": cast["id"], "targets": ["A"], "pay": "auto"})
        self.cast(game, "A", source, ["C"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        phased = (creature, ring, equipment)
        phase_events = [event for event in game.state.events if event.code == "permanent.phase_out"]
        self.assertEqual(1, len(phase_events))
        self.assertEqual({card.ref for card in phased}, set(phase_events[0].details["out"]))
        for before in phased:
            live = game.state.cards[before.object_id]
            self.assertTrue(live.phased_out)
            self.assertEqual(before.logical_object_id, live.logical_object_id)
            self.assertEqual("battlefield", live.zone)
        for root in (creature, ring):
            self.assertEqual("C", game.state.cards[root.object_id].phasing_record.return_seat)
        self.assertTrue(game.state.cards[equipment.object_id].phasing_record.indirect)
        self.assertFalse(game.state.cards[artifact_land.object_id].phased_out)
        self.assertFalse(game.state.cards[other_ring.object_id].phased_out)
        self.assertEqual({"C"}, {seat for seat, player in game.state.players.items() if player.stats.get(PROTECTION)})
        self.assertEqual(0, available_life_for_payment(game.engine, "C"))
        self.assertFalse(life_payment_prohibited(game.engine, "A"))
        bolt_offer = self.actions(game, "B")["cast:" + bolt.ref]
        self.assertIn("A", bolt_offer["target_schema"]["legal_refs"])
        self.assertNotIn("C", bolt_offer["target_schema"]["legal_refs"])
        self.reject(game, "B", {"action_id": bolt_offer["id"], "targets": ["C"], "pay": "auto"})
        self.reject(game, "B", {"action_id": bolt_offer["id"], "targets": [ring.ref], "pay": "auto"})

        before_life = game.state.players["C"].life
        before_hand = len(game.state.players["C"].zones["hand"])
        self.cast(game, "C", recover)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before_life, game.state.players["C"].life)
        self.assertEqual(before_hand, len(game.state.players["C"].zones["hand"]))
        turn = game.state.turn_sequence
        self.until_following_turn(game, turn, "B")
        self.assertTrue(game.state.players["C"].stats.get(PROTECTION))
        self.assertTrue(life_payment_prohibited(game.engine, "C"))
        self.assertTrue(game.state.cards[ring.object_id].phased_out)
        self.until_following_turn(game, turn, "C")
        self.assertNotIn(PROTECTION, game.state.players["C"].stats)
        self.assertFalse(life_payment_prohibited(game.engine, "C"))
        for before in phased:
            live = game.state.cards[before.object_id]
            self.assertFalse(live.phased_out)
            self.assertEqual(before.logical_object_id, live.logical_object_id)
        self.assertEqual(creature.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertEqual({"+1/+1": 1}, game.state.cards[creature.object_id].counters)
        self.assertFalse(game.state.cards[ring.object_id].tapped)
        self.replay(game, load=True)

    def test_illegal_sole_opponent_stops_resolution_and_exile(self):
        game = self.session(70216145)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE, zone="hand")
        response = self.add(game.engine, "Teferi's Protection", seat="C", zone="hand")
        caster_ring = self.add(game.engine, "Sol Ring")
        chosen_land = self.add(game.engine, "Plains", seat="C")
        for card in (source, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        game.state.players["C"].mana_pool.update({"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, ["C"])
        self.cast(game, "C", response)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[response.object_id].zone)
        self.assertTrue(game.state.cards[chosen_land.object_id].phased_out)
        before = [event for event in game.state.events if event.code in {"player.protection", "permanent.phase_out"}]
        self.assertEqual(2, len(before))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.cards[caster_ring.object_id].phased_out)
        after = [event for event in game.state.events if event.code in {"player.protection", "permanent.phase_out"}]
        self.assertEqual([event.event_id for event in before], [event.event_id for event in after])
        self.replay(game, load=True)

    def test_skipped_untap_preserves_phasing_after_next_turn_protection_ends(self):
        game = self.session(70216146)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE, zone="hand")
        stasis = self.add(game.engine, "Stasis", seat="D")
        disenchant = self.add(game.engine, "Disenchant", seat="C", zone="hand")
        ring = self.add(game.engine, "Sol Ring", seat="C")
        ring.tapped = True
        for number in range(2):
            self.add(game.engine, "Plains", seat="C", ref=f"skip-payment-{number}")
        for card in (source, stasis, disenchant):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, ["C"])
        self.resolve_top(game)
        self.assertTrue(game.state.players["C"].stats.get(PROTECTION))
        self.assertTrue(life_payment_prohibited(game.engine, "C"))
        turn = game.state.turn_sequence
        self.until_following_turn(game, turn, "C")
        self.assertNotIn(PROTECTION, game.state.players["C"].stats)
        self.assertFalse(life_payment_prohibited(game.engine, "C"))
        self.assertTrue(game.state.cards[ring.object_id].phased_out)
        game = self.replay(game, load=True)
        self.cast(game, "C", disenchant, [stasis.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[stasis.object_id].zone)
        self.assertTrue(game.state.cards[ring.object_id].phased_out)
        self.until_following_turn(game, game.state.turn_sequence, "C")
        returned = game.state.cards[ring.object_id]
        self.assertFalse(returned.phased_out)
        self.assertFalse(returned.tapped)
        self.assertEqual(ring.logical_object_id, returned.logical_object_id)
        self.replay(game, load=True)

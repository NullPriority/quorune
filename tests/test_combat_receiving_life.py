"""Native grouped combat receiving, original attacking seat, and replay."""

from dataclasses import replace
from hashlib import file_digest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.received_damage_model import RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_CAPABILITY
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.combat_receiving_life import (
    COMBAT_RECEIVING_LIFE_CONTEXT, CombatReceivingLifeReceipt,
)
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Souls of the Faultless"
PROFILE = "commander_review"


class CombatReceivingLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "combat-receiving-life.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "bound-effect-program-cards.json", fixtures / "combat-receiving-life-cards.json"], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Grouped combat receiving",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def _record(self, game, name):
        directory = os.environ.get("QUORUNE_SOULS_RECORD_PARENT")
        if directory is None:
            self.replay(game, load=True)
            return
        path = Path(directory) / name
        game.save(path)
        result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
        manifest = (path / "manifest.json").read_bytes()
        self.assertEqual(3, json.loads(manifest)["record_schema_version"])
        with (path / "manifest.json").open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        receipt = {"manifest_sha256": digest, "commands": len(game.commands),
                   "final_state_sha256": result["final_state_hash"]}
        path.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def _combat(self, game, attackers, blocker):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": {card.ref: "B" for card in attackers}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        block = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: [card.ref for card in attackers]}})
        self.assertTrue(block.ok, block.summary)

    @staticmethod
    def _triggers(game, souls):
        return [item for item in game.state.stack if item.source_object_id == souls.object_id
                and item.context.get("event") == RECEIVED_COMBAT_DAMAGE_EVENT]

    def _receive(self, game, souls):
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        (trigger,) = self._triggers(game, souls)
        receipt = CombatReceivingLifeReceipt.from_dict(trigger.context[COMBAT_RECEIVING_LIFE_CONTEXT])
        self.assertEqual(("A", trigger.context["event_id"]), (receipt.attacking_player, receipt.event_id))
        self.assertEqual(receipt.amount, trigger.context["combat_amount"])
        return trigger, receipt

    @staticmethod
    def _life_history(game):
        return [(event.kind, event.target, event.amount) for event in game.state.turn_history.events
                if event.kind in {"player_gained_life", "player_lost_life"}]

    def test_multiple_attackers_make_one_committed_receipt_and_lethal_source_replays(self):
        game = self.session(61127021)
        souls = self.add(game.engine, ORIGINAL, seat="B")
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"souls-attacker-{i}") for i in (1, 2)]
        valor = self.add(game.engine, "Valor Made Real", seat="B", zone="hand")
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        for card in (souls, valor):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        self.cast_for(game, "B", valor, [souls.ref])
        self.assertIsNone(self.resolve(game))
        incarnation = game.state.cards[souls.object_id].logical_object_id
        self._combat(game, attackers, souls)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.damage")
        self.assertEqual(["B"], list(game.state.pending_decision.actors))
        assignment = game.act("pilot:B", {"action_id": "assign_damage", "assignments": []})
        self.assertTrue(assignment.ok, assignment.summary)
        trigger, receipt = self._receive(game, souls)
        self.assertEqual(("B", 4, incarnation), (trigger.controller, receipt.amount, receipt.recipient_incarnation))
        self.assertEqual("graveyard", game.state.cards[souls.object_id].zone)
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36, 44, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual([("player_gained_life", "B", 4), ("player_lost_life", "A", 4)],
                         self._life_history(game))
        self._record(game, "souls-grouped-lethal")

    def test_attacking_source_gains_then_loses_and_gain_prohibition_does_not_skip_loss(self):
        for prohibited in (False, True):
            with self.subTest(prohibited=prohibited):
                game = self.session(61127022 + prohibited)
                souls = self.add(game.engine, ORIGINAL)
                permit = self.add(game.engine, "High Alert")
                blocker = self.add(game.engine, "Grizzly Bears", seat="B")
                if prohibited:
                    knight = self.add(game.engine, "Knight of Dusk's Shadow", seat="B")
                    self.assert_closed(game, knight)
                for card in (souls, permit):
                    self.assert_closed(game, card)
                souls.acquired_control_turn_count = -1
                self.prepare(game)
                self.checkpoint(game)
                self._combat(game, [souls], blocker)
                trigger, receipt = self._receive(game, souls)
                self.assertEqual(("A", 2), (trigger.controller, receipt.amount))
                self.assertEqual(2, game.state.cards[souls.object_id].marked_damage)
                self.assertEqual("graveyard", game.state.cards[blocker.object_id].zone)
                self.assertIsNone(self.resolve(game))
                expected = 38 if prohibited else 40
                self.assertEqual([expected, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
                history = [("player_lost_life", "A", 2)]
                if not prohibited:
                    history.insert(0, ("player_gained_life", "A", 2))
                self.assertEqual(history, self._life_history(game))
                self._record(game, "souls-attacking-prohibited" if prohibited else "souls-attacking-gain-loss")

    def test_noncombat_and_fully_prevented_combat_never_trigger(self):
        game = self.session(61127024)
        souls = self.add(game.engine, ORIGINAL, seat="B")
        attacker = self.add(game.engine, "Grizzly Bears")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        fog = self.add(game.engine, "Fog", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        for card in (souls, bolt, fog):
            self.assert_closed(game, card)
        attacker.acquired_control_turn_count = -1
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, [souls.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[souls.object_id].marked_damage)
        self.assertEqual([], self._triggers(game, souls))
        self._combat(game, [attacker], souls)
        self.cast_for(game, "B", fog)
        self.resolve_top(game)
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual([], self._triggers(game, souls))
        self.assertEqual([], self._life_history(game))
        self.assertEqual(3, game.state.cards[souls.object_id].marked_damage)
        self.assertEqual("battlefield", game.state.cards[attacker.object_id].zone)
        self._record(game, "souls-prevented-and-noncombat")

    def test_defender_composes_with_exact_grouped_trigger_and_runtime_authentication(self):
        game = self.session(61127025)
        souls = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, souls)
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = game.engine.semantics.programs_for_oracle(record.oracle_id)
        (program,) = (row for row in programs if row.event == RECEIVED_COMBAT_DAMAGE_EVENT)
        self.assertIn("Defender", record.keywords)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status)
        (trigger_node,) = (node for node in ir.faces[0].nodes if node.event == RECEIVED_COMBAT_DAMAGE_EVENT)
        self.assertEqual((39, len(record.oracle_text)), (trigger_node.span.start, trigger_node.span.end))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != RECEIVED_DAMAGE_CAPABILITY)
        for changed in (
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            replace(program, effects=tuple(reversed(program.effects))),
            replace(program, event_condition=None),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        different = record.oracle_text.replace("attacking player loses", "defending player loses")
        other = compile_oracle_card(replace(record, oracle_text=different),
                                    capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(other.material_residuals)

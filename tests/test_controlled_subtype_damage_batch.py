"""Complete damage occurrences count actual distinct opponents once."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.controlled_subtype_damage_batch import (
    CAPABILITY, COMBAT_CAPABILITY, COMBAT_EVENT, COMBAT_MECHANIC, EVENT,
    controlled_subtype_combat_occurrences, controlled_subtype_damage_occurrences,
    controlled_subtype_damage_runtime_dependencies,
)
from quorune.record import authoritative_state_hash
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class ControlledSubtypeDamageBatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-subtype-damage-batch.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/controlled-subtype-damage-batch-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Grouped Pirate damage", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_complete_source_and_withdrawn_batch_authority(self):
        source = self.db.lookup("Malcolm, Keen-Eyed Navigator")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        code = compile_oracle_card(source, **options)
        self.assertEqual("exact", code.status, code.material_residuals)
        node, = [ability for ability in code.faces[0].nodes if ability.event == EVENT]
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual("$context.amount", node.effects[0]["quantity"])
        for old, new in (("deal damage to your opponents", "deal combat damage to your opponents"),
                         ("each opponent dealt damage", "each creature dealt damage")):
            changed = replace(source, oracle_text=source.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Test-only grouped damage withdrawal"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        changed = compile_oracle_card(source, **{**options, "capability_registry": registry})
        self.assertNotEqual("exact", changed.status)

    def test_group_uses_only_positive_player_rows_from_the_controlled_subtype(self):
        def final(source, target, amount, **changes):
            return SimpleNamespace(**{
                "source_logical_object_id": source, "target": target, "dealt_amount": amount,
                "was_dealt": amount > 0, "target_kind": "player", "source_zone": "battlefield",
                "source_controller": "A", "source_subtypes": ("pirate",), **changes,
            })

        occurrence, = controlled_subtype_damage_occurrences(
            [
                final("p1@1", "B", 3), final("p2@1", "B", 1), final("p3@1", "C", 2),
                final("p3@1", "D", 0), final("p4@1", "planeswalker", 2, target_kind="permanent"),
                final("p1@1", "A", 2), final("other@1", "D", 2, source_subtypes=("bear",)),
                final("spell@1", "D", 2, source_zone="stack"),
            ],
            event_id="complete-batch", active_seats=frozenset("ABCD"), wanted_subtypes=frozenset({"pirate"}),
        )
        self.assertEqual(("B", "C"), occurrence.opponents)
        self.assertEqual(3, len(occurrence.hits))
        self.assertEqual(2, occurrence.semantic_context()["amount"])

    def test_broodcaller_sums_only_combat_dragons_and_replays_private_hand_entry(self):
        game = self.session(51020056)
        engine = game.engine
        source = self.add(engine, "Broodcaller Scourge")
        incoming = self.add(engine, "Grizzly Bears", zone="hand", ref="private-broodcaller-bear")
        too_expensive = self.add(engine, "Broodcaller Scourge", zone="hand", ref="private-broodcaller-heavy")
        nonpermanent = self.add(engine, "Murder", zone="hand", ref="private-broodcaller-instant")
        foreign = self.add(engine, "Grizzly Bears", zone="hand", seat="D", ref="private-broodcaller-foreign")
        unrelated = self.add(engine, "Grizzly Bears", ref="unrelated-combat-bear")
        attackers = {}
        for index, (power, seat) in enumerate(((2, "B"), (2, "B"), (1, "C"))):
            ref, = engine.create_token("A", name=f"Fixture Dragon {index}", characteristics={
                "type_line": "Token Creature — Dragon", "power": str(power), "toughness": "2",
            })
            dragon = engine._resolve_object("A", ref, zones={"battlefield"})
            dragon.acquired_control_turn_count = -1
            attackers[ref] = seat
        unrelated.acquired_control_turn_count = -1
        attackers[unrelated.ref] = "B"
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": attackers})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda current: sum(item.context.get("event") == COMBAT_EVENT
                                             for item in current.state.stack) == 2)
        triggers = {item.context["target"]: item for item in game.state.stack if item.context.get("event") == COMBAT_EVENT}
        self.assertEqual({"B": 4, "C": 1}, {seat: row.context["amount"] for seat, row in triggers.items()})
        self.assertEqual(2, len({row.context["event_id"] for row in triggers.values()}))
        self.assertTrue(all(row.source_object_id == source.object_id and not row.targets for row in triggers.values()))
        self.assertEqual(34, game.state.players["B"].life)
        self.assertEqual(39, game.state.players["C"].life)
        for _ in range(2):
            defender = game.state.stack[-1].context["target"]
            pending = self.resolve(game)
            self.assertEqual("semantic.choice", pending.kind)
            refs = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_refs"]
            self.assertEqual(defender == "B", incoming.ref in refs)
            self.assertTrue({too_expensive.ref, nonpermanent.ref, foreign.ref}.isdisjoint(refs))
            command = {"action_id": "choose", "card": incoming.ref if defender == "B" else None}
            for seat in "BCD":
                packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
                self.assertNotIn(too_expensive.ref, packet)
                self.assertNotIn(nonpermanent.ref, packet)
            baseline = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:D", command).ok)
            self.assertFalse(game.act("pilot:A", {"action_id": "choose", "card": nonpermanent.ref}).ok)
            self.assertEqual(baseline, authoritative_state_hash(game.state))
            game = self.replay(game, load=True)
            result = game.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[incoming.object_id].zone)
        self.assertEqual("hand", game.state.cards[too_expensive.object_id].zone)
        self.replay(game, load=True)

    def test_broodcaller_fails_closed_without_exact_combat_amount_authority(self):
        source = self.db.lookup("Broodcaller Scourge", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}

        def closed():
            code = compile_oracle_card(source, **options)
            self.assertEqual("exact", code.status, code.material_residuals)
            node, = [node for face in code.faces for node in face.nodes if node.event == COMBAT_EVENT]
            self.assertIn(COMBAT_CAPABILITY, node.capability_dependencies)
            self.assertEqual("$context.amount", node.effects[0]["query"]["amount"])
            self.assertIsNone(node.target_schema)
            return node

        node = closed()
        for text in (
            source.oracle_text.replace("combat damage", "damage"),
            source.oracle_text.replace("a player", "a planeswalker"),
            source.oracle_text.replace("that damage", "the number of attacking creatures"),
            source.oracle_text.replace("onto the battlefield.", "into your graveyard."),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(source, oracle_text=text), **options).status)
        with patch("quorune.compiler.controlled_subtype_damage_batch._combat_binding_parts", return_value=None):
            with self.assertRaises(AssertionError):
                closed()
        program = SimpleNamespace(
            event=COMBAT_EVENT, coverage=(COMBAT_MECHANIC,), effects=(
                {**node.effects[0], "query": {**node.effects[0]["query"], "amount": 4}},
            ), target_schema=None, active_zone="battlefield", handlers=(), cost_schema=None, trigger_limit=None,
        )
        with self.assertRaises(ValueError):
            controlled_subtype_damage_runtime_dependencies(program)

    def test_broodcaller_damage_receipt_excludes_noncombat_and_other_recipients(self):
        def event(target, amount, **changes):
            return SimpleNamespace(**{
                "target": target, "dealt_amount": amount, "was_dealt": amount > 0, "combat": True,
                "target_kind": "player", "source_zone": "battlefield", "source_controller": "A",
                "source_subtypes": ("dragon",), **changes,
            })

        rows = controlled_subtype_combat_occurrences(
            (event("B", 3), event("B", 2), event("C", 2), event("D", 0),
             event("B", 9, combat=False), event("B", 9, source_subtypes=("cat",)),
             event("B", 9, source_controller="D"), event("walker", 9, target_kind="permanent")),
            event_id="damage", active_seats=frozenset("ABCD"), wanted_subtypes=frozenset({"dragon"}),
        )
        self.assertEqual([("A", "B", 5), ("A", "C", 2), ("D", "B", 9)],
                         [(row["source_controller"], row["target"], row["amount"]) for row in rows])
        self.assertEqual(3, len({row["event_id"] for row in rows}))

    def game(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Malcolm, Keen-Eyed Navigator", ref="group-observer")
        self.assert_closed(game, source)
        return game, source

    def grouped_triggers(self, game):
        return [item for item in game.state.stack if item.context.get("event") == EVENT]

    def treasures(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Treasure Token"]

    def test_native_first_strike_and_regular_damage_create_separate_grouped_triggers(self):
        game, source = self.game(1204001)
        d = self.add(game.engine, "Generic First Strike Pirate", ref="first-strike-source")
        b1 = self.add(game.engine, "Generic Ordinary Pirate", ref="regular-b-first")
        b2 = self.add(game.engine, "Generic Draw Pirate", ref="regular-b-second")
        c = self.add(game.engine, "Generic Ordinary Pirate", ref="regular-c")
        other = self.add(game.engine, "Grizzly Bears", ref="unrelated-d")
        self.assert_closed(game, b2)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        declared = {d.ref: "D", b1.ref: "B", b2.ref: "B", c.ref: "C", other.ref: "D"}
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": declared})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda current: bool(self.grouped_triggers(current)))
        first, = self.grouped_triggers(game)
        self.assertEqual(source.object_id, first.source_object_id)
        self.assertEqual(["D"], first.context["damaged_opponents"])
        self.assertEqual(1, first.context["amount"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(1, len(self.treasures(game)))
        self.until(game, lambda current: bool(self.grouped_triggers(current)))
        regular, = self.grouped_triggers(game)
        self.assertNotEqual(first.context["event_id"], regular.context["event_id"])
        self.assertEqual(["B", "C"], regular.context["damaged_opponents"])
        self.assertEqual(3, len(regular.context["damage_hits"]))
        self.assertTrue(any(item.source_object_id == b2.object_id and item.kind == "triggered_ability"
                            for item in game.state.stack))
        self.settle(game)
        self.assertEqual(3, len(self.treasures(game)))
        self.replay(game, load=True)

    def noncombat_scene(self, seed, *, departed=False):
        game, source = self.game(seed)
        dealer = self.add(game.engine, "Generic Damage Pirate", ref="noncombat-dealer")
        answer = self.add(game.engine, "Murder", seat="B", zone="hand") if departed else None
        self.assert_closed(game, dealer)
        self.prepare(game, mana={})
        if departed:
            game.state.players["B"].mana_pool.update({"B": 2, "C": 1})
        self.checkpoint(game)
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        offer = next(row for row in packet["legal"]["actions"] if row["id"].startswith(f"activate:{dealer.ref}:"))
        accepted = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        if departed:
            self.cast(game, "B", answer, [dealer.ref])
            self.resolve_top(game)
            self.assertEqual("graveyard", game.state.cards[dealer.object_id].zone)
        self.resolve_top(game)
        triggers = self.grouped_triggers(game)
        self.assertEqual(1, len(triggers))
        self.assertEqual(source.object_id, triggers[0].source_object_id)
        self.assertEqual(["B", "C", "D"], triggers[0].context["damaged_opponents"])
        self.assertEqual(3, triggers[0].context["amount"])
        self.assertEqual([39, 39, 39], [game.state.players[seat].life for seat in "BCD"])
        return game

    def test_native_noncombat_batch_hits_all_opponents_once_and_replays(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.noncombat_scene(1204002 + 2 * departed, departed=departed)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(3, len(self.treasures(game)))
                self.replay(game, load=True)

    def test_missing_group_dispatch_breaks_native_noncombat_witness(self):
        with patch("quorune.rules.controlled_subtype_damage_batch.controlled_subtype_damage_occurrences", return_value=()):
            with self.assertRaises(AssertionError):
                self.noncombat_scene(1204003)

"""Native evidence for one source combat-damage untap and additional combat."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler import trigger_result_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.extra_combat_model import (
    EXTRA_COMBAT_CAPABILITY, EXTRA_COMBAT_FOLLOWUP_FIELD, EXTRA_COMBAT_PHASES_FIELD,
    ExtraCombatUntap, decode_extra_combat_effect, extra_combat_effect,
)
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PORT = "Port Razer"
BLOOD = "Bloodthirster"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = (PORT, BLOOD)


class DamageTriggeredExtraCombatTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-restriction-material-cards.json": {
                "Unsummon", "Cloudshift", "Grizzly Bears", "Sol Ring", "Plains", "Island",
            },
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "scheduled-turn-skip-cards.json": {"Stonehorn Dignitary"},
            "self-loyalty-instant-permission-cards.json": {"The Wandering Emperor"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = [FIXTURES / "damage-triggered-extra-combat-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / name
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "damage-triggered-extra-combat.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Extra combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _ready_source(self, game, name):
        source = self.add(game.engine, name)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def _combat_damage_trigger(self, game, source, *, index=0):
        self.until(game, lambda g: self.trigger_count(g, source) == 1)
        self.assertEqual(("combat", "combat_damage", index),
                         (game.state.phase, game.state.step, game.state.combat.damage_step_index))
        return game.state.combat.damage_sequence_id

    def _next_attack(self, game, previous_combat):
        self.until(game, lambda g: g.state.pending_decision is not None
                   and g.state.pending_decision.kind == "combat.attackers"
                   and g.state.combat.damage_sequence_id != previous_combat)
        return game.state.combat.damage_sequence_id

    @staticmethod
    def _started_combats(game, turn_sequence):
        return [e for e in game.state.events if e.code == "step.begin"
                and e.step == "beginning_combat" and e.turn_sequence == turn_sequence]

    def test_exact_wire_and_empty_phase_record_keep_the_established_shape(self):
        for mode in ExtraCombatUntap:
            with self.subTest(mode=mode):
                descriptor = extra_combat_effect(mode)
                self.assertIs(mode, decode_extra_combat_effect(descriptor))
                for malformed in (
                    None,
                    dict(descriptor, **{EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": True}}),
                    dict(descriptor, op="tap"),
                    dict(descriptor, phase="main"),
                ):
                    with self.assertRaises(ValueError):
                        decode_extra_combat_effect(malformed)
                if mode is ExtraCombatUntap.SOURCE:
                    resolved = {**descriptor, EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "source_card": None}}
                    self.assertIs(mode, decode_extra_combat_effect(resolved, resolved=True))
                    malformed = {**descriptor, EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "source_card": "$target.0"}}
                    with self.assertRaises(ValueError):
                        decode_extra_combat_effect(malformed)
        game = self.session(4821900)
        raw = game.state.to_dict()
        self.assertNotIn(EXTRA_COMBAT_PHASES_FIELD, raw)
        restored = GameState.from_dict(raw)
        self.assertEqual(raw, restored.to_dict())
        self.assertEqual(authoritative_state_hash(game.state), authoritative_state_hash(restored))
        for malformed in (0, -1, True, None, "2"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                GameState.from_dict(dict(raw, **{EXTRA_COMBAT_PHASES_FIELD: malformed}))

    def test_complete_originals_keep_damage_untap_and_attack_history_siblings(self):
        for name, untap in ((PORT, ExtraCombatUntap.CONTROLLED_CREATURES), (BLOOD, ExtraCombatUntap.SOURCE)):
            with self.subTest(name=name):
                record, programs = self._original(name)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                trigger, = [p for p in programs if p.event == "damage.dealt.self"]
                self.assertIn(EXTRA_COMBAT_CAPABILITY, trigger.capability_dependencies)
                result, = trigger.effects
                self.assertEqual(extra_combat_effect(untap), dict(result))
                self.assertTrue(any(p.event != trigger.event and p.active_zone == "battlefield" for p in programs))
                missing = tuple(value for value in trigger.capability_dependencies if value != EXTRA_COMBAT_CAPABILITY)
                from dataclasses import replace
                from quorune.card_programs.binding import bind_semantic_program_runtime

                altered = replace(trigger, capability_dependencies=missing,
                                  capability_closure=self.registry.closure(missing, profile=PROFILE).to_dict())
                rejected = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(rejected["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + EXTRA_COMBAT_CAPABILITY, rejected["blockers"])

    def test_actual_port_razer_untaps_its_creatures_and_keeps_direct_player_history(self):
        game = self.session(4821910)
        source = self._ready_source(game, PORT)
        ally = self.add(game.engine, "Grizzly Bears")
        outsider = self.add(game.engine, "Grizzly Bears", seat="B", ref="extra-combat-other-bear")
        artifact = self.add(game.engine, "Sol Ring")
        walker = self.add(game.engine, "The Wandering Emperor", seat="B")
        private = self.add(game.engine, "Sol Ring", seat="D", zone="hand", ref="extra-combat-private-d")
        walker.counters["loyalty"] = 3
        for card in (ally, outsider, artifact):
            card.tapped = True
        self.prepare(game)
        self.checkpoint(game)
        turn = game.state.turn_sequence
        turn_id = game.state.current_turn.turn_id
        self.attack(game, [(source, "B")])
        first = self._combat_damage_trigger(game, source)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual([False, False, True, True], [
            game.state.cards[card.object_id].tapped for card in (source, ally, outsider, artifact)])
        self.assertEqual(1, len(self._started_combats(game, turn)))
        game = self.replay(game, load=True)
        prior_starts = len(self._started_combats(game, turn))
        second = self._next_attack(game, first)
        self.assertNotEqual(first, second)
        self.assertEqual((turn_id, turn, "A"), (game.state.current_turn.turn_id, game.state.turn_sequence, game.state.active_player))
        self.assertEqual(0, game.state.extra_combat_phases)
        domains = game.engine._attack_declaration_problem("A").domains[source.ref]
        self.assertNotIn("B", domains)
        self.assertIn("C", domains)
        self.assertIn(walker.ref, domains)
        before = authoritative_state_hash(game.state)
        bad = {"action_id": "attack", "attackers": {source.ref: "B"}}
        self.assertFalse(game.act("pilot:A", bad).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.attack(game, [(source, walker.ref)])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        hit, = (
            row for event in game.state.events if event.code == "combat.damage"
            for row in event.details["damage_events"]
            if row["source"] == source.ref and row["target"] == walker.ref
        )
        self.assertEqual((4, "permanent"), (hit["amount"], hit["target_kind"]))
        self.assertIn("planeswalker", hit["target_types"])
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertEqual(prior_starts + 1, len(self._started_combats(game, turn)))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_bloodthirster_keeps_both_damage_grants_through_stun_and_one_combat_skip(self):
        game = self.session(4821920)
        source = self._ready_source(game, BLOOD)
        source.counters["stun"] = 1
        ally = self.add(game.engine, "Grizzly Bears")
        ally.tapped = True
        strobe = self.add(game.engine, "Assault Strobe", zone="hand")
        dignitary = self.add(game.engine, "Stonehorn Dignitary", seat="B")
        dignitary.tapped = True
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", strobe, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertLessEqual({"flying", "trample", "double strike"}, set(game.engine._combat_keywords(source)))
        self.attack(game, [(source, "B")])
        combat_id = self._combat_damage_trigger(game, source, index=0)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertTrue(current.tapped)
        self.assertEqual(0, current.counters.get("stun", 0))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual(combat_id, self._combat_damage_trigger(game, source, index=1))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.extra_combat_phases)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertTrue(game.state.cards[ally.object_id].tapped)
        turn = game.state.turn_sequence
        self.assertEqual(2, game.state.to_dict()[EXTRA_COMBAT_PHASES_FIELD])
        self.assertEqual(1, len(self._started_combats(game, turn)))
        game = self.replay(game, load=True)
        prior_starts = len(self._started_combats(game, turn))
        self.cast(game, "B", blink, [dignitary.ref])
        self.target(game, "B", ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.extra_combat_phases)
        grant, = game.state.scheduled_turn_skips
        self.assertEqual(("A", "combat"), (grant.player, grant.spec.boundary.value))
        new_combat = self._next_attack(game, combat_id)
        self.assertNotEqual(combat_id, new_combat)
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertFalse(game.state.scheduled_turn_skips)
        self.assertNotIn(EXTRA_COMBAT_PHASES_FIELD, game.state.to_dict())
        self.attack(game, [])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(turn, game.state.turn_sequence)
        self.assertEqual(prior_starts + 1, len(self._started_combats(game, turn)))
        self.replay(game, load=True)

    def test_actual_bloodthirster_still_adds_the_combat_after_its_source_leaves(self):
        game = self.session(4821930)
        source = self._ready_source(game, BLOOD)
        companion = self.add(game.engine, "Grizzly Bears")
        companion.acquired_control_turn_count = -1
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        before = self._combat_damage_trigger(game, source)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.extra_combat_phases)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self._next_attack(game, before)
        self.assertEqual(0, game.state.extra_combat_phases)
        prior_life = game.state.players["C"].life
        self.attack(game, [(companion, "C")])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual(prior_life - 2, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_omitting_the_exact_combat_result_restores_both_original_residuals(self):
        with patch.object(trigger_result_templates, "_extra_combat_after_source_damage_result", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual(["trigger_effect"], [row.kind for row in ir.material_residuals])
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_combat_phase_authority_withdrawal_denies_both_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(registry_data)
        next(row for row in document["capabilities"] if row["id"] == EXTRA_COMBAT_CAPABILITY).update(
            status="blocked", blockers=["Scoped extra combat authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((EXTRA_COMBAT_CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{EXTRA_COMBAT_CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

"""Pinned CR 107.1b and 608.2: one live creature target supplies power."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_creature_power_results import target_creature_power_effect, target_creature_power_node_capabilities
from quorune.scalar_effect_amount_model import TARGET_CREATURE_POWER_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAMES = ("Soul's Majesty", "Soul's Grace", "Wall of Reverence")
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class TargetCreaturePowerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "target-creature-power.sqlite3"
        build_fixture_database([base, FIXTURES / "target-creature-power-result-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current creature power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def complete_program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        admission, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies)
        return card, program

    def test_complete_originals_use_one_ordinary_live_creature_target(self):
        for name in NAMES:
            with self.subTest(name=name):
                _, program = self.complete_program(name)
                schema = program.target_schema
                self.assertEqual(("battlefield",), tuple(schema["zones"]))
                self.assertEqual(("creature",), tuple(schema["types_any"]))
                self.assertEqual(1, schema["count"])
                self.assertEqual(None if name == NAMES[1] else "you", schema.get("controller_relation"))
                effect, = program.effects
                if name == NAMES[2]:
                    self.assertEqual("step.begin", program.event)
                    self.assertEqual("offer_optional_effect", effect["op"])
                    effect, = effect["effects"]
                self.assertEqual(target_creature_power_effect("draw" if name == NAMES[0] else "life"), effect)
        card, _ = self.complete_program(NAMES[0])
        for text in ("Draw cards equal to the power of any target.",
                     "Draw cards equal to the power of any number of target creatures you control.",
                     "Draw cards equal to the power of target creature an opponent controls.",
                     "Draw cards equal to the power of target creature you control. Rewrite history."):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertNotEqual("exact", value.status)

    def test_raw_target_shape_and_scalar_do_not_admit_coercion_or_missing_owner(self):
        _, program = self.complete_program(NAMES[0])
        effect, = program.effects
        scalar = effect["count"]
        for changed in (
            replace(program, target_schema=None),
            replace(program, target_schema={k: v for k, v in program.target_schema.items() if k != "controller_relation"}),
            replace(program, effects=({**effect, "player": "$target.0"},)),
            replace(program, effects=({**effect, "count": {**scalar, "characteristic": "toughness"}},)),
            replace(program, effects=({**effect, "count": {**scalar, "schema_version": True}},)),
            replace(program, effects=({**effect, "count": {**scalar, "coefficient": 2}},)),
        ):
            with self.subTest(changed=changed.to_dict()):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        self.assertFalse(target_creature_power_node_capabilities(effects=program.effects,
            target_schema={**program.target_schema, "count": True}))
        self.assertFalse(target_creature_power_node_capabilities(effects=program.effects,
            target_schema=program.target_schema, cost_schema={"additional_cost": "unrepresented"}))
        deps = [dep for dep in program.capability_dependencies if dep != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def test_actual_souls_majesty_uses_current_power_and_sole_invalid_target_stops_draw(self):
        game = self.session(107115161)
        spell = self.add(game.engine, NAMES[0], zone="hand")
        bearer = self.add(game.engine, "Grizzly Bears", ref="majesty-valid-bear")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="majesty-foreign-bear")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "G": 2})
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        self.assertIn(bearer.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(other.ref, action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": action["id"], "targets": [other.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [bearer.ref])
        initial = tuple(game.state.players["A"].zones["hand"])
        invalid = self.replay(game, load=True)
        self.cast_for(invalid, "B", bounce, [bearer.ref])
        self.assertIsNone(self.resolve(invalid))
        self.assertEqual(initial + (bearer.object_id,), tuple(invalid.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", invalid.state.cards[spell.object_id].zone)
        self.replay(invalid)
        self.cast_for(game, "B", growth, [bearer.ref])
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - set(initial)
        self.assertEqual(5, len(drawn))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(viewer, full=True))
            for oid in drawn:
                self.assertNotIn(game.state.cards[oid].ref, packet)
        self.replay(game, load=True)

    def test_actual_souls_grace_uses_foreign_target_and_clamps_negative_power(self):
        for negative in (False, True):
            with self.subTest(negative=negative):
                game = self.session(107115162 + negative)
                spell = self.add(game.engine, NAMES[1], zone="hand")
                bearer = self.add(game.engine, "Wall of Omens" if negative else "Grizzly Bears", seat="B")
                slip = self.add(game.engine, "Tragic Slip", seat="C", zone="hand")
                self.add(game.engine, "Swamp", seat="C")
                self.assert_closed(game, spell)
                self.prepare(game, mana={"C": 1, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [bearer.ref])
                if negative:
                    self.cast_for(game, "C", slip, [bearer.ref])
                    self.resolve_top(game)
                    self.assertEqual(-1, game.engine._numeric_stat(bearer.object_id, "power"))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40 if negative else 42, game.state.players["A"].life)
                self.assertEqual(40, game.state.players["B"].life)
                self.replay(game)

    def test_actual_wall_of_reverence_announces_target_before_optional_current_power(self):
        game = self.session(107115164)
        wall = self.add(game.engine, NAMES[2])
        bearer = self.add(game.engine, "Grizzly Bears", ref="wall-controlled-bear")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="wall-foreign-bear")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        self.assert_closed(game, wall)
        self.prepare(game)
        self.checkpoint(game)
        for _ in range(50):
            pending = game.state.pending_decision
            if pending.kind == "semantic.target":
                break
            if pending.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            else:
                self.assertEqual("priority", pending.kind)
                command = {"action_id": "pass"}
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("Wall never announced its ordinary target")
        visible = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]
        self.assertIn(bearer.ref, visible)
        self.assertNotIn(foreign.ref, visible)
        command = {"action_id": "choose", "targets": [bearer.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "B", growth, [bearer.ref])
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        resumed = self.replay(game, load=True)
        for session, choice, life in ((game, "decline", 40), (resumed, "apply", 45)):
            result = session.act("pilot:A", {"action_id": "choose", "choice": choice})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(life, session.state.players["A"].life)
            self.replay(session)

    def test_actual_current_power_witness_kills_stale_printed_power_mutation(self):
        from quorune import scalar_effect_amounts

        current = scalar_effect_amounts._reference

        def wrong_power(host, item, spec, **kwargs):
            result = current(host, item, spec, **kwargs)
            return {**result, "power": 2} if spec.origin.value == "target_creature_power" else result

        with patch.object(scalar_effect_amounts, "_reference", side_effect=wrong_power):
            with self.assertRaises(AssertionError):
                self.test_actual_souls_majesty_uses_current_power_and_sole_invalid_target_stops_draw()

    def test_verified_withdrawal_retains_aggregate_draw_and_unrelated_targeting(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Target creature power dependency withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (*NAMES, "Rishkar's Expertise", "Unsummon"):
            admission, _ = register_card(self.db, registry, name)
            self.assertEqual(name not in NAMES, admission["strict_capability_ready"], admission["blockers"])

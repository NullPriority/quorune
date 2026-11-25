"""Resolve the printed all-creature self-damage spells through the canonical batch."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import creature_power_damage_templates
from quorune.creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY, CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_SELF_DAMAGE_SET_CAPABILITY, CREATURE_SELF_DAMAGE_SET_DEPENDENCIES,
    creature_self_damage_set_effect, validate_creature_self_damage_set,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import capability_covered_mechanics, load_default_capability_registry
from quorune.rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SOLAR = "Solar Blaze"
WAVE = "Wave of Reckoning"
ORIGINALS = (SOLAR, WAVE)
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class SimultaneousCreatureSelfDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-public-state-cards.json": {"Disfigure", "Giant Growth", "Grizzly Bears"},
            "announced-x-public-damage-set-cards.json": {"Furnace of Rath", "Dictate of the Twin Gods"},
            "creature-power-multiple-cards.json": {"Vampire Nighthawk"},
            "damage-death-exile-cards.json": {"Wall of Omens"},
            "prepared-power-damage-cards.json": {"Justice Strike"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "simultaneous-creature-self-damage-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "simultaneous-creature-self-damage.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Simultaneous creature self-damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @contextmanager
    def damage_events(self, game, creatures):
        seen = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **fields):
            if event == "damage.dealt":
                marks = tuple(game.state.cards[card.object_id].marked_damage for card in creatures)
                seen.append((dict(context), marks))
            return dispatch(event, context, **fields)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            yield seen

    def _assert_single_own_source_batch(self, events, creatures):
        ids = {card.object_id for card in creatures}
        self.assertEqual(ids, {event["source_object_id"] for event, _marks in events})
        self.assertEqual(len(ids), len(events))
        self.assertEqual(1, len({event["event_id"] for event, _marks in events}))
        for event, _marks in events:
            self.assertEqual(event["source_object_id"], event["target_object_id"])
            self.assertEqual(event["source_logical_object_id"], event["target_logical_object_id"])
            self.assertEqual(event["source_controller"], event["target_controller"])
            self.assertFalse(event["combat"])

    def test_exact_untargeted_wire_rejects_other_scopes_and_keeps_existing_interactions(self):
        payload = creature_self_damage_set_effect()
        mechanics = (CREATURE_POWER_DAMAGE_MECHANIC, "cr-120-damage")
        validate_creature_self_damage_set(payload)
        required = fixed_creature_power_damage_node_capabilities(
            effects=(payload,), target_schema=None, mechanic_ids=mechanics,
        )
        self.assertEqual(CREATURE_SELF_DAMAGE_SET_DEPENDENCIES, required)
        self.assertEqual(required, typed_result_amount_runtime_dependencies(payload))
        self.assertIn(CREATURE_POWER_DAMAGE_MECHANIC, capability_covered_mechanics(required))
        self.assertFalse(fixed_creature_power_damage_node_capabilities(
            effects=(payload,), target_schema={"count": 1}, mechanic_ids=mechanics,
        ))
        self.assertFalse(fixed_creature_power_damage_node_capabilities(
            effects=(payload, {"op": "draw"}), target_schema=None, mechanic_ids=mechanics,
        ))
        for mutation in (
            dict(payload, schema_version=5.0), dict(payload, schema_version=4),
            dict(payload, source="$source"), dict(payload, power_multiplier=2),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies(mutation)
        for text in (
            "Each tapped creature deals damage to itself equal to its power.",
            "Each creature you control deals damage to itself equal to its power.",
            "Each creature deals damage to itself equal to twice its power.",
            "Each creature deals damage to itself equal to its power. Draw a card.",
        ):
            self.assertIsNone(creature_power_damage_templates.fixed_creature_power_damage_effect_template(
                text, card_name="Unsupported creature self-damage",
            ))

    def test_complete_originals_share_the_set_and_justice_strike_retains_its_target(self):
        for name in ORIGINALS:
            card = self.db.lookup(name, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            report, semantics = register_card(self.db, self.registry, name)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])
            self.assertEqual("capability_closed", report["trust_basis"])
            program, = semantics.programs_for_oracle(card.oracle_id)
            self.assertIsNone(program.target_schema)
            self.assertIn(CREATURE_SELF_DAMAGE_SET_CAPABILITY, program.capability_dependencies)
            effect, = program.effects
            validate_creature_self_damage_set(effect)
        report, semantics = register_card(self.db, self.registry, "Justice Strike")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        justice = self.db.lookup("Justice Strike", fuzzy=False)
        old, = semantics.programs_for_oracle(justice.oracle_id)
        self.assertIn(CREATURE_POWER_DAMAGE_CAPABILITY, old.capability_dependencies)
        self.assertEqual("$target.0", old.effects[0]["target"])
        self.assertNotIn("schema_version", old.effects[0])

    def test_paid_wave_applies_own_deathtouch_and_lifelink_and_omits_nonpositive_power(self):
        game = self.session(6496150)
        wave = self.add(game.engine, WAVE, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        vampire = self.add(game.engine, "Vampire Nighthawk", seat="B")
        zero = self.add(game.engine, "Wall of Omens", seat="C")
        negative = self.add(game.engine, "Wall of Omens", seat="D", ref="self-damage-negative")
        reduce = self.add(game.engine, "Disfigure", seat="D", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="self-damage-private")
        self.assert_closed(game, vampire)
        self.prepare(game, mana={"C": 4, "W": 1})
        game.state.players["D"].mana_pool["B"] = 1
        self.checkpoint(game)

        self.cast(game, "A", wave)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "D", reduce, [negative.ref])
        self.resolve_top(game)
        current = game.engine._numeric_stat(game.state.cards[negative.object_id].object_id, "power")
        self.assertEqual(-2, current)
        game = self.replay(game, load=True)
        life = game.state.players["B"].life
        with self.damage_events(game, (bear, vampire)) as events:
            self.assertIsNone(self.resolve(game))
        self._assert_single_own_source_batch(events, (bear, vampire))
        self.assertEqual([(2, 2), (2, 2)], [marks for _event, marks in events])
        self.assertEqual([2, 2], [event["amount"] for event, _marks in events])
        for body in (bear, vampire):
            self.assertEqual("graveyard", game.state.cards[body.object_id].zone)
        for body in (zero, negative):
            self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
            self.assertEqual(0, game.state.cards[body.object_id].marked_damage)
        self.assertEqual(life + 2, game.state.players["B"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_paid_solar_captures_live_power_and_replays_each_affected_controllers_choice(self):
        game = self.session(6496160)
        solar = self.add(game.engine, SOLAR, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        enlightened = self.add(game.engine, "Burrenton Forge-Tender", seat="B")
        furnace = self.add(game.engine, "Furnace of Rath", seat="C")
        dictate = self.add(game.engine, "Dictate of the Twin Gods", seat="D")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        for card in (enlightened, furnace, dictate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "W": 1})
        game.state.players["C"].mana_pool["G"] = 1
        self.checkpoint(game)

        self.cast(game, "A", solar)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "C", growth, [bear.ref])
        self.resolve_top(game)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        with self.damage_events(game, (bear, enlightened)) as events:
            for seat in ("A", "B"):
                self.assertEqual(["pilot:" + seat], game.pending_principals())
                self.assertEqual([], events)
                self.assertTrue(all(game.state.cards[card.object_id].marked_damage == 0 for card in (bear, enlightened)))
                options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                command = {"action_id": "choose", "replacement": options[-1]["id"]}
                unchanged = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:D", command).ok)
                self.assertFalse(game.act("pilot:" + seat, {**command, "replacement": "unissued"}).ok)
                self.assertEqual(unchanged, authoritative_state_hash(game.state))
                chosen = game.act("pilot:" + seat, command)
                self.assertTrue(chosen.ok, chosen.summary)
            self.assertIsNone(self.resolve(game))
        self._assert_single_own_source_batch(events, (bear, enlightened))
        amounts = {event["source_controller"]: (event["assigned_amount"], event["amount"]) for event, _marks in events}
        self.assertEqual({"A": (5, 20), "B": (1, 4)}, amounts)
        self.assertEqual([(20, 4), (20, 4)], [marks for _event, marks in events])
        for card in (bear, enlightened):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_compiler_omission_restores_both_unrepresented_whole_spell_results(self):
        with patch.object(creature_power_damage_templates, "_creature_self_damage_set_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("spell_effect", residual.kind)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_self_damage_set_authority_withdrawal_keeps_targeted_justice_strike(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CREATURE_SELF_DAMAGE_SET_CAPABILITY).update(
            status="blocked", blockers=["Scoped simultaneous creature self-damage withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CREATURE_SELF_DAMAGE_SET_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CREATURE_SELF_DAMAGE_SET_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        targeted, _ = register_card(self.db, registry, "Justice Strike")
        self.assertTrue(targeted["strict_capability_ready"], targeted["blockers"])


if __name__ == "__main__":
    unittest.main()

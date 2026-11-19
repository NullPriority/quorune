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
from quorune.life_gain_permission import (
    LIFE_GAIN_PROHIBITION_CAPABILITY,
    TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD,
    ResolvedLifeGainProhibition,
    temporary_opponent_life_gain_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.life_prohibitions import collect_life_gain_permissions
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Roiling Vortex"
SPELL_CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"


class ZeroManaDamageAndLifeGainPermissionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Plains"},
            "ability-source-counter-cards.json": {"Counterspell"},
            "activation-result-views-cards.json": {"Ornithopter"},
            "additional-conditional-modifier-cards.json": {"Disenchant", "Revitalize"},
            "attached-cast-restriction-cards.json": {"Seal of Cleansing"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "event-player-fixed-damage-cards.json": {"Roiling Vortex"},
            "expend-cards.json": {"Frogmite"},
            "public-life-gain-scopes-cards.json": {"Rhox Faithmender"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "zero-mana-damage-and-life-gain-permission.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Zero-paid damage and life permission", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return card, {p.event: p for p in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_original_keeps_upkeep_and_binds_both_other_results(self):
        card, programs = self.original_programs()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"step.begin", "spell.cast", "activate"}, programs.keys())
        upkeep, = programs["step.begin"].effects
        self.assertEqual(("damage", 1), (upkeep["op"], upkeep["amount"]))
        trigger = programs["spell.cast"]
        self.assertEqual({"field": "mana_spent_total", "op": "eq", "value": 0}, thaw_value(trigger.event_condition))
        self.assertEqual([{"op": "damage", "source": "$source", "target": "$context.player", "amount": 5}],
                         thaw_value(trigger.effects))
        self.assertIsNone(trigger.target_schema)
        self.assertIn("intervening_condition", trigger.coverage)
        activation = programs["activate"]
        self.assertEqual([temporary_opponent_life_gain_effect()], thaw_value(activation.effects))
        self.assertIn(LIFE_GAIN_PROHIBITION_CAPABILITY, activation.capability_dependencies)
        self.assertEqual(1, activation.cost_schema["mana"]["R"])
        self.assertIsNone(activation.target_schema)

    def test_malformed_mana_result_permission_and_resolution_scopes_fail_closed(self):
        _, programs = self.original_programs()
        trigger = programs["spell.cast"]
        effect, = thaw_value(trigger.effects)
        activation = programs["activate"]
        permission, = thaw_value(activation.effects)
        for program, changes in (
            (trigger, {"effects": [{**effect, "amount": 5.0}]}),
            (trigger, {"effects": [{**effect, "target": "$controller"}]}),
            (trigger, {"event_condition": {**thaw_value(trigger.event_condition), "value": False}}),
            (trigger, {"active_zone": "command"}),
            (activation, {"effects": [{**permission, "source": "$source"}]}),
            (activation, {"effects": [{**permission, "delta": 0}]}),
            (activation, {"effects": [{**permission, TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD: {
                "schema_version": 1, "affected_player_relation": "any"}}]}),
            (activation, {"effects": [{**permission, TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD: {
                "schema_version": True, "affected_player_relation": "opponent"}}]}),
            (activation, {"active_zone": "graveyard"}),
        ):
            with self.subTest(event=program.event, changes=changes):
                binding = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)

    def _accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _activate(self, game, seat, card, targets=()):
        self.priority(game, seat)
        action, = (row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + card.ref + ":"))
        self._accept(game, seat, {"action_id": action["id"], "targets": list(targets), "pay": "auto"})

    def _lands(self, game, seat, names):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, ref=f"vortex-{seat}-land-{index}")

    def _trigger_for(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def _advance(self, game, predicate):
        for _ in range(96):
            if predicate(game):
                return
            pending = game.state.pending_decision
            self.assertIsNotNone(pending)
            if pending.kind == "priority":
                command = {"action_id": "pass"}
            elif pending.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif pending.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Unexpected actual decision during turn advance: {pending.kind}")
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail("Native turn advance did not reach the requested boundary")

    def test_actual_zero_paid_cast_damage_survives_source_and_original_spell_departure(self):
        game = self.session(76156841)
        vortex = self.add(game.engine, NAME, seat="B")
        seal = self.add(game.engine, "Seal of Cleansing", seat="D")
        free = self.add(game.engine, "Frogmite", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        self._lands(game, "C", ["Island", "Island"])
        for index in range(4):
            self.add(game.engine, "Ornithopter", ref=f"vortex-affinity-{index}")
        for card in (vortex, seal, free, counter):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", free)
        spell = next(item for item in game.state.stack if item.card_object_id == free.object_id)
        pending, = self._trigger_for(game, vortex)
        self.assertEqual(("B", "A", spell.ref, 0, []),
                         (pending.controller, pending.context["player"], pending.context["stack"],
                          pending.context["mana_spent_total"], pending.targets))
        self.cast(game, "C", counter, [spell.ref])
        self.assertEqual(1, len(self._trigger_for(game, vortex)))
        self._activate(game, "D", seal, [vortex.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[vortex.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[free.object_id].zone)
        self.assertEqual(1, len(self._trigger_for(game, vortex)))
        self.assertEqual(40, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([35, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_activated_opponent_permission_survives_departure_and_expires_at_cleanup(self):
        game = self.session(76156842)
        vortex = self.add(game.engine, NAME)
        rhox = self.add(game.engine, "Rhox Faithmender", seat="B")
        removal = self.add(game.engine, "Disenchant", seat="D", zone="hand")
        earlier, denied, restored = (
            self.add(game.engine, "Revitalize", seat="B", zone="hand", ref="vortex-life-" + phase)
            for phase in ("earlier", "denied", "restored")
        )
        self._lands(game, "B", ["Plains", "Forest", "Plains", "Forest"])
        self._lands(game, "D", ["Plains", "Forest"])
        for card in (vortex, rhox, removal, earlier, denied, restored):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)

        self.cast(game, "B", earlier)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["B"].life)
        self._activate(game, "A", vortex)
        self.assertIsNone(self.resolve(game))
        rule, = (effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedLifeGainProhibition))
        self.assertEqual(("A", ("B", "C", "D")), (rule.controller, rule.denied_players))
        self.assertTrue(collect_life_gain_permissions(game.engine).allows_gain("A"))
        self.assertEqual(rule, ResolvedLifeGainProhibition.from_dict(rule.to_dict()))
        self.cast(game, "D", removal, [vortex.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[vortex.object_id].zone)
        game = self.replay(game, load=True)

        hand = set(game.state.players["B"].zones["hand"])
        self.cast(game, "B", denied)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["B"].life)
        drawn, = set(game.state.players["B"].zones["hand"]) - hand
        for seat in "ACD":
            self.assertNotIn(game.state.cards[drawn].ref, json.dumps(game.packet("pilot:" + seat, full=True)))

        turn = game.state.turn_sequence
        self._advance(game, lambda session: session.state.turn_sequence > turn and session.state.active_player == "B")
        self.assertFalse(any(isinstance(effect, ResolvedLifeGainProhibition) for effect in game.state.continuous_effects))
        self.cast(game, "B", restored)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(52, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_retained_upkeep_damages_only_the_active_player(self):
        game = self.session(76156843)
        vortex = self.add(game.engine, NAME)
        self.assert_closed(game, vortex)
        self.prepare(game)
        self.checkpoint(game)
        self._advance(game, lambda session: bool(self._trigger_for(session, vortex)))
        trigger, = self._trigger_for(game, vortex)
        self.assertEqual(("step.begin", "B", "A"),
                         (trigger.context["event"], trigger.context["player"], trigger.controller))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 39, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_omitting_either_exact_result_restores_its_independent_original_residual(self):
        for dependency in (
            "quorune.compiler.public_trigger_conditions._zero_mana_cast_damage_binding",
            "quorune.compiler.life_templates._temporary_opponent_life_gain_template",
        ):
            with self.subTest(dependency=dependency), patch(dependency, return_value=None):
                binding, _ = register_card(self.db, self.registry, NAME)
                ordinary, _ = register_card(self.db, self.registry, "Revitalize")
                self.assertFalse(binding["strict_capability_ready"], binding)
                self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])

    def test_verified_withdrawals_require_cast_receipt_and_life_permission_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (SPELL_CAST, LIFE_GAIN_PROHIBITION_CAPABILITY):
            with self.subTest(capability=capability):
                raw = deepcopy(source)
                next(row for row in raw["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Vortex cast or life permission dependency withdrawn"])
                registry = CapabilityRegistry(raw)
                index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                original, _ = register_card(self.db, registry, NAME)
                ordinary, _ = register_card(self.db, registry, "Revitalize")
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
                self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])

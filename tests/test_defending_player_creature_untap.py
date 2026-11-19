from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import (
    DEFENDING_PLAYER_UNTAP_SET_FIELD,
    decode_defending_player_creature_untap,
    defending_player_creature_untap_effect,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
from quorune.semantic_runtime.tap_state_handlers import SetPublicTapStateSetHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "defending-player-creature-untap-cards.json"
AUTOMATON = "Jangling Automaton"
TAP_SET = "permanent.tap_state.fixed_set"
SOURCE_ATTACK = "trigger.event.normalized_self_attack"


class DefendingPlayerCreatureUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Fervor", "Unsummon", "Island", "Grizzly Bears"},
            "targeted-graveyard-exile-cards.json": {"Sol Ring", "Shock"},
            "qualified-quantity-retention-cards.json": {"Village Bell-Ringer"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            fixture = json.loads((FIXTURES / name).read_bytes())
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in names]
            assert {card["name"] for card in fixture["cards"]} == names
            ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [row for row in fixture.get("rulings", ()) if row["oracle_id"] in ids]
            subset = folder / name
            subset.write_text(json.dumps(fixture), encoding="utf-8")
            fixtures.append(subset)
        path = folder / "defending-player-untap.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defending player's creature untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_owns_one_untargeted_source_attack_set(self):
        card = self.db.lookup(AUTOMATON, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("creature.attacks", node.event)
        self.assertIsNone(node.target_schema)
        self.assertTrue({TAP_SET, SOURCE_ATTACK, "trigger.placement.apnap"} <= set(node.capability_dependencies))
        self.assertEqual((defending_player_creature_untap_effect(),), node.effects)
        binding, semantic_registry = register_card(self.db, self.registry, AUTOMATON)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantic_registry.programs_for_oracle(card.oracle_id)
        effect, = program.effects
        no_version = {key: value for key, value in effect.items() if key != DEFENDING_PLAYER_UNTAP_SET_FIELD}
        for changed in (
            replace(program, effects=[no_version]),
            replace(program, effects=[{**effect, DEFENDING_PLAYER_UNTAP_SET_FIELD: True}]),
            replace(program, effects=[{**effect, "set": {**effect["set"], "target_controller": "$controller"}}]),
            replace(program, event_condition=None),
            replace(program, event="permanent.enter.self"),
        ):
            checked = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(checked["strict"], checked)
        retained = [dep for dep in program.capability_dependencies if dep != SOURCE_ATTACK]
        missing = replace(program, capability_dependencies=retained,
            capability_closure=self.registry.closure(retained, profile=PROFILE).to_dict())
        rebound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(rebound["strict"], rebound)
        self.assertIn("capability:undeclared_runtime_dependency:" + SOURCE_ATTACK, rebound["blockers"])

        for oracle in (
            "Whenever another creature attacks, untap all creatures defending player controls.",
            "Whenever this creature attacks, untap all artifacts defending player controls.",
            "Whenever this creature attacks, you may untap all creatures defending player controls.",
            "Whenever this creature attacks, you may pay {1}. If you do, untap all creatures defending player controls.",
            "Whenever this creature attacks, tap all creatures defending player controls.",
            card.oracle_text + " Perform an unsupported action.",
        ):
            with self.subTest(oracle=oracle):
                changed = compile_oracle_card(replace(card, oracle_text=oracle),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", changed.status, changed.material_residuals)

    def test_bound_known_departed_defender_has_an_empty_untargeted_result(self):
        context = ReadOnlyHandlerContext.from_sequences(actor="A", default_reason="attack",
            seats="ABCD", active_seats="ACD", apnap_order="ACD")
        handler = SetPublicTapStateSetHandler()
        result = handler.lower(defending_player_creature_untap_effect("B", "source"), context)
        self.assertEqual((), result.intents)
        for recipient in ("foreign", "$context.defending_player"):
            with self.subTest(recipient=recipient), self.assertRaises(SemanticNodeError):
                handler.lower(defending_player_creature_untap_effect(recipient, "source"), context)

    def test_actual_split_attack_untaps_only_retained_defenders_current_creatures(self):
        game = self.session(50850112)
        source = self.add(game.engine, AUTOMATON, zone="hand")
        stay_b = self.add(game.engine, "Grizzly Bears", ref="second-attacker-b")
        attack_c = self.add(game.engine, "Grizzly Bears", ref="attacker-c")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="becomes-legal-blocker")
        stunned = self.add(game.engine, "Grizzly Bears", seat="B", ref="stunned-blocker")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-lane-creature")
        artifact = self.add(game.engine, "Sol Ring", seat="B", ref="defender-noncreature")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="private-defender-test")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Fervor")
        for permanent in (blocker, stunned, other, artifact):
            permanent.tapped = True
        stunned.counters["stun"] = 1
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(source, "B"), (stay_b, "B"), (attack_c, "C")])
        trigger, = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.assertEqual("B", trigger.context["defending_player"])
        self.assertEqual([], trigger.targets)
        self.assertTrue(game.state.cards[blocker.object_id].tapped)
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
        game = self.replay(game, load=True)

        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[blocker.object_id].tapped,
            "The retained defender's unstunned creature must untap")
        self.assertTrue(game.state.cards[stunned.object_id].tapped)
        self.assertEqual(0, game.state.cards[stunned.object_id].counters.get("stun", 0))
        self.assertTrue(game.state.cards[artifact.object_id].tapped)
        self.assertTrue(game.state.cards[other.object_id].tapped)
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.blockers"
            and session.state.pending_decision.actors == ["B"])
        legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertIn(stay_b.ref, legal[blocker.ref])
        self.assertEqual([], legal.get(stunned.ref, []))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": "block", "blocks": {stunned.ref: stay_b.ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        chosen = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: stay_b.ref}})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIn(blocker.object_id, game.state.combat.blockers[stay_b.object_id])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_redirecting_recipient_to_other_combat_lane_breaks_native_witness(self):
        def redirect(effect, *, resolved=False):
            group = decode_defending_player_creature_untap(effect, resolved=resolved)
            return replace(group, target_controller="C")

        with patch("quorune.semantic_runtime.tap_state_handlers.decode_defending_player_creature_untap",
                side_effect=redirect):
            with self.assertRaisesRegex(AssertionError, "unstunned creature must untap"):
                self.test_actual_split_attack_untaps_only_retained_defenders_current_creatures()

    def test_verified_withdrawals_distinguish_the_attack_and_public_tap_set(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            evidence = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
            return registry

        names = (AUTOMATON, "Village Bell-Ringer", "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            binding, _ = register_card(self.db, baseline, name)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for capability, expected in (
            (SOURCE_ATTACK, {AUTOMATON}), (TAP_SET, {AUTOMATON, "Village Bell-Ringer"}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Defender attack or public tap set withdrawn"])
                selected = verified(rows)
                closure = selected.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    binding, _ = register_card(self.db, selected, name)
                    self.assertEqual(name not in expected, binding["strict_capability_ready"],
                        (name, binding["blockers"]))

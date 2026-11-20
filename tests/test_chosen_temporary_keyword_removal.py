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
from quorune.continuous_effects import CharacteristicState, _apply_ability_operation
from quorune.continuous_effect_model import ContinuousOperation
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_keyword_removal import CAPABILITY, CHOICE_FIELD, CHOICE_OPTION_SETS
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SPONGE = "Walking Sponge"
URBORG = "Urborg"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "chosen-temporary-keyword-removal-cards.json"


class ChosenTemporaryKeywordRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {
                "Isamaru, Hound of Konda", "Fervor", "Grizzly Bears", "Unsummon", "Island",
            },
            "targeted-graveyard-exile-cards.json": {"Swamp", "Shock"},
            "landwalk-as-though-cards.json": {"Bog Wraith"},
            "fixed-battlefield-query-characteristics-cards.json": {"Azorius Skyguard"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            snapshot = folder / name
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(snapshot)
        path = folder / "chosen-keyword-removal.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen temporary keyword removal", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_full_originals_keep_one_target_complete_branches_and_printed_mana(self):
        for name, words in zip((SPONGE, URBORG), CHOICE_OPTION_SETS):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = ir.faces[0].nodes
                choice, = [n for n in nodes if any(CHOICE_FIELD in e for e in n.effects)]
                self.assertIn(CAPABILITY, choice.capability_dependencies)
                self.assertIn("target.revalidate_resolution", choice.capability_dependencies)
                self.assertEqual(["creature"], choice.target_schema["types_any"])
                self.assertEqual(name == URBORG, any("mana.activated.fixed_output" in n.capability_dependencies
                    for n in nodes))
                effect, = choice.effects
                self.assertEqual(words, tuple(option["id"] for option in effect["options"]))
                for word, branch in effect["then_by_choice"].items():
                    leaf, = branch
                    self.assertEqual([word], leaf["remove_keywords"])
                    self.assertEqual("$target.0", leaf["card"])
                ready, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if any(e.get("op") == "choose_option" for e in p.effects)]
                for changed in (
                    replace(program, event="resolve", active_zone="stack", handlers=[]),
                    replace(program, effects=[{**effect, CHOICE_FIELD: True}]),
                    replace(program, effects=[{key: value for key, value in effect.items() if key != CHOICE_FIELD}]),
                    replace(program, effects=[{**effect, "then_by_choice": {**effect["then_by_choice"],
                        words[0]: [{**effect["then_by_choice"][words[0]][0], "card": "$source"}]}}]),
                    replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
                ):
                    runtime = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(runtime["strict"], runtime)
                for oracle in (card.oracle_text.replace("creature loses", "permanent loses"),
                        card.oracle_text.replace("until end of turn", "until your next turn"),
                        card.oracle_text.replace("first strike", "haste")):
                    mutated = compile_oracle_card(replace(card, oracle_text=oracle),
                        capability_registry=self.registry, capability_profile=PROFILE)
                    self.assertNotEqual("exact", mutated.status)

    def targeted_activation(self, game, seat, source, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [a for a in actions if a["id"].startswith("activate:" + source.ref + ":")
            and a.get("target_schema")]
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        result = game.act("pilot:" + seat, {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def resolve_choice(self, game, words):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual(words, tuple(row["id"] for row in packet["ctx"]["options"]))

    def test_actual_sponge_choice_removes_one_keyword_and_replays_pending_decision(self):
        game = self.session(60826131)
        source = self.add(game.engine, SPONGE, zone="hand")
        target = self.add(game.engine, "Azorius Skyguard", seat="B")
        self.add(game.engine, "Fervor")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="keyword-choice-private")
        self.assert_closed(game, source)
        self.assert_closed(game, target)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.targeted_activation(game, "A", source, target)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.resolve_choice(game, CHOICE_OPTION_SETS[0])
        self.assertTrue({"flying", "first strike"} <= set(game.engine._combat_keywords(
            game.state.cards[target.object_id])))

        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "Flying"}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "choice": "Haste"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": "Flying"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        keywords = game.engine._combat_keywords(game.state.cards[target.object_id])
        self.assertNotIn("flying", keywords)
        self.assertIn("first strike", keywords)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_urborg_removes_swampwalk_while_the_block_is_still_available(self):
        game = self.session(60826132)
        land = self.add(game.engine, URBORG)
        self.add(game.engine, "Swamp")
        walker = self.add(game.engine, "Bog Wraith", seat="B")
        blocker = self.add(game.engine, "Grizzly Bears")
        walker.acquired_control_turn_count = -1
        self.assert_closed(game, land)
        self.assert_closed(game, walker)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {walker.ref: "A"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(game.engine._can_block(walker, blocker)[0])
        self.targeted_activation(game, "A", land, walker)
        self.resolve_choice(game, CHOICE_OPTION_SETS[1])
        result = game.act("pilot:A", {"action_id": "choose", "choice": "Swampwalk"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("swampwalk", game.engine._combat_keywords(game.state.cards[walker.object_id]))

        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertIn(walker.ref, legal[blocker.ref])
        blocked = game.act("pilot:A", {"action_id": "block", "blocks": {blocker.ref: walker.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.assertIn(blocker.object_id, game.state.combat.blockers[walker.object_id])
        self.replay(game, load=True)

    def test_actual_urborg_black_mana_is_a_separate_tap_action(self):
        game = self.session(60826133)
        land = self.add(game.engine, URBORG)
        self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, land)
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability, = [a for a in game.engine._activated_abilities(land) if a.mana_ability]
        action_id = f"activate:{land.ref}:{ability.ability_id}"
        self.assertTrue(any(row["id"] == action_id for row in actions))
        self.assertTrue(any(a["id"].startswith("activate:" + land.ref + ":") and a.get("target_schema")
            for a in actions))
        paid = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool.get("B"))
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_target_loss_consumes_the_paid_tap_and_skips_the_choice(self):
        game = self.session(60826134)
        source = self.add(game.engine, SPONGE)
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.targeted_activation(game, "A", source, target)
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_named_landwalk_removal_preserves_other_named_or_unrepresented_variants(self):
        state = CharacteristicState("Rule witness", abilities=["Landwalk", "Swampwalk", "Forestwalk", "Flying"])
        _apply_ability_operation(state, ContinuousOperation("remove_ability", "Swampwalk"))
        self.assertEqual(["Landwalk", "Forestwalk", "Flying"], state.abilities)
        _apply_ability_operation(state, ContinuousOperation("remove_ability", "Forestwalk"))
        self.assertEqual(["Flying"], state.abilities)
        unsupported = CharacteristicState("Unrepresented witness", abilities=["Landwalk"])
        _apply_ability_operation(unsupported, ContinuousOperation("remove_ability", "Swampwalk"))
        self.assertEqual(["Landwalk"], unsupported.abilities)

    def test_omitting_the_landwalk_marker_cleanup_breaks_the_actual_block(self):
        with patch("quorune.continuous_effects.LANDWALK_KEYWORDS", ()):
            with self.assertRaisesRegex(AssertionError, "Unsupported landwalk variant"):
                self.test_actual_urborg_removes_swampwalk_while_the_block_is_still_available()

    def test_verified_withdrawals_separate_keyword_removal_from_target_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        originals = {SPONGE, URBORG}
        names = (SPONGE, URBORG, "Unsummon", "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for capability, blocked in (
            (CAPABILITY, originals), ("target.revalidate_resolution", originals | {"Unsummon"}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Chosen keyword removal or target authority withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))

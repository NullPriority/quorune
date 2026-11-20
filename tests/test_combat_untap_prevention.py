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
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Maze of Ith", "Ebony Horse", "Maze of Shadows", "Elvish Scout", "Ith, High Arcanist")
UNTAP = "permanent.untap.effect"
PREVENTION = "damage.prevention.persistent_amount"
BOUND = "resolution.effect_program.bound_references"
ORDERED = "resolution.effect_program.closed_components"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class CombatUntapPreventionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Mountain", "Island", "Shock", "Unsummon", "Murder"},
            "public-graveyard-entry-cards.json": {"Craw Wurm"},
        }
        fixtures = [FIXTURES / "combat-untap-prevention-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            selected = folder / name
            selected.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(selected)
        path = folder / "combat-untap-prevention.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered combat untap and prevention", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_five_complete_originals_retain_exact_target_cost_order_and_siblings(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = ir.faces[0].nodes
                node, = [n for n in nodes if any(e.get("op") == "untap" for e in n.effects)]
                self.assertTrue({UNTAP, PREVENTION, BOUND, ORDERED, "target.revalidate_resolution"}
                    <= set(node.capability_dependencies))
                self.assertEqual("attacking", node.target_schema["combat_state"])
                self.assertEqual("you" if name in {"Ebony Horse", "Elvish Scout"} else None,
                    node.target_schema.get("controller_relation"))
                self.assertEqual(["shadow"] if name == "Maze of Shadows" else None,
                    node.target_schema.get("keywords_all"))
                untap, incoming, outgoing = node.effects
                self.assertEqual({"op": "untap", "card": "$target.0"}, untap)
                self.assertEqual("$target.0", incoming["scope"]["target_ref"])
                self.assertEqual("$target.0", outgoing["scope"]["source_ref"])
                self.assertEqual(("$stack", "$stack"), (incoming["application_group_id"], outgoing["application_group_id"]))
                self.assertEqual(("combat", "combat"), (incoming["damage_kind"], outgoing["damage_kind"]))
                self.assertIs(node.cost["tap_source"], True)
                self.assertEqual(2 if name == "Ebony Horse" else 0, node.cost["mana"]["GENERIC"])
                self.assertEqual(1 if name == "Elvish Scout" else 0, node.cost["mana"]["G"])
                all_deps = {cap for part in nodes for cap in part.capability_dependencies}
                self.assertEqual(name == "Maze of Shadows", "mana.activated.fixed_output" in all_deps)
                if name == "Ith, High Arcanist":
                    self.assertIn("combat.attack.vigilance", all_deps)
                    suspend, = [part for part in nodes if "suspend" in part.mechanics]
                    self.assertEqual("cast.cost", suspend.event)
                    self.assertTrue({"casting.lifecycle.fixed_public", "casting.lifecycle.fixed_zone_cast"}
                        <= set(suspend.capability_dependencies))
                ready, _ = register_card(self.db, self.registry, name)
                self.assertTrue(ready["strict_capability_ready"], ready["blockers"])

    def test_raw_group_requires_both_ordered_combat_directions_and_the_same_target(self):
        card = self.db.lookup("Maze of Ith", fuzzy=False)
        ready, semantic = register_card(self.db, self.registry, card.name)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = [p for p in semantic.programs_for_oracle(card.oracle_id)
            if any(e.get("op") == "untap" for e in p.effects)]
        untap, incoming, outgoing = thaw_value(program.effects)
        for effects in (
            [incoming, outgoing, untap],
            [untap, outgoing, incoming],
            [untap, incoming],
            [untap, incoming, {**outgoing, "application_group_id": "$source"}],
            [untap, incoming, {**outgoing, "damage_kind": "noncombat"}],
            [untap, {**incoming, "scope": {**incoming["scope"], "target_ref": "$target.1"}}, outgoing],
        ):
            with self.subTest(effects=effects):
                result = bind_semantic_program_runtime(replace(program, effects=effects),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def attack_with(self, game, *attackers):
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:B", {"action_id": "attack",
            "attackers": {attacker.ref: "A" for attacker in attackers}})
        self.assertTrue(accepted.ok, accepted.summary)

    def targeted_offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")
            and row.get("target_schema")]
        return offered

    def activate(self, game, source, target):
        offer = self.targeted_offer(game, source)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))

    def test_actual_stun_replacement_still_prevents_both_directions_and_only_combat(self):
        game = self.session(50761411)
        maze = self.add(game.engine, "Maze of Ith")
        attacker = self.add(game.engine, "Craw Wurm", seat="B")
        attacker.counters["stun"] = 1
        unprotected = self.add(game.engine, "Grizzly Bears", seat="B", ref="other-maze-attacker")
        blocker = self.add(game.engine, "Grizzly Bears", ref="maze-blocker")
        shock = self.add(game.engine, "Shock", zone="hand")
        self.add(game.engine, "Mountain")
        self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="maze-private-hand")
        for card in (maze, attacker, shock):
            self.assert_closed(game, card)
        self.attack_with(game, attacker, unprotected)
        before = game.state.players["A"].life
        self.assertTrue(game.state.cards[attacker.object_id].tapped)
        self.activate(game, maze, attacker)
        guarded = game.state.cards[attacker.object_id]
        self.assertEqual((True, 0, "A"), (guarded.tapped, guarded.counters.get("stun", 0), guarded.attacking))
        shields = game.state.damage_prevention_shields
        self.assertEqual(2, len(shields))
        self.assertTrue(shields[0].application_group_id)
        self.assertEqual(shields[0].application_group_id, shields[1].application_group_id)

        self.cast(game, "A", shock, [attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[attacker.object_id].marked_damage)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertIn(attacker.ref, offered[blocker.ref])
        accepted = game.act("pilot:A", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda s: s.state.step == "end_combat")
        self.assertEqual(before - 2, game.state.players["A"].life)
        self.assertEqual(("battlefield", 0), (game.state.cards[blocker.object_id].zone,
            game.state.cards[blocker.object_id].marked_damage))
        self.assertEqual(2, game.state.cards[attacker.object_id].marked_damage)
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_late_untap_does_not_undo_damage_and_untapped_attacker_is_legal(self):
        game = self.session(50761412)
        maze = self.add(game.engine, "Maze of Ith")
        ith = self.add(game.engine, "Ith, High Arcanist")
        ith.acquired_control_turn_count = -1
        ordinary = self.add(game.engine, "Grizzly Bears", seat="B")
        vigilant = self.add(game.engine, "Serra Angel", seat="B")
        for card in (maze, ith, vigilant):
            self.assert_closed(game, card)
        self.attack_with(game, ordinary, vigilant)
        before = game.state.players["A"].life
        self.until(game, lambda s: s.state.step == "end_combat")
        self.assertEqual(before - 6, game.state.players["A"].life)
        self.assertTrue(game.state.cards[ordinary.object_id].tapped)
        self.assertFalse(game.state.cards[vigilant.object_id].tapped)
        self.activate(game, maze, ordinary)
        self.activate(game, ith, vigilant)
        for attacker in (ordinary, vigilant):
            card = game.state.cards[attacker.object_id]
            self.assertEqual((False, "A"), (card.tapped, card.attacking))
        self.assertEqual(before - 6, game.state.players["A"].life)
        self.assertEqual(4, len(game.state.damage_prevention_shields))
        self.replay(game, load=True)

    def test_actual_shadow_and_control_targets_and_departed_target_rollback(self):
        game = self.session(50761413)
        maze = self.add(game.engine, "Maze of Shadows")
        horse = self.add(game.engine, "Ebony Horse")
        scout = self.add(game.engine, "Elvish Scout")
        scout.acquired_control_turn_count = -1
        shadow = self.add(game.engine, "Soltari Foot Soldier", seat="B")
        ordinary = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        game.state.players["A"].mana_pool.update({"C": 2, "G": 1})
        for card in (maze, horse, scout, shadow, bounce):
            self.assert_closed(game, card)
        self.attack_with(game, shadow, ordinary)
        offer = self.targeted_offer(game, maze)
        self.assertEqual([shadow.ref], offer["target_schema"]["legal_refs"])
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"].startswith(("activate:" + horse.ref + ":", "activate:" + scout.ref + ":"))
            for row in actions))
        self.assertTrue(any(row["id"].startswith("activate:" + maze.ref + ":") and not row.get("target_schema")
            for row in actions))
        before = authoritative_state_hash(game.state)
        wrong = game.act("pilot:A", {"action_id": offer["id"], "targets": [ordinary.ref], "pay": "auto"})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": offer["id"], "targets": [shadow.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(game, "B", bounce, [shadow.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[shadow.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[maze.object_id].tapped)
        self.assertFalse(game.state.damage_prevention_shields)
        self.replay(game, load=True)

    def test_actual_shadow_maze_colorless_sibling_is_immediate_and_separate(self):
        game = self.session(50761414)
        maze = self.add(game.engine, "Maze of Shadows")
        self.assert_closed(game, maze)
        self.prepare(game)
        self.checkpoint(game)
        ability, = [a for a in game.engine._activated_abilities(maze) if a.mana_ability]
        action_id = f"activate:{maze.ref}:{ability.ability_id}"
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(row["id"] == action_id for row in actions))
        paid = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool.get("C"))
        self.assertFalse(game.state.stack)
        self.assertFalse(game.state.damage_prevention_shields)
        self.replay(game, load=True)

    def test_actual_ith_suspends_with_four_time_counters_as_a_separate_action(self):
        game = self.session(50761415)
        ith = self.add(game.engine, "Ith, High Arcanist", zone="hand")
        self.assert_closed(game, ith)
        self.prepare(game, mana={"W": 1, "U": 1})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith("suspend:" + ith.ref + ":")]
        paid = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        suspended = game.state.cards[ith.object_id]
        self.assertEqual(("exile", 4, False),
            (suspended.zone, suspended.counters.get("time"), suspended.face_down))
        self.assertFalse(game.state.stack)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_omitting_paired_ownership_blocks_real_original_registration(self):
        with patch("quorune.rules.prevention_component_shapes.paired_target_combat_prevention_capabilities", return_value=()):
            ready, _ = register_card(self.db, self.registry, "Maze of Ith")
        self.assertFalse(ready["strict_capability_ready"], ready)
        self.assertTrue(any("Ordered programs require every complete child owner" in item
            for item in ready["blockers"]), ready["blockers"])

    def test_verified_capability_withdrawals_require_both_actual_components(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        evidence = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        for capability in (UNTAP, PREVENTION):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Ordered combat result requirement withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=evidence, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in item for item in closure.blockers))
                for name in ORIGINALS:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertFalse(ready["strict_capability_ready"], (name, ready["blockers"]))
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])

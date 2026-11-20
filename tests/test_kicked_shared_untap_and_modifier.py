from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune import tap_state
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Gift of Growth"
KICKED_TARGET = "resolution.effect.fixed_same_target_self_replacement"
UNTAP = "permanent.untap.effect"
FIXTURES = ROOT / "tests" / "fixtures"


class KickedSharedUntapAndModifierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "kicked-additive-target-lifelink-cards.json": {"Vampire's Bite"},
        }
        parts = [FIXTURES / "kicked-shared-untap-and-modifier-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        database = folder / "kicked-untap-and-modifier.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Kicked untap and stat change", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "resolve")
        return card, program

    def test_original_binds_both_owned_ordered_branches_and_one_target(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(any("casting.kicker.fixed_mana" in node.capability_dependencies for node in ir.faces[0].nodes))
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
            thaw_value(program.target_schema))
        wrapper, = thaw_value(program.effects)
        self.assertEqual((5, "$context.kicked"), (wrapper["schema_version"], wrapper["cast_fact"]))
        self.assertTrue({KICKED_TARGET, UNTAP, "resolution.effect_program.closed_components",
                         "continuous.resolution.fixed_characteristics_until_end_of_turn"}
            <= set(program.capability_dependencies))
        untap = {"op": "untap", "card": "$target.0"}
        for branch, amount in ((wrapper["otherwise_effects"], 2), (wrapper["effects"], 4)):
            self.assertEqual([untap, {"op": "modify_stats_until_end_of_turn", "card": "$target.0",
                                      "power": amount, "toughness": amount}], branch)

    def test_raw_branches_require_one_shared_untap_before_each_fixed_stat_result(self):
        _, program = self.original_program()
        wrapper, = thaw_value(program.effects)
        untap, modifier = wrapper["effects"]
        for changed in (
            {**wrapper, "effects": [modifier, untap]},
            {**wrapper, "effects": [modifier]},
            {**wrapper, "otherwise_effects": wrapper["otherwise_effects"][1:]},
            {**wrapper, "effects": [untap, {**modifier, "card": "$target.1"}]},
            {**wrapper, "effects": [{**untap, "card": "$target.1"}, modifier]},
            {**wrapper, "mechanic_ids": [value for value in wrapper["mechanic_ids"]
                                       if value not in {"closed-effect-program", "bound-effect-program"}]},
        ):
            with self.subTest(changed=changed):
                bound = bind_semantic_program_runtime(replace(program, effects=[changed]),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound["blockers"])

    def prepare_cast(self, game, spell, target, *, kicked):
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        self.assert_closed(game, spell)
        action = self.action(game, "A", spell)
        options = {item["id"]: item for item in action["cost_options"]}
        groups = options["normal"]["target_schema"]["groups"]
        self.assertEqual(groups, options["kicked"]["target_schema"]["groups"])
        group, = groups
        self.assertEqual((1, 1), (group["min"], group["max"]))
        self.assertIn(target.ref, group["legal_refs"])
        return {"action_id": action["id"], "cost_option": "kicked" if kicked else "normal",
                "targets": [target.ref], "pay": "auto"}

    def test_real_each_payment_untaps_before_its_own_stat_change_and_replays(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                game = self.session(76156010 + kicked)
                spell = self.add(game.engine, NAME, zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B", ref="gift-untap-recipient")
                target.tapped = True
                other = self.add(game.engine, "Grizzly Bears", seat="C", ref="gift-untap-other")
                other.tapped = True
                private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="gift-private-hand")
                command = self.prepare_cast(game, spell, target, kicked=kicked)
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:C", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                paid = game.act("pilot:A", command)
                self.assertTrue(paid.ok, paid.summary)
                self.assertIs(game.state.stack[-1].context["kicked"], kicked)
                self.assertEqual(0 if kicked else 2, sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                original = tap_state.set_permanent_tapped
                order = []

                def observe(host, object_ref, **kwargs):
                    if object_ref == target.ref and kwargs["tapped"] is False:
                        order.append((game.state.cards[target.object_id].tapped,
                            game.engine._numeric_stat(target.object_id, "power"),
                            game.engine._numeric_stat(target.object_id, "toughness")))
                    return original(host, object_ref, **kwargs)

                with patch.object(tap_state, "set_permanent_tapped", side_effect=observe):
                    self.assertIsNone(self.resolve(game))
                self.assertEqual([(True, 2, 2)], order)
                self.assertFalse(game.state.cards[target.object_id].tapped)
                final = 6 if kicked else 4
                self.assertEqual((final, final), tuple(game.engine._numeric_stat(target.object_id, stat)
                    for stat in ("power", "toughness")))
                self.assertTrue(game.state.cards[other.object_id].tapped)
                self.assertEqual(2, game.engine._numeric_stat(other.object_id, "power"))
                for viewer in "ABC":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
                self.replay(game, load=True)

    def test_actual_already_untapped_creature_still_gets_one_stat_branch(self):
        game = self.session(76156012)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assertFalse(target.tapped)
        command = self.prepare_cast(game, spell, target, kicked=True)
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((6, 6), tuple(game.engine._numeric_stat(target.object_id, stat)
            for stat in ("power", "toughness")))
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.until(game, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main")
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(target.object_id, stat)
            for stat in ("power", "toughness")))
        self.replay(game, load=True)

    def test_actual_departed_target_cancels_ordered_pair_and_retains_payment(self):
        game = self.session(76156013)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        target.tapped = True
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, bounce)
        paid = game.act("pilot:A", self.prepare_cast(game, spell, target, kicked=True))
        self.assertTrue(paid.ok, paid.summary)
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        with patch.object(tap_state, "set_permanent_tapped", wraps=tap_state.set_permanent_tapped) as untap:
            self.assertIsNone(self.resolve(game))
        self.assertFalse(any(call.args[1] == target.ref and not call.kwargs["tapped"] for call in untap.call_args_list))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_omitting_the_exact_untap_producer_keeps_only_the_original_residual(self):
        with patch("quorune.compiler.kicked_self_replacement_templates._kicked_untap_stat_replacement",
                   return_value=None):
            original, _ = register_card(self.db, self.registry, NAME)
            other, _ = register_card(self.db, self.registry, "Vampire's Bite")
        self.assertFalse(original["strict_capability_ready"], original)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawal_requires_the_untap_and_shared_kicker_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (UNTAP, KICKED_TARGET):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Ordered untap prerequisite withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, NAME)
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])

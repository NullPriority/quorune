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


PROFILE = "commander_review"
NAME = "Vampire's Bite"
KICKED_TARGET = "resolution.effect.fixed_same_target_self_replacement"
LIFELINK = "damage.result.lifelink"
FIXTURES = ROOT / "tests" / "fixtures"


class KickedAdditiveTargetLifelinkTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        parts = [FIXTURES / "kicked-additive-target-lifelink-cards.json"]
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "kicked-self-replacement-cards.json": {"Reverberate", "Field Research"},
            "ability-source-counter-cards.json": {"Counterspell"},
        }
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data["rulings"] if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "kicked-additive-lifelink.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Kicked same-target lifelink", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "resolve")
        return card, program

    def test_original_has_one_declared_target_and_both_branches_keep_the_mandatory_stats(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(any("casting.kicker.fixed_mana" in node.capability_dependencies for node in ir.faces[0].nodes))
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
            thaw_value(program.target_schema))
        wrapper, = thaw_value(program.effects)
        self.assertEqual((5, {"schema_version": 1, "kind": "kicked_cast"}, "$context.kicked"),
            (wrapper["schema_version"], wrapper["condition"], wrapper["cast_fact"]))
        stats = {"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": 3, "toughness": 0}
        grant = {"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": "Lifelink"}
        self.assertEqual([stats], wrapper["otherwise_effects"])
        self.assertEqual([stats, grant], wrapper["effects"])
        self.assertTrue({KICKED_TARGET, LIFELINK, "target.revalidate_resolution"} <= set(program.capability_dependencies))

    def test_fixed_branch_cannot_reorder_move_its_target_or_drop_the_original_cast_fact(self):
        card, program = self.original_program()
        wrapper, = thaw_value(program.effects)
        first, second = wrapper["effects"]
        for changed in (
            {**wrapper, "effects": [second, first]},
            {**wrapper, "effects": [first, {**second, "card": "$target.1"}]},
            {**wrapper, "cast_fact": "$context.spent_other"},
            {**wrapper, "otherwise_effects": []},
        ):
            with self.subTest(changed=changed):
                bound = bind_semantic_program_runtime(replace(program, effects=[changed]),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound["blockers"])
        unsupported = replace(card, oracle_text=card.oracle_text.replace("gains lifelink", "gains trample"))
        ir = compile_oracle_card(unsupported, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def proposed(self, game, spell, target, *, kicked):
        action = self.action(game, "A", spell)
        options = {row["id"]: row for row in action["cost_options"]}
        groups = options["normal"]["target_schema"]["groups"]
        self.assertEqual(groups, options["kicked"]["target_schema"]["groups"])
        group, = groups
        self.assertEqual((1, 1), (group["min"], group["max"]))
        self.assertIn(target.ref, group["legal_refs"])
        option = "kicked" if kicked else "normal"
        return {"action_id": action["id"], "cost_option": option, "pay": "auto", "targets": [target.ref]}

    def test_real_paid_and_unpaid_casts_apply_same_stats_and_only_kicked_combat_gains_life(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                game = self.session(76155910 + kicked)
                spell = self.add(game.engine, NAME, zone="hand")
                recipient = self.add(game.engine, "Grizzly Bears", seat="B")
                recipient.acquired_control_turn_count = -1
                private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="kicked-private-hand")
                self.assert_closed(game, spell)
                self.prepare(game, active="B", actor="A", mana={"B": 2, "C": 2})
                self.checkpoint(game)
                command = self.proposed(game, spell, recipient, kicked=kicked)
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:C", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIs(game.state.stack[-1].context["kicked"], kicked)
                self.assertEqual(0 if kicked else 3, sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((5, 2), tuple(game.engine._numeric_stat(recipient.object_id, kind)
                    for kind in ("power", "toughness")))
                self.assertEqual(kicked, "lifelink" in game.engine._combat_keywords(game.state.cards[recipient.object_id]))
                self.until(game, lambda s: s.state.pending_decision is not None
                           and s.state.pending_decision.kind == "combat.attackers")
                attack = game.act("pilot:B", {"action_id": "attack", "attackers": {recipient.ref: "C"}})
                self.assertTrue(attack.ok, attack.summary)
                self.until(game, lambda s: s.state.phase == "postcombat_main")
                self.assertEqual((40, 45 if kicked else 40, 35),
                    tuple(game.state.players[seat].life for seat in "ABC"))
                for viewer in "ABC":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
                self.replay(game, load=True)

    def test_actual_copy_keeps_paid_lifelink_with_the_original_countered(self):
        game = self.session(76155912)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        copy_spell = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        for card in (spell, copy_spell, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, "C": 2})
        game.state.players["B"].mana_pool["R"] = 2
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        paid = game.act("pilot:A", self.proposed(game, spell, target, kicked=True))
        self.assertTrue(paid.ok, paid.summary)
        original_ref = game.state.stack[-1].ref
        self.cast(game, "B", copy_spell, [original_ref])
        self.until(game, lambda s: (s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "semantic.choice")
                   or any(item.kind == "spell_copy" for item in s.state.stack))
        if game.state.pending_decision is not None and game.state.pending_decision.kind == "semantic.choice":
            keep = game.act("pilot:B", {"action_id": "choose", "targets": [target.ref]})
            self.assertTrue(keep.ok, keep.summary)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertIs(copied.context["kicked"], True)
        self.assertEqual(("B", [target.ref]), (copied.controller, copied.targets))
        self.cast(game, "C", counter, [original_ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.engine._numeric_stat(target.object_id, "power"))
        self.assertIn("lifelink", game.engine._combat_keywords(game.state.cards[target.object_id]))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(3, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_lost_announced_target_cancels_the_paid_branch_without_refund(self):
        game = self.session(76155913)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-unselected-bear")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, "C": 2})
        self.checkpoint(game)
        paid = game.act("pilot:A", self.proposed(game, spell, target, kicked=True))
        self.assertTrue(paid.ok, paid.summary)
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual(2, game.engine._numeric_stat(other.object_id, "power"))
        self.assertNotIn("lifelink", game.engine._combat_keywords(game.state.cards[other.object_id]))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_omitting_the_additive_producer_restores_only_the_original_residual(self):
        with patch("quorune.compiler.kicked_self_replacement_templates.kicked_additive_target_lifelink_template",
                   return_value=None):
            original, _ = register_card(self.db, self.registry, NAME)
            other, _ = register_card(self.db, self.registry, "Field Research")
        self.assertFalse(original["strict_capability_ready"], original)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_shared_target_branch_and_lifelink_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (KICKED_TARGET, LIFELINK):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Kicked additive target prerequisite withdrawn"])
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

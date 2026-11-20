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
from quorune.next_spell_cost_model import CAPABILITY, ResolvedNextSpellCostReduction, recurring_spell_cost_query
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
SOPRANO = "Spellbinding Soprano"
MASKMAKER = "Goblin Maskmaker"
ATTACK = "trigger.event.normalized_self_attack"
FIXTURES = ROOT / "tests" / "fixtures"


class RepeatedSourceAttackCastCostReductionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Unsummon", "Island", "Murder"},
            "activation-public-state-cards.json": {"Wastes"},
            "flashback-cards.json": {"Think Twice"},
            "additional-conditional-modifier-cards.json": {"Divination"},
            "activation-view-equivalence-cards.json": {"Manalith"},
            "qualified-attack-cards.json": {"Daru Lancer"},
        }
        parts = [FIXTURES / "repeated-source-attack-cast-cost-reduction-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "repeated-attack-cost.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Repeated attack cast costs", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in programs if program.event == "creature.attacks")
        return card, trigger, programs

    def test_complete_originals_keep_exact_spell_scopes_and_independent_encore(self):
        for name, face_down_only in ((SOPRANO, False), (MASKMAKER, True)):
            with self.subTest(name=name):
                card, program, siblings = self.original_program(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual("battlefield", program.active_zone)
                self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, program.event_condition)
                self.assertIsNone(program.target_schema)
                self.assertTrue({CAPABILITY, ATTACK, "trigger.placement.apnap"} <= set(program.capability_dependencies))
                (effect,) = thaw_value(program.effects)
                self.assertEqual((2, 1, face_down_only),
                    (effect["schema_version"], effect["amount"], effect["face_down_only"]))
                self.assertEqual(recurring_spell_cost_query(face_down_only).to_dict(), effect["spell_query"])
                if name == SOPRANO:
                    encore, = (other for other in siblings if other.event == "activate")
                    self.assertEqual("graveyard", encore.active_zone)
                    self.assertEqual("encore_tokens", encore.effects[0]["op"])

    def test_raw_repeated_rule_requires_exact_source_event_and_announced_scope(self):
        _, program, _ = self.original_program(MASKMAKER)
        (effect,) = thaw_value(program.effects)
        for changes in (
            {"event": "creature.blocks"},
            {"event_condition": None},
            {"active_zone": "graveyard"},
            {"effects": [{**effect, "face_down_only": 1}]},
            {"effects": [{**effect, "face_down_only": False}]},
            {"effects": [{**effect, "amount": 2}]},
            {"effects": [{**effect, "schema_version": 1}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        ):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result["blockers"])

    def test_versioned_journal_keeps_legacy_fields_and_rejects_changed_recurring_operands(self):
        legacy = ResolvedNextSpellCostReduction("next-rule", "scope-source", 1, "A",
            recurring_spell_cost_query(False), 1)
        repeated = replace(legacy, effect_id="turn-rule", schema_version=2)
        old = legacy.to_dict()
        new = repeated.to_dict()
        self.assertNotIn("face_down_only", old)
        self.assertIs(new["face_down_only"], False)
        self.assertEqual(old, ResolvedNextSpellCostReduction.from_dict(old).to_dict())
        self.assertEqual(new, ResolvedNextSpellCostReduction.from_dict(new).to_dict())
        for malformed in ({**old, "face_down_only": False}, {**new, "face_down_only": 0},
                          {**new, "spell_query": {"types_any": ["instant", "sorcery"]}},
                          {**new, "face_down_only": True}, {**new, "amount": 2}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                ResolvedNextSpellCostReduction.from_dict(malformed)

    def cost_rules(self, game):
        return [effect for effect in game.state.continuous_effects
                if isinstance(effect, ResolvedNextSpellCostReduction)]

    def action_ids(self, game):
        self.priority(game, "A")
        return {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}

    def test_actual_soprano_keeps_discount_through_two_spells_and_source_departure(self):
        game = self.session(76156110)
        source = self.add(game.engine, SOPRANO)
        source.acquired_control_turn_count = -1
        instant = self.add(game.engine, "Think Twice", zone="hand")
        sorcery = self.add(game.engine, "Divination", zone="hand")
        artifact = self.add(game.engine, "Manalith", zone="hand")
        lands = [self.add(game.engine, "Island", ref=f"soprano-island-{i}") for i in range(2)]
        wastes = self.add(game.engine, "Wastes")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="soprano-response-island")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="soprano-private-hand")
        for card in (source, instant, sorcery, artifact, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        rule, = self.cost_rules(game)
        self.assertEqual((2, False, "A"), (rule.schema_version, rule.face_down_only, rule.controller))

        action = self.action(game, "A", instant)
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, sum(game.state.cards[land.object_id].tapped for land in lands))
        self.assertFalse(game.state.cards[wastes.object_id].tapped)
        self.assertEqual([rule.effect_id], [effect.effect_id for effect in self.cost_rules(game)])
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        self.assertNotIn("cast:" + artifact.ref, self.action_ids(game))
        self.cast(game, "A", sorcery)
        self.assertTrue(game.state.cards[wastes.object_id].tapped)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual([rule.effect_id], [effect.effect_id for effect in self.cost_rules(game)])
        self.assertIsNone(self.resolve(game))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_maskmaker_reduces_two_face_down_casts_with_hidden_identities_until_cleanup(self):
        game = self.session(76156111)
        source = self.add(game.engine, MASKMAKER)
        source.acquired_control_turn_count = -1
        first = self.add(game.engine, "Daru Lancer", zone="hand", ref="first-masked-hidden-name")
        second = self.add(game.engine, "Glacial Stalker", zone="hand", ref="second-masked-hidden-name")
        artifact = self.add(game.engine, "Manalith", zone="hand")
        lands = [self.add(game.engine, "Wastes", ref=f"maskmaker-wastes-{i}") for i in range(4)]
        for card in (source, first, second, artifact):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        rule, = self.cost_rules(game)
        self.assertEqual((2, True), (rule.schema_version, rule.face_down_only))
        for count, card in enumerate((first, second), start=1):
            action = "cast-morph:" + card.ref
            self.assertIn(action, self.action_ids(game))
            accepted = game.act("pilot:A", {"action_id": action, "pay": "auto"})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertEqual(count * 2, sum(game.state.cards[land.object_id].tapped for land in lands))
            self.assertEqual([rule.effect_id], [effect.effect_id for effect in self.cost_rules(game)])
            self.assertIsNone(self.resolve(game))
            current = game.state.cards[card.object_id]
            self.assertTrue(current.face_down)
            for viewer in "BCD":
                packet = json.dumps(game.packet("pilot:" + viewer, full=True))
                self.assertNotIn(card.printed_name, packet)
            if count == 1:
                self.assertNotIn("cast:" + artifact.ref, self.action_ids(game))
                game = self.replay(game, load=True)
        def next_main(session):
            return session.state.active_player == "B" and session.state.phase == "precombat_main"

        self.until(game, lambda s: next_main(s) or (s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "cleanup.discard"))
        if not next_main(game):
            cleanup = game.packet("pilot:A", full=True)["decision"]["ctx"]
            discarded = game.act("pilot:A", {"a": "discard",
                "cards": [row["id"] for row in cleanup["hand"][:cleanup["count"]]]})
            self.assertTrue(discarded.ok, discarded.summary)
            self.until(game, next_main)
        self.assertEqual([], self.cost_rules(game))
        self.replay(game, load=True)

    def test_omitting_exact_recurring_producer_restores_both_complete_original_residuals(self):
        with patch("quorune.compiler.next_spell_cost_templates.recurring_attack_spell_cost_template", return_value=None):
            for name in (SOPRANO, MASKMAKER):
                original, _ = register_card(self.db, self.registry, name)
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
            other, _ = register_card(self.db, self.registry, "Think Twice")
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_source_attack_and_cast_cost_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (ATTACK, CAPABILITY):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Recurring cost prerequisite withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in (SOPRANO, MASKMAKER):
                    binding, _ = register_card(self.db, registry, name)
                    self.assertFalse(binding["strict_capability_ready"], binding["blockers"])
                other, _ = register_card(self.db, registry, "Think Twice")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])

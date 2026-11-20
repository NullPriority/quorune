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
from quorune.temporary_cast_rule_model import CAPABILITY, OPERATION, ResolvedCastRestriction
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Xantid Swarm"
ATTACK = "trigger.event.normalized_self_attack"
FIXTURES = ROOT / "tests" / "fixtures"


class AttackDefenderCastProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "activation-restriction-material-cards.json": {"Lightning Bolt", "Forest"},
            "activation-result-views-cards.json": {"Mountain"},
            "compiled-reviewed-trigger-cards.json": {"Ambush Viper"},
            "targeted-controller-untap-cards.json": {"Ajani Vengeant"},
            "temporary-cast-restriction-cards.json": {"Silence"},
        }
        parts = [FIXTURES / "attack-defender-cast-prohibition-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "attacked-defender-cast.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defending-player casting restriction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_trigger(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "creature.attacks")
        return card, trigger

    def test_complete_original_keeps_flying_and_one_untargeted_attack_recipient(self):
        card, trigger = self.original_trigger()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        keyword, = (node for node in ir.faces[0].nodes if node.kind == "keyword_ability")
        self.assertIn("combat.block.flying", keyword.capability_dependencies)
        self.assertEqual("battlefield", trigger.active_zone)
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, trigger.event_condition)
        self.assertIsNone(trigger.target_schema)
        self.assertTrue({ATTACK, CAPABILITY, "trigger.placement.apnap"} <= set(trigger.capability_dependencies))
        self.assertEqual([{"op": OPERATION, "actor": "$controller", "player": "$context.defending_player",
                           "restriction": {"schema_version": 1, "player_scope": "target", "spell_kind": "any"}}],
                         thaw_value(trigger.effects))

    def test_raw_defender_prohibition_requires_exact_untargeted_source_attack(self):
        _, trigger = self.original_trigger()
        effect, = thaw_value(trigger.effects)
        for changes in (
            {"event": "creature.blocks"},
            {"active_zone": "graveyard"},
            {"event_condition": None},
            {"effects": [{**effect, "player": "$context.player"}]},
            {"effects": [{**effect, "restriction": {**effect["restriction"], "spell_kind": "noncreature"}}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}},
        ):
            with self.subTest(changes=changes):
                binding = bind_semantic_program_runtime(replace(trigger, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)

    def offers(self, game, player):
        self.priority(game, player)
        return game.packet("pilot:" + player, full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_actual_attack_locks_only_its_planeswalker_defender_after_response_until_cleanup(self):
        game = self.session(76155510)
        source = self.add(game.engine, NAME)
        other = self.add(game.engine, "Grizzly Bears", ref="separate-attacker")
        for attacker in (source, other):
            attacker.acquired_control_turn_count = -1
        walker = self.add(game.engine, "Ajani Vengeant", seat="B")
        walker.counters["loyalty"] = 4
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        red = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="locked-defender-instant")
        flash = self.add(game.engine, "Ambush Viper", seat="B", zone="hand", ref="locked-defender-creature")
        unaffected = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand", ref="unlocked-defender-instant")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="unrelated-private-spell")
        self.add(game.engine, "Island", seat="B")
        mountain = self.add(game.engine, "Mountain", seat="B", ref="locked-defender-mountain")
        self.add(game.engine, "Forest", seat="B")
        self.add(game.engine, "Generic Bound Plains", seat="B")
        self.add(game.engine, "Mountain", seat="C", ref="unlocked-defender-mountain")
        for card in (source, walker, bounce, red, flash, unaffected):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, walker.ref), (other, "C")])
        trigger, = game.state.stack
        self.assertEqual((source.object_id, "B", []),
            (trigger.source_object_id, trigger.context["defending_player"], trigger.targets))
        before = {row["id"] for row in self.offers(game, "B")}
        ids = {"cast:" + card.ref for card in (red, flash)}
        self.assertTrue(ids <= before, before)

        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        restriction, = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedCastRestriction)]
        self.assertEqual(("A", "B", "any"), (restriction.controller, restriction.target_player,
                                           restriction.restriction.spell_kind))
        after = self.offers(game, "B")
        self.assertFalse(ids.intersection(row["id"] for row in after))
        mana = next(row for row in after if row["id"].startswith("activate:" + mountain.ref + ":"))
        self.assertTrue(game.act("pilot:B", {"action_id": mana["id"], "pay": "auto"}).ok)
        self.assertGreaterEqual(game.state.players["B"].mana_pool.get("R", 0), 1)
        self.priority(game, "B")
        locked = authoritative_state_hash(game.state)
        for card, targets in ((red, ["A"]), (flash, [])):
            result = game.act("pilot:B", {"action_id": "cast:" + card.ref, "pay": "auto", "targets": targets})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(locked, authoritative_state_hash(game.state))

        life = game.state.players["A"].life
        self.cast(game, "C", unaffected, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 3, game.state.players["A"].life)
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.until(game, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "cleanup.discard")
        cleanup = game.packet("pilot:A", full=True)["decision"]["ctx"]
        discarded = game.act("pilot:A", {"a": "discard",
            "cards": [row["id"] for row in cleanup["hand"][:cleanup["count"]]]})
        self.assertTrue(discarded.ok, discarded.summary)
        self.until(game, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main")
        self.assertFalse(any(isinstance(effect, ResolvedCastRestriction) for effect in game.state.continuous_effects))
        self.assertTrue(ids <= {row["id"] for row in self.offers(game, "B")})
        self.replay(game, load=True)

    def test_actual_defender_concession_leaves_no_restriction_on_surviving_seats(self):
        game = self.session(76155511)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        spell = self.add(game.engine, "Lightning Bolt", seat="D", zone="hand")
        self.add(game.engine, "Mountain", seat="D")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "C")])
        self.priority(game, "C")
        result = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn("C", game.engine.active_seats)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(isinstance(effect, ResolvedCastRestriction) for effect in game.state.continuous_effects))
        life = game.state.players["A"].life
        self.cast(game, "D", spell, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 3, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_omitting_the_sealed_defender_producer_blocks_the_original(self):
        with patch("quorune.compiler.temporary_cast_restrictions.attacked_defender_cast_restriction", return_value=None):
            original, _ = register_card(self.db, self.registry, NAME)
            ordinary, _ = register_card(self.db, self.registry, "Silence")
        self.assertFalse(original["strict_capability_ready"], original)
        self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])

    def test_verified_owner_withdrawals_block_the_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (CAPABILITY, ATTACK):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Attacked-defender result owner withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, NAME)
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
                ordinary, _ = register_card(self.db, registry, "Silence")
                self.assertEqual(cap != CAPABILITY, ordinary["strict_capability_ready"], ordinary["blockers"])

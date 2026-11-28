from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict, canonical_ability_fragments
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.haste import has_effective_haste
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.riot import GrantedRiotSpec, RIOT_ENTRY_HANDLER_ID, RIOT_GRANTED_COMPONENT_PREFIX
from quorune.riot import RIOT_GRANT_CAPABILITY, RIOT_GRANT_HANDLER_ID
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence_storage import read_evidence_document
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class RhythmOfTheWildTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "rhythm.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ripple-cards.json",
            fixtures / "next-cast-counter-protection-cards.json",
            fixtures / "attached-counter-prohibition-cards.json",
            fixtures / "announced-populate-cards.json",
            fixtures / "static-land-animation-cards.json",
            fixtures / "upkeep-creature-token-absence-cards.json",
            fixtures / "event-player-sacrifice-cards.json",
            fixtures / "combat-damage-entry-history-cards.json",
            fixtures / "countered-ability-source-loss-cards.json",
            fixtures / "rhythm-of-the-wild-cards.json",
            fixtures / "public-exile-followup-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Rhythm witnesses", [
            DeckEntry("Sliver Hivelord", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def _options(self, session, seat="A"):
        packet = session.packet("pilot:" + seat, full=True)
        self.assertEqual("replacement.order", packet["decision"]["kind"])
        return [row["id"] for row in packet["decision"]["ctx"]["options"]]

    def _choose(self, session, effect, seat="A"):
        result = session.act("pilot:" + seat, {"action_id": "choose", "choices": {"replacement": effect}})
        self.assertTrue(result.ok, result.summary)

    def _grants(self, session, card):
        effective = session.engine._effective_card_data(session.state.cards[card.object_id])
        return [f for f in canonical_ability_fragments(effective.get("ability_fragments", ()))
                if isinstance(f, GrantedRiotSpec)]

    def test_actual_rhythm_protects_creature_and_stacks_two_grants_with_intrinsic_riot(self):
        session = self.session(702136701)
        self.add(session.engine, "Rhythm of the Wild", ref="rhythm-first")
        rhythm = self.add(session.engine, "Rhythm of the Wild", zone="hand", ref="rhythm-second")
        goblin = self.add(session.engine, "Zhur-Taa Goblin", zone="hand")
        dismiss = self.add(session.engine, "Dismiss", seat="B", zone="hand")
        draw = self.add(session.engine, "Forest", seat="B", zone="library", ref="rhythm-private-dismiss-draw")
        self.add(session.engine, "Mountain", ref="rhythm-paid-mountain")
        self.add(session.engine, "Forest", ref="rhythm-paid-forest")
        for card in (rhythm, goblin, dismiss):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "R": 1, "G": 1})
        session.state.players["B"].mana_pool.update({"C": 2, "U": 2})
        self.checkpoint(session)

        self.cast_for(session, "A", rhythm)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", goblin)
        spell = next(item for item in session.state.stack if item.card_object_id == goblin.object_id)
        self.cast_for(session, "B", dismiss, [spell.ref])
        self.resolve_top(session)
        self.assertEqual("stack", session.state.cards[goblin.object_id].zone)
        self.assertEqual("hand", session.state.cards[draw.object_id].zone)
        for viewer in "ACD":
            self.assertNotIn(draw.ref, json.dumps(session.packet("pilot:" + viewer, full=True)))

        self.assertEqual("replacement.order", self.resolve(session).kind)
        options = self._options(session)
        counter_options = [value for value in options if not value.startswith("decline:")]
        self.assertEqual(3, len(counter_options))
        granted = [value for value in counter_options if RIOT_GRANTED_COMPONENT_PREFIX in value]
        intrinsic, = [value for value in counter_options if value not in granted]
        self.assertEqual(2, len(granted))
        self.assertTrue(intrinsic.startswith(f"{RIOT_ENTRY_HANDLER_ID}:"))
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": "choose", "choices": {"replacement": granted[0]}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self._choose(session, granted[0])
        self.assertEqual("stack", session.state.cards[goblin.object_id].zone)
        session = self.replay(session, load=True)
        self._choose(session, intrinsic)
        self._choose(session, f"decline:{granted[1]}")
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[goblin.object_id]
        self.assertEqual(("battlefield", 2), (entered.zone, entered.counters.get("+1/+1")))
        self.assertTrue(has_effective_haste(session.engine, entered))
        self.assertEqual(2, len({fragment.instance_id for fragment in self._grants(session, goblin)}))
        self.replay(session, load=True)

    def test_actual_rhythm_haste_when_counters_forbidden_excludes_tokens_and_opposing_creatures(self):
        session = self.session(702136702)
        rhythm = self.add(session.engine, "Rhythm of the Wild")
        protection = self.add(session.engine, "Solemnity", seat="B")
        creature = self.add(session.engine, "Grizzly Bears", zone="hand", ref="rhythm-own-bears")
        enemy = self.add(session.engine, "Ambush Viper", seat="B", zone="hand", ref="rhythm-rival-viper")
        tokens = self.add(session.engine, "Dragon Fodder", zone="hand")
        removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.add(session.engine, "Mountain", ref="rhythm-fodder-mountain")
        self.add(session.engine, "Plains", seat="B", ref="rhythm-removal-plains")
        self.add(session.engine, "Wastes", seat="B", ref="rhythm-removal-wastes")
        for card in (rhythm, protection, creature, tokens, removal):
            self.assert_closed(session, card)
        self.prepare(session, mana={"G": 1, "C": 2})
        session.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(session)

        self.cast_for(session, "A", creature)
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[creature.object_id]
        self.assertTrue(has_effective_haste(session.engine, entered))
        self.assertEqual(0, entered.counters.get("+1/+1", 0))
        self.assertEqual(1, len(self._grants(session, creature)))
        self.cast_for(session, "B", enemy)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self._grants(session, enemy))
        self.assertFalse(has_effective_haste(session.engine, session.state.cards[enemy.object_id]))
        self.cast_for(session, "A", tokens)
        self.assertIsNone(self.resolve(session))
        created = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(created))
        self.assertTrue(all(not self._grants(session, token) for token in created))
        self.assertTrue(all(not has_effective_haste(session.engine, token) for token in created))
        session = self.replay(session, load=True)
        self.cast_for(session, "B", removal, [rhythm.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[rhythm.object_id].zone)
        self.assertFalse(self._grants(session, creature))
        self.assertTrue(has_effective_haste(session.engine, session.state.cards[creature.object_id]))
        self.replay(session, load=True)

    def test_actual_sculpting_steel_gets_riot_only_after_its_creature_copy_is_chosen(self):
        session = self.session(702136703)
        self.add(session.engine, "Rhythm of the Wild")
        automaton = self.add(session.engine, "Universal Automaton", seat="B")
        steel = self.add(session.engine, "Sculpting Steel", zone="hand")
        thassa = self.add(session.engine, "Thassa, God of the Sea", zone="hand")
        self.add(session.engine, "Island", ref="rhythm-thassa-island")
        for index in range(2):
            self.add(session.engine, "Wastes", ref=f"rhythm-thassa-generic-{index}")
        self.assert_closed(session, steel)
        self.assert_closed(session, thassa)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.cast_for(session, "A", steel)
        self.assertEqual("replacement.order", self.resolve(session).kind)
        initial = self._options(session)
        self.assertTrue(all(RIOT_GRANTED_COMPONENT_PREFIX not in value for value in initial))
        copy, = [value for value in initial if automaton.ref in value and not value.startswith("decline:")]
        self._choose(session, copy)
        options = self._options(session)
        granted, = [value for value in options if RIOT_GRANTED_COMPONENT_PREFIX in value
                    and not value.startswith("decline:")]
        self.assertEqual("stack", session.state.cards[steel.object_id].zone)
        session = self.replay(session, load=True)
        self._choose(session, granted)
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[steel.object_id]
        self.assertEqual(("battlefield", "A", 1), (entered.zone, entered.controller, entered.counters.get("+1/+1")))
        types, _, _ = session.engine._type_parts(session.engine._effective_card_data(entered)["type_line"])
        self.assertIn("creature", types)
        self.assertEqual(1, len(self._grants(session, steel)))

        self.cast_for(session, "A", thassa)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[thassa.object_id]
        self.assertEqual("battlefield", current.zone)
        types, _, _ = session.engine._type_parts(session.engine._effective_card_data(current)["type_line"])
        self.assertNotIn("creature", types)
        self.assertFalse(self._grants(session, thassa))
        self.assertEqual(0, current.counters.get("+1/+1", 0))
        self.replay(session, load=True)

    def test_rhythm_seals_the_original_counter_rule_and_exact_grant_program(self):
        record = self.db.lookup("Rhythm of the Wild", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        protection, riot = ir.faces[0].nodes
        self.assertEqual("rule.stack.uncounterable.fixed-query.v1", protection.handlers[0]["handler_id"])
        self.assertEqual(RIOT_GRANT_HANDLER_ID, riot.handlers[0]["handler_id"])
        self.assertIn(RIOT_GRANT_CAPABILITY, riot.capability_dependencies)
        marker = GrantedRiotSpec("grant-incarnation:component")
        self.assertEqual(marker, ability_fragment_from_dict(ability_fragment_to_dict(marker)))
        with self.assertRaises(ValueError):
            GrantedRiotSpec.from_dict({"schema_version": True, "instance_id": "grant-incarnation:component"})
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

        from quorune.semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot

        session = self.session(702136704)
        provider = self.add(session.engine, "Rhythm of the Wild")
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        self.prepare(session)
        before = authoritative_state_hash(session.state)
        snapshot = capture_zone_change_replacement_snapshot(
            session.engine, ((provider.object_id, "graveyard"), (entrant.object_id, "battlefield")),
            destination_controllers={entrant.object_id: "A"},
        )
        grant, = [effect for effect in snapshot.effects if RIOT_GRANTED_COMPONENT_PREFIX in effect.effect_id]
        self.assertEqual(entrant.ref, grant.source_id)
        self.assertIn(provider.logical_object_id, grant.effect_id)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_rhythm_compiler_omission_and_verified_grant_withdrawal_preserve_counter_clause(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index

        record = self.db.lookup("Rhythm of the Wild", fuzzy=False)
        with patch("quorune.compiler.runtime_templates._keyword_grant_runtime_template", return_value=None):
            omitted = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual(["static_ability"], [row.kind for row in omitted.material_residuals])
        self.assertTrue(omitted.faces[0].nodes[0].exact)
        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in source["capabilities"] if row["id"] == RIOT_GRANT_CAPABILITY).update(
            status="blocked", blockers=["test withdrawal of the live nontoken grant"],
        )
        registry = CapabilityRegistry(source)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        ids = {row["test_id"] for row in read_evidence_document(
            ROOT / "quorune/rules/capability-evidence.json")["declarations"]}
        index = build_index(registry_value=source, declaration_source=declarations, discovered_test_ids=ids)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        blocked = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertIn(f"capability:status:{RIOT_GRANT_CAPABILITY}:blocked",
                      [reason for resid in blocked.material_residuals for reason in resid.blockers])
        self.assertTrue(blocked.faces[0].nodes[0].exact)


    def test_actual_modal_land_face_does_not_get_creature_riot(self):
        game = self.session(702136704)
        rhythm = self.add(game.engine, "Rhythm of the Wild")
        modal = self.add(game.engine, "Kazandu Mammoth // Kazandu Valley", zone="hand")
        for card in (rhythm, modal):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        play, = [row for row in actions if row.get("card") == modal.ref and row.get("action") == "play_land"]
        result = game.act("pilot:A", {"action_id": play["id"], "face": "Kazandu Valley"})
        self.assertTrue(result.ok, result.summary)
        pending = game.state.pending_decision
        self.assertNotEqual("replacement.order", pending.kind if pending else None,
                            "The Valley is a land and should have no Riot choice")
        current = game.state.cards[modal.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual("Kazandu Valley", game.engine._effective_card_data(current)["name"])
        self.assertFalse(current.counters)
        self.assertFalse(has_effective_haste(game.engine, current))
        self.assertFalse(self._grants(game, current))
        self.replay(game, load=True)

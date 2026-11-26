"""Native faction choices and exact Brotherhood and Enclave outcomes."""

from dataclasses import replace
from hashlib import file_digest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import ENTRY_FACTION_CAPABILITY
from quorune.rules.faction_trigger_model import FACTION_DRAW_CAPABILITY, FACTION_RAD_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rad_counter_model import RAD_CAPABILITY, RAD_EFFECT_FIELD, RADIATION_CAUSE
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ORIGINAL = "Struggle for Project Purity"
PROFILE = "commander_review"


class FactionEntryTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "faction-entry.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "bound-effect-program-cards.json", fixtures / "faction-entry-trigger-cards.json"], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Faction choice",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    def choose_faction(self, game, card, faction):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        offered = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in packet["ctx"]["options"]}
        self.assertEqual({"Brotherhood", "Enclave"}, set(offered))
        self.assertEqual("stack", game.state.cards[card.object_id].zone)
        command = {"action_id": "choose", "choices": {"replacement": offered[faction]}}
        before = authoritative_state_hash(game.state)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "choices": {"replacement": "forged"}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertEqual(faction, game.state.cards[card.object_id].annotations.get("chosen_faction"))

    def advance_through_cleanup(self, game, predicate):
        def cleanup(current):
            decision = current.state.pending_decision
            return decision is not None and decision.kind == "cleanup.discard"

        while True:
            self.until(game, lambda current: predicate(current) or cleanup(current))
            if predicate(game):
                return
            principal = game.pending_principals()[0]
            decision = game.packet(principal, full=True)["decision"]
            count = decision["ctx"]["count"]
            cards = [row["id"] for row in decision["ctx"]["hand"][:count]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)

    def _draw_counts(self, game):
        return {seat: len(game.state.players[seat].draw_history) for seat in "ABCD"}

    def _trigger(self, game, source, event):
        return next((item for item in game.state.stack
                     if item.source_object_id == source.object_id and item.context.get("event") == event), None)

    def _replay_receipt(self, game, name):
        target = os.environ.get("QUORUNE_FACTION_RECORD_PARENT")
        if target is None:
            self.replay(game, load=True)
            return
        destination = Path(target) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        receipt = {"manifest_sha256": digest, "commands": len(game.commands),
                   "final_state_sha256": authoritative_state_hash(game.state)}
        destination.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def test_brotherhood_counts_actual_draws_across_replacement_and_resume(self):
        game = self.session(30460701)
        engine = game.engine
        source = self.add(engine, ORIGINAL, zone="hand")
        dredger = self.add(engine, "Life from the Loam", seat="B", zone="graveyard")
        reflections = [self.add(engine, "Thought Reflection", seat=seat, ref=f"faction-double-{seat}")
                       for seat in "CD"]
        for card in (source, *reflections):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.choose_faction(game, source, "Brotherhood")

        def upkeep_trigger(current):
            return (current.state.active_player == "A" and current.state.step == "upkeep"
                    and self._trigger(current, source, "step.begin") is not None)

        self.advance_through_cleanup(game, lambda current: upkeep_trigger(current) or (
            current.state.pending_decision is not None and current.state.pending_decision.kind == "draw.replacement"
        ))
        if not upkeep_trigger(game):
            self.assertEqual(["B"], game.state.pending_decision.actors)
            ordinary = game.act("pilot:B", {"action_id": "choose", "choice": "draw"})
            self.assertTrue(ordinary.ok, ordinary.summary)
            for seat in "CD":
                self.advance_through_cleanup(game, lambda current: current.state.active_player == seat)
            self.advance_through_cleanup(game, upkeep_trigger)
        self.assertEqual(1, self.trigger_count(game, source))
        item = self._trigger(game, source, "step.begin")
        self.assertEqual(("A", []), (item.controller, item.targets))
        before = self._draw_counts(game)
        pending = self.resolve(game)
        self.assertEqual(("draw.replacement", ["B"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        game = self.replay(game, load=True)
        result = game.act("pilot:B", {"action_id": "choose", "choice": dredger.ref})
        self.assertTrue(result.ok, result.summary)
        self.settle(game)

        self.assertEqual({"A": 4, "B": 0, "C": 2, "D": 2},
            {seat: self._draw_counts(game)[seat] - before[seat] for seat in "ABCD"})
        self.assertEqual("hand", game.state.cards[dredger.object_id].zone)
        hidden_ref = game.state.players["C"].draw_history[-1]["object"]
        self.assertNotIn(hidden_ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertEqual(0, game.state.players["B"].counters.get("rad", 0))
        self._replay_receipt(game, "faction-brotherhood-game-record")

    def test_enclave_uses_sealed_direct_attack_and_survives_source_departure(self):
        game = self.session(30460702)
        engine = game.engine
        source = self.add(engine, ORIGINAL, zone="hand")
        mirror = self.add(engine, "Mirage Mirror")
        attackers = [self.add(engine, "Grizzly Bears", seat="B", ref=f"faction-attacker-{index}")
                     for index in range(3)]
        remove_attacker = self.add(engine, "Unsummon", seat="B", zone="hand")
        remove_source = self.add(engine, "Disenchant", seat="C", zone="hand")
        for index in range(2):
            self.add(engine, "Island", ref=f"faction-activation-island-{index}")
        self.add(engine, "Island", seat="B")
        for index in range(2):
            self.add(engine, "Plains", seat="C", ref=f"faction-response-plains-{index}")
        for card in (source, mirror, remove_attacker, remove_source, *attackers):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.choose_faction(game, source, "Enclave")

        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority"
                   and current.state.pending_decision.actors == ["A"])
        action = next(action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if action["id"].startswith(f"activate:{mirror.ref}:"))
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(ORIGINAL, game.engine.display_name(mirror.object_id))
        self.assertNotIn("chosen_faction", game.state.cards[mirror.object_id].annotations)

        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack",
            "attackers": {attackers[0].ref: "A", attackers[1].ref: "A", attackers[2].ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        item = self._trigger(game, source, "combat.player_attacked")
        self.assertIsNotNone(item)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, self.trigger_count(game, mirror))
        self.assertEqual(("B", "A", 2, []),
            (item.context["player"], item.context["defender"], item.context["attacker_count"], item.targets))

        self.cast_for(game, "B", remove_attacker, [attackers[0].ref])
        self.resolve_top(game)
        self.cast_for(game, "C", remove_source, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[attackers[0].object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotIn("chosen_faction", game.state.cards[source.object_id].annotations)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"A": 0, "B": 4, "C": 0, "D": 0},
            {seat: game.state.players[seat].counters.get("rad", 0) for seat in "ABCD"})
        self._replay_receipt(game, "faction-enclave-game-record")

    def test_enclave_counters_feed_next_precombat_main_radiation(self):
        game = self.session(30460703)
        game.state.config.semantic_policy = "trusted_only"
        engine = game.engine
        source = self.add(engine, ORIGINAL, zone="hand")
        attacker = self.add(engine, "Grizzly Bears", seat="B", ref="enclave-attacker")
        nonland = self.add(engine, "Grizzly Bears", seat="B", zone="library", ref="enclave-milled-creature")
        land = self.add(engine, "Plains", seat="B", zone="library", ref="enclave-milled-land")
        for number in range(2):
            self.add(engine, "Plains", seat="B", zone="library", ref=f"enclave-turn-draw-{number}")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.choose_faction(game, source, "Enclave")

        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        player = game.state.players["B"]
        self.assertEqual(0, player.counters.get("rad", 0))
        self.assertFalse(any(RAD_EFFECT_FIELD in item.context for item in game.state.stack))
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "A"}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNotNone(self._trigger(game, source, "combat.player_attacked"))
        self.resolve_top(game)
        self.assertEqual((2, 40), (player.counters.get("rad", 0), player.life))

        self.advance_through_cleanup(game, lambda current: current.state.active_player == "B"
                                     and current.state.phase == "postcombat_main")
        self.assertEqual(2, player.counters.get("rad", 0))
        self.assertFalse(any(RAD_EFFECT_FIELD in item.context for item in game.state.stack))
        for seat in "CDAB":
            self.advance_through_cleanup(game, lambda current: current.state.active_player == seat
                                         and current.state.phase == "precombat_main")
            radiation = [item for item in game.state.stack if RAD_EFFECT_FIELD in item.context]
            if seat != "B":
                self.assertEqual([], radiation)

        trigger, = radiation
        self.assertEqual(("B", "triggered_ability", None, None, []),
                         (trigger.controller, trigger.kind, trigger.source_object_id,
                          trigger.card_object_id, trigger.targets))
        self.assertEqual((2, 40), (player.counters.get("rad", 0), player.life))
        for card in (land, nonland):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
            self.assertNotIn(card.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.resolve_top(game)
        self.assertEqual((1, 39), (player.counters.get("rad", 0), player.life))
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (land, nonland)))
        radiation_life, = [event for event in game.state.events
                           if event.code == "effect.life" and event.details.get("cause") == RADIATION_CAUSE]
        self.assertEqual(-1, radiation_life.details["delta"])
        self.assertEqual({"A": 0, "B": 1, "C": 0, "D": 0},
                         {seat: game.state.players[seat].counters.get("rad", 0) for seat in "ABCD"})
        self._replay_receipt(game, "faction-enclave-radiation-game-record")

    def test_enclave_admission_loses_real_inherent_rad_authority(self):
        live = self.registry.closure((FACTION_RAD_CAPABILITY,), profile=PROFILE)
        self.assertTrue(live.trusted, live.blockers)
        self.assertIn(RAD_CAPABILITY, self.registry.capability(FACTION_RAD_CAPABILITY)["dependencies"])
        self.assertTrue(self.registry.closure((RAD_CAPABILITY,), profile=PROFILE).trusted)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        rad = next(row for row in raw["capabilities"] if row["id"] == RAD_CAPABILITY)
        rad.update(status="blocked", blockers=["Test withdrawal of inherent rad authority"])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))

        attack = withdrawn.closure((FACTION_RAD_CAPABILITY,), profile=PROFILE)
        self.assertEqual(tuple(row for row in attack.blockers if RAD_CAPABILITY in row), attack.blockers)
        self.assertIn(f"status:{RAD_CAPABILITY}:blocked", attack.blockers)
        self.assertTrue(withdrawn.closure((FACTION_DRAW_CAPABILITY,), profile=PROFILE).trusted)
        bound, _ = register_card(self.db, withdrawn, ORIGINAL)
        self.assertFalse(bound["strict_capability_ready"], bound)

    def test_whole_card_requires_exact_branches_and_independent_capabilities(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        registered = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        capabilities = {"zone.change": ENTRY_FACTION_CAPABILITY, "step.begin": FACTION_DRAW_CAPABILITY,
                        "combat.player_attacked": FACTION_RAD_CAPABILITY}
        self.assertEqual(set(capabilities), set(registered))
        for event, capability in capabilities.items():
            program = registered[event]
            self.assertIsNone(program.target_schema)
            deps = [value for value in program.capability_dependencies if value != capability]
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            decision = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(decision["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", decision["blockers"])
        for oracle_text in (
            card.oracle_text.replace("Brotherhood or Enclave", "Brotherhood or Vault"),
            card.oracle_text.split("\n", 1)[1],
            card.oracle_text.replace("card drawn this way", "opponent"),
            card.oracle_text.replace("twice that many", "that many"),
            card.oracle_text.replace("attacks you", "attacks you or a planeswalker you control"),
        ):
            declined = compile_oracle_card(replace(card, oracle_text=oracle_text),
                                           capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", declined.status)
        with patch("quorune.compiler.faction_trigger_templates.faction_trigger_node", return_value=None):
            declined = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", declined.status)

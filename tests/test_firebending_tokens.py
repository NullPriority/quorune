"""Created tokens own separate Firebending triggers and combat-retained mana."""

from pathlib import Path
import json
import os
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native
import test_provoke_rules as combat


class FirebendingTokenRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay
    advance = combat.ProvokeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "firebending-tokens.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/firebending-token-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Intrinsic Firebending tokens", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_created_token_triggers_survive_sacrifice_and_retain_mana_until_combat_ends(self):
        session = self.session(979_001)
        source = self.add(session.engine, "Fire Nation Attacks", zone="hand")
        haste = self.add(session.engine, "Fervor")
        sacrifice = self.add(session.engine, "Goblin Bombardment")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="firebending-private-C")
        for card in (source, haste, sacrifice):
            self.assertTrue(self.binding(session, card)["strict_capability_ready"], card.printed_name)
        self.prepare(session, mana={"C": 10, "R": 10})
        self.checkpoint(session)
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        tokens = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual(("A", "A"), (token.owner, token.controller))
            self.assertEqual((2, 2), (session.engine._numeric_stat(token.object_id, "power"),
                session.engine._numeric_stat(token.object_id, "toughness")))
        self.advance(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(0, session.state.players["A"].mana_pool["R"])
        attack = {"action_id": "attack", "attacks": {tokens[0].ref: "B", tokens[1].ref: "C"}}
        self.reject(session, attack, seat="D")
        result = session.act("pilot:A", attack)
        self.assertTrue(result.ok, result.summary)
        pending = [*session.state.stack,
            *(item for batch in session.state.pending_trigger_batches for item in batch.items)]
        triggers = [item for item in pending if item.source_object_id in {token.object_id for token in tokens}]
        self.assertEqual(2, len(triggers))
        self.assertEqual({token.object_id for token in tokens}, {item.source_object_id for item in triggers})
        self.assertTrue(all(item.controller == "A" for item in triggers))
        session = self.private_replay(session, private)
        if session.state.pending_decision.kind == "trigger.order":
            rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
            command = {"action_id": "order", "triggers": [row["id"] for row in rows]}
            self.reject(session, command, seat="D")
            result = session.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
        self.priority(session, "A")
        row = next(row for row in self.actions(session) if row["id"].startswith("activate:" + sacrifice.ref + ":"))
        command = {"action_id": row["id"], "cost_cards": [tokens[0].ref], "targets": ["D"], "pay": "auto"}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn(tokens[0].object_id, session.state.players["A"].zones["battlefield"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual(2, session.state.players["A"].mana_pool["R"])
        self.assertEqual(39, session.state.players["D"].life)
        self.advance(session, lambda current: current.state.step == "end_combat"
            and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertEqual(2, session.state.players["A"].mana_pool["R"])
        self.assertEqual({"A": 40, "B": 40, "C": 38, "D": 39},
            {seat: session.state.players[seat].life for seat in "ABCD"})
        self.advance(session, lambda current: current.state.phase == "postcombat_main"
            and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertEqual(0, session.state.players["A"].mana_pool["R"])
        self.private_replay(session, private)


class FirebendingSourceAndExileTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FirebendingTokenRuntimeTests.setUpClass.__func__(cls)
        cls.deck = DeckDefinition("Fire Lord Zuko witness", [
            DeckEntry("Fire Lord Zuko", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Fire Lord Zuko"])

    def save_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import authoritative_state_hash, replay_record

        parent = os.environ.get("QUORUNE_ZUKO_RECORD_PARENT")
        if parent is None:
            self.replay(game, load=True)
            return
        destination = Path(parent) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            manifest_hash = file_digest(handle, "sha256").hexdigest()
        destination.with_suffix(".receipt.json").write_text(
            json.dumps({"manifest_sha256": manifest_hash, "final_state_sha256": state_hash,
                        "commands": len(game.commands)}, indent=2) + "\n", encoding="utf-8")

    def test_firebending_x_uses_live_or_last_known_power_and_expires_after_combat(self):
        for leaves in (False, True):
            with self.subTest(leaves=leaves):
                game = self.session(702189400 + int(leaves))
                source = self.add(game.engine, "Fire Lord Zuko", ref="firebending-original")
                boost = self.add(game.engine, "Brute Force", zone="hand")
                self.add(game.engine, "Mountain")
                if leaves:
                    response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B")
                    self.assert_closed(game, response)
                for card in (source, boost):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
                result = game.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
                self.assertTrue(result.ok, result.summary)
                trigger, = game.state.stack
                self.assertEqual(("triggered_ability", "creature.attacks"), (trigger.kind, trigger.context["event"]))
                self.assertFalse(game.state.players["A"].mana_pool.get("R", 0))
                self.cast_for(game, "A", boost, [source.ref])
                self.resolve_top(game)
                self.assertEqual(5, game.engine._numeric_stat(source.object_id, "power"))
                if leaves:
                    self.cast_for(game, "B", response, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                label = "lki" if leaves else "live"
                self.save_replay(game, f"zuko-{label}-pending-game-record")
                self.resolve_top(game)
                self.assertEqual(5, game.state.players["A"].mana_pool["R"])
                self.until(game, lambda current: current.state.step == "end_combat")
                self.assertEqual(5, game.state.players["A"].mana_pool["R"])
                self.until(game, lambda current: current.state.phase == "postcombat_main")
                self.assertEqual(0, game.state.players["A"].mana_pool.get("R", 0))
                self.save_replay(game, f"zuko-{label}-ended-combat-game-record")

    def test_zuko_command_cast_and_separate_exile_cast_and_entry_receipts(self):
        from quorune.oracle_ir import register_generated_programs

        game = self.session(608214001)
        source, = (game.state.cards[oid] for oid in game.state.players["A"].zones["command"])
        self.assertTrue(source.is_commander)
        register_generated_programs(self.db, game.engine.semantics, (self.db.lookup(source.printed_name),),
            trust_level="provisional", capability_registry=self.registry, capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        impulse = self.add(game.engine, "Reckless Impulse", zone="hand")
        blink = self.add(game.engine, "Flicker of Fate", zone="hand")
        creature = self.add(game.engine, "Isamaru, Hound of Konda", zone="library", ref="zuko-exile-creature")
        self.add(game.engine, "Mountain", zone="library", ref="zuko-exile-land")
        for card in (source, impulse, blink, creature):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "R": 3, "W": 5, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast_for(game, "A", impulse)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[creature.object_id].zone)
        self.cast_for(game, "A", game.state.cards[creature.object_id])
        trigger = game.state.stack[-1]
        self.assertEqual((source.object_id, "spell.cast", "exile"),
                         (trigger.source_object_id, trigger.context["event"], trigger.context["from"]))
        self.save_replay(game, "zuko-exile-cast-pending-game-record")
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[source.object_id].counters["+1/+1"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertFalse(game.state.cards[creature.object_id].counters)
        self.cast_for(game, "A", blink, [game.state.cards[creature.object_id].ref])
        self.resolve_top(game)
        trigger = game.state.stack[-1]
        self.assertEqual((source.object_id, "permanent.enter", "exile"),
                         (trigger.source_object_id, trigger.context["event"], trigger.context["from"]))
        self.save_replay(game, "zuko-exile-arrival-pending-game-record")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[source.object_id].counters["+1/+1"])
        self.assertEqual(1, game.state.cards[creature.object_id].counters["+1/+1"])
        self.save_replay(game, "zuko-exile-receipts-resolved-game-record")

    def test_zuko_full_binding_rejects_withdrawn_ability_authorities(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.exile_origin_creature_counters import CAPABILITY as EXILE_CAPABILITY
        from quorune.rules.source_power_firebending import CAPABILITY as FIRE_CAPABILITY
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Fire Lord Zuko", fuzzy=False)
        profile = "commander_review"
        bound, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = {row.event: row for row in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"creature.attacks", "spell.cast"}, set(programs))
        for event, cap in (("creature.attacks", FIRE_CAPABILITY), ("spell.cast", EXILE_CAPABILITY)):
            program = programs[event]
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            mutant = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile=profile).to_dict())
            result = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=profile)
            self.assertFalse(result["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + cap, result["blockers"])
        for oracle in (card.oracle_text.replace("Zuko's power", "Zuko's toughness"),
                       card.oracle_text.replace("enters from exile", "enters from anywhere"),
                       card.oracle_text.replace("each creature you control", "each target creature you control")):
            result = compile_oracle_card(
                replace(card, oracle_text=oracle), capability_registry=self.registry, capability_profile=profile,
            )
            self.assertNotEqual("exact", result.status)

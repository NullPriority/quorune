"""Actual main-phase casts select complete, fixed original spell results."""

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_CONTEXT, MAIN_PHASE_CAST_REPLACEMENT_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhaseCastResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-cast-results.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-cast-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original cast timing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_require_the_actual_cast_and_preserve_the_original_results(self):
        for name, operation in (
            ("Might of Old Krosa", "modify_stats_until_end_of_turn"),
            ("Careful Consideration", "draw"),
            ("Summary Judgment", "damage"),
            ("Sulfurous Blast", "damage_fixed_set"),
        ):
            with self.subTest(name=name):
                admission, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantics.programs_for_oracle(card.oracle_id)
                condition, = spell.effects
                self.assertEqual(MAIN_PHASE_CAST_REPLACEMENT_VERSION, condition["schema_version"])
                self.assertEqual(operation, condition["effects"][0]["op"])
                self.assertEqual(operation, condition["otherwise_effects"][0]["op"])
                for altered in (
                    {**condition, "cast_fact": True},
                    {**condition, "player": "$active"},
                    {**condition, "otherwise_effects": []},
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        replace(spell, effects=[altered]), capability_registry=self.registry,
                        profile="commander_review",
                    )["strict"])
                changed = replace(card, oracle_text=card.oracle_text.replace("your main phase", "your first main phase"))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def keep_targets(self, game, actor):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        result = game.act("pilot:" + actor, {"action_id": "choose"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_main_phase_might_adds_four_and_its_uncast_copy_adds_two(self):
        game = self.session(60126001)
        spell = self.add(game.engine, "Might of Old Krosa", zone="hand")
        copy = self.add(game.engine, "Twincast", zone="hand", seat="B")
        creature = self.add(game.engine, "Grizzly Bears")
        for card in (spell, copy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [creature.ref])
        original = game.state.stack[-1]
        self.assertEqual({"schema_version": 1, "caster": "A", "own_main_phase": True},
                         original.context[MAIN_PHASE_CAST_CONTEXT])
        self.assertEqual(2, game.engine._numeric_stat(creature.object_id, "power"))
        self.cast_for(game, "B", copy, [original.ref])
        self.keep_targets(game, "B")
        self.assertEqual(("spell_copy", "B"), (game.state.stack[-1].kind, game.state.stack[-1].controller))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(4, game.engine._numeric_stat(creature.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(8, game.engine._numeric_stat(creature.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_spell_control_change_cannot_claim_another_players_cast(self):
        game = self.session(60126002)
        spell = self.add(game.engine, "Might of Old Krosa", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", zone="hand", seat="B")
        creature = self.add(game.engine, "Grizzly Bears")
        for card in (spell, takeover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        game.state.players["B"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [creature.ref])
        original = game.state.stack[-1]
        self.cast_for(game, "B", takeover, [original.ref])
        self.keep_targets(game, "B")
        controlled = game.state.stack[-1]
        self.assertEqual((original.ref, "B", "A"), (
            controlled.ref, controlled.controller, controlled.context[MAIN_PHASE_CAST_CONTEXT]["caster"],
        ))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(creature.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_careful_consideration_draws_before_its_target_privately_discards_two(self):
        game = self.session(60126003)
        spell = self.add(game.engine, "Careful Consideration", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "U": 2})
        old_hand = set(game.state.players["B"].zones["hand"])
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        drawn = set(game.state.players["B"].zones["hand"]) - old_hand
        self.assertEqual(4, len(drawn))
        selected = [game.state.cards[object_id].ref for object_id in sorted(drawn)[:2]]
        for viewer in ("pilot:A", "pilot:C", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            for ref in selected:
                self.assertNotIn(ref, json.dumps(packet))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for actor, refs in (("A", selected), ("B", selected[:1])):
            self.assertFalse(game.act("pilot:" + actor, {"action_id": "choose", "cards": refs}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", {"action_id": "choose", "cards": selected})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(len(old_hand) + 2, len(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_summary_judgment_keeps_one_tapped_target_and_revalidates_its_incarnation(self):
        game = self.session(60126004)
        spell = self.add(game.engine, "Summary Judgment", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        ready = self.add(game.engine, "Air Elemental", seat="B", ref="summary-tapped-creature")
        ready.tapped = True
        illegal = self.add(game.engine, "Air Elemental", seat="B", ref="summary-untapped-creature")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        offer = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": offer["id"], "targets": [illegal.ref], "pay": "auto"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [ready.ref])
        bounced = self.replay(game, load=True)
        self.cast_for(bounced, "B", bounce, [ready.ref])
        self.resolve_top(bounced)
        self.assertIsNone(self.resolve(bounced))
        self.assertEqual("hand", bounced.state.cards[ready.object_id].zone)
        self.assertEqual(0, bounced.state.cards[illegal.object_id].marked_damage)
        self.replay(bounced, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[ready.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[illegal.object_id].zone)
        self.replay(game, load=True)

    def test_actual_global_damage_distinguishes_own_and_opponent_main_phase(self):
        for actor, amount in (("A", 3), ("B", 2)):
            with self.subTest(actor=actor):
                game = self.session(60126005 + amount)
                spell = self.add(game.engine, "Sulfurous Blast", zone="hand", seat=actor)
                creatures = [self.add(game.engine, "Air Elemental", seat=seat, ref=f"blast-{seat}")
                             for seat in "ABCD"]
                ring = self.add(game.engine, "Sol Ring", seat="C")
                self.assert_closed(game, spell)
                if actor == "A":
                    for index, name in enumerate(("Mountain", "Mountain", "Plains", "Plains")):
                        self.add(game.engine, name, ref=f"blast-mana-{index}")
                self.prepare(game, actor=actor, active="A", mana=None if actor == "A" else {"C": 2, "R": 2})
                self.checkpoint(game)
                if actor == "A":
                    self.until(game, lambda current: current.state.phase == "postcombat_main")
                self.cast_for(game, actor, spell)
                self.assertIsNone(self.resolve(game))
                self.assertEqual([40 - amount] * 4, [game.state.players[seat].life for seat in "ABCD"])
                self.assertEqual([amount] * 4, [game.state.cards[card.object_id].marked_damage for card in creatures])
                self.assertEqual(0, game.state.cards[ring.object_id].marked_damage)
                self.replay(game, load=True)


class MainPhaseOptionalExileTests(InheritedNativeFixture, unittest.TestCase):
    setUpClass = classmethod(FixedMainPhaseCastResultTests.setUpClass.__func__)

    def save_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import replay_record

        parent = os.environ.get("QUORUNE_MAIN_PHASE_EXILE_RECORD_PARENT")
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

    def cast_dust(self, game, seat, card, first, second=None):
        self.priority(game, seat)
        offer = self.action(game, seat, card)
        groups = {row["id"]: row for row in offer["target_schema"]["groups"]}
        self.assertEqual((1, 1, 0, 1),
                         (groups["target_0"]["min"], groups["target_0"]["max"],
                          groups["target_1"]["min"], groups["target_1"]["max"]))
        self.assertIn(first.ref, groups["target_0"]["legal_refs"])
        if second is not None:
            self.assertIn(second.ref, groups["target_1"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        invalid = [
            {"target_0": [first.ref], "target_1": [first.ref]},
            {"target_0": [], "target_1": [first.ref]},
        ]
        for targets in invalid:
            rejected = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [
                {"group": group, "ref": ref} for group, refs in targets.items() for ref in refs
            ], "pay": "auto"})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        targets = {"target_0": [first.ref], "target_1": [second.ref] if second is not None else []}
        result = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [
            {"group": group, "ref": ref} for group, refs in targets.items() for ref in refs
        ], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        self.assertEqual(targets, item.context["target_groups"])
        return item

    def finish_optional_exile(self, game, *, apply):
        self.assertEqual(("semantic.choice", ["A"]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        command = {"action_id": "choose", "choice": "apply" if apply else "decline"}
        before = authoritative_state_hash(game.state)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_dust_main_phase_selects_optional_target_then_declines_at_resolution(self):
        for omit in (False, True):
            with self.subTest(omit=omit):
                game = self.session(60121671 + int(omit))
                spell = self.add(game.engine, "Return to Dust", zone="hand")
                primary = self.add(game.engine, "Sol Ring", seat="B")
                extra = self.add(game.engine, "Garruk's Uprising", seat="C")
                self.prepare(game, mana={"C": 2, "W": 2})
                self.assert_closed(game, spell)
                self.checkpoint(game)
                item = self.cast_dust(game, "A", spell, primary, None if omit else extra)
                self.assertEqual({"schema_version": 1, "caster": "A", "own_main_phase": True},
                                 item.context[MAIN_PHASE_CAST_CONTEXT])
                pending = self.resolve(game)
                self.assertEqual("exile", game.state.cards[primary.object_id].zone)
                if omit:
                    self.assertIsNone(pending)
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    self.save_replay(game, "return-to-dust-main-optional-game-record")
                    self.finish_optional_exile(game, apply=False)
                self.assertEqual("battlefield", game.state.cards[extra.object_id].zone)
                self.save_replay(game, "return-to-dust-main-omitted-game-record" if omit
                                 else "return-to-dust-main-declined-game-record")

    def test_dust_off_turn_second_target_can_keep_it_resolving_after_first_becomes_illegal(self):
        for choose_second in (True, False):
            with self.subTest(choose_second=choose_second):
                game = self.session(60121673 + int(choose_second))
                spell = self.add(game.engine, "Return to Dust", seat="B", zone="hand")
                primary = self.add(game.engine, "Sol Ring", seat="C")
                extra = self.add(game.engine, "Garruk's Uprising", seat="D")
                response = self.add(game.engine, "Disenchant", zone="hand")
                self.prepare(game, mana={"C": 1, "W": 1})
                game.state.players["B"].mana_pool.update({"C": 2, "W": 2})
                for card in (spell, response):
                    self.assert_closed(game, card)
                self.checkpoint(game)
                item = self.cast_dust(game, "B", spell, primary, extra if choose_second else None)
                self.assertEqual({"schema_version": 1, "caster": "B", "own_main_phase": False},
                                 item.context[MAIN_PHASE_CAST_CONTEXT])
                self.cast_for(game, "A", response, [primary.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[primary.object_id].zone)
                self.assertIsNone(self.resolve(game))
                relevant = [event for event in game.state.events if event.details.get("stack") == item.ref]
                self.assertEqual(choose_second, any(event.code == "stack.resolve" for event in relevant))
                self.assertEqual(not choose_second, any(event.details.get("reason") == "all targets illegal on resolution"
                                                        for event in relevant))
                self.assertEqual("battlefield", game.state.cards[extra.object_id].zone)
                self.save_replay(game, "return-to-dust-off-turn-partial-game-record" if choose_second
                                 else "return-to-dust-all-illegal-game-record")

    def test_dust_copy_keeps_both_targets_exiles_only_first_and_original_can_choose_second(self):
        game = self.session(60121677)
        spell = self.add(game.engine, "Return to Dust", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        primary = self.add(game.engine, "Sol Ring", seat="C")
        extra = self.add(game.engine, "Garruk's Uprising", seat="D")
        self.prepare(game, mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool["U"] = 2
        for card in (spell, copy):
            self.assert_closed(game, card)
        self.checkpoint(game)
        original = self.cast_dust(game, "A", spell, primary, extra)
        self.cast_for(game, "B", copy, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.save_replay(game, "return-to-dust-copy-target-pending-game-record")
        result = game.act("pilot:B", {"action_id": "choose"})
        self.assertTrue(result.ok, result.summary)
        copied = game.state.stack[-1]
        self.assertEqual("spell_copy", copied.kind)
        self.assertEqual(original.context["target_groups"], copied.context["target_groups"])
        self.resolve_top(game)
        self.assertEqual(("exile", "battlefield"),
                         (game.state.cards[primary.object_id].zone, game.state.cards[extra.object_id].zone))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.save_replay(game, "return-to-dust-remaining-optional-game-record")
        self.finish_optional_exile(game, apply=True)
        self.assertEqual("exile", game.state.cards[extra.object_id].zone)
        self.save_replay(game, "return-to-dust-copy-and-original-game-record")

    def test_dust_requires_exact_group_roles_optional_result_and_cast_authority(self):
        from copy import deepcopy
        from quorune.cast_origin_replacement import MAIN_PHASE_OPTIONAL_EXILE_CAPABILITY

        profile = "commander_review"
        card = self.db.lookup("Return to Dust", fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(value for value in program.capability_dependencies if value != MAIN_PHASE_OPTIONAL_EXILE_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=profile).to_dict())
        blocked = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=profile)
        self.assertFalse(blocked["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{MAIN_PHASE_OPTIONAL_EXILE_CAPABILITY}",
                      blocked["blockers"])
        altered = deepcopy(program.target_schema)
        altered["groups"][1]["different_from_groups"] = []
        self.assertFalse(bind_semantic_program_runtime(
            replace(program, target_schema=altered), capability_registry=self.registry, profile=profile,
        )["strict"])
        for text in (card.oracle_text.replace("your main phase", "your turn"),
                     card.oracle_text.replace("one other target", "one target"),
                     card.oracle_text.replace("you may exile", "exile")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=profile,
            ).status)

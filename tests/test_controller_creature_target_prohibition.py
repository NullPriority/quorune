"""Actual cast-triggered targeting restriction for the live creature set."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_WINDOW,
    turn_creature_target_prohibition_instruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class ControllerCreatureTargetProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-creature-target-prohibition.sqlite3"
        fixture = ROOT / "tests/fixtures/controller-creature-target-prohibition-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Current creature targeting", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 1, "U": 3, "W": 1})
        self.prepare(game, mana={"G": 1, "C": 1, "U": 3})
        self.checkpoint(game)

    def target_offer(self, game, seat, *, action=None, source=None):
        self.priority(game, seat)
        options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        if source is not None:
            offer, = (row for row in options if row["id"].startswith(f"activate:{source.ref}:"))
        else:
            offer, = (row for row in options if row["id"] == action)
        return offer

    def reject_target(self, game, seat, offer, *, refused, available):
        legal = offer["target_schema"]["groups"][0]["legal_refs"]
        self.assertIn(available, legal)
        self.assertNotIn(refused, legal)
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [refused], "pay": "auto"})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_cast_rule_revalidates_older_targets_after_the_amulet_leaves(self):
        game = self.session(76343001)
        source = self.add(game.engine, "Veilstone Amulet")
        bear = self.add(game.engine, "Grizzly Bears")
        old = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        draw = self.add(game.engine, "Reach Through Mists", zone="hand")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.ready(game, source, bear, old, draw, destroy)
        self.cast(game, "B", old, [bear.ref])
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast(game, "A", draw)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", destroy, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotIn(TURN_CREATURE_TARGET_PROHIBITION_WINDOW, game.state.players["A"].stats)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(game.state.turn_sequence, game.state.players["A"].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[old.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertNotIn("hexproof", game.engine._combat_keywords(bear))
        self.replay(game, load=True)

    def test_actual_live_creature_menu_denies_opponents_allows_self_and_expires(self):
        game = self.session(76343002)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(object_id, "library", reason="Prepare an ordinary starting hand for cleanup")
        source = self.add(game.engine, "Veilstone Amulet")
        resident = self.add(game.engine, "Grizzly Bears", ref="target-rule-resident")
        arriving = self.add(game.engine, "Grizzly Bears", zone="hand", ref="target-rule-arriving")
        own = self.add(game.engine, "Unsummon", zone="hand", ref="target-rule-own")
        opposed = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="target-rule-opposed")
        eligible = self.add(game.engine, "Grizzly Bears", seat="B", ref="target-rule-unprotected")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="C")
        pinger.acquired_control_turn_count = -1
        self.add(game.engine, "Island", seat="B")
        self.ready(game, source, arriving, opposed, own, pinger)
        self.cast(game, "A", arriving)
        self.assertEqual("stack", game.state.cards[arriving.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(game.state.turn_sequence, game.state.players["A"].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW])
        self.resolve_top(game)
        arriving = game.state.cards[arriving.object_id]
        self.assertEqual("battlefield", arriving.zone)
        spell_offer = self.target_offer(game, "B", action=f"cast:{opposed.ref}")
        self.reject_target(game, "B", spell_offer, refused=arriving.ref, available=eligible.ref)
        ability_offer = self.target_offer(game, "C", source=pinger)
        self.reject_target(game, "C", ability_offer, refused=resident.ref, available=eligible.ref)
        result = game.act("pilot:C", {"action_id": ability_offer["id"], "targets": [eligible.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[eligible.object_id].marked_damage)
        own_offer = self.target_offer(game, "A", action=f"cast:{own.ref}")
        self.assertIn(resident.ref, own_offer["target_schema"]["groups"][0]["legal_refs"])
        self.cast(game, "A", own, [resident.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[resident.object_id].zone)
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertNotIn(TURN_CREATURE_TARGET_PROHIBITION_WINDOW, game.state.players["A"].stats)
        offer = self.target_offer(game, "B", action=f"cast:{opposed.ref}")
        self.assertIn(arriving.ref, offer["target_schema"]["groups"][0]["legal_refs"])
        self.cast(game, "B", opposed, [arriving.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[arriving.object_id].zone)
        self.replay(game, load=True)

    def test_vines_casters_opponents_lose_old_and_new_targets_but_caster_keeps_them(self):
        from quorune.rules.targeted_turn_target_prohibition import WINDOW

        game = self.session(76343011)
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="opponent-protected-creature")
        bystander = self.add(game.engine, "Grizzly Bears", seat="C", ref="unrestricted-creature")
        vines = self.add(game.engine, "Vines of Vastwood", zone="hand")
        old = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="prior-owner-spell")
        later = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="later-owner-spell")
        caster_spell = self.add(game.engine, "Unsummon", zone="hand", ref="caster-still-targets")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="C")
        pinger.acquired_control_turn_count = -1
        self.assert_closed(game, vines)
        self.prepare(game, active="B", actor="B", mana={"U": 3, "G": 1, "C": 1})
        game.state.players["A"].mana_pool.update({"G": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "B", old, [target.ref])
        self.cast_for(game, "A", vines, [target.ref])
        self.resolve_top(game)
        self.assertEqual(2, game.engine._numeric_stat(target.object_id, "power"))
        self.assertIn(WINDOW, game.state.players["A"].stats)
        self.assertNotIn(WINDOW, game.state.players["B"].stats)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[old.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)

        offer = self.target_offer(game, "B", action=f"cast:{later.ref}")
        self.reject_target(game, "B", offer, refused=target.ref, available=bystander.ref)
        ability = self.target_offer(game, "C", source=pinger)
        self.reject_target(game, "C", ability, refused=target.ref, available=bystander.ref)
        offer = self.target_offer(game, "A", action=f"cast:{caster_spell.ref}")
        self.assertIn(target.ref, offer["target_schema"]["groups"][0]["legal_refs"])
        incarnation = game.state.cards[target.object_id].logical_object_id
        self.cast_for(game, "A", caster_spell, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.cast_for(game, "B", target)
        self.resolve_top(game)
        self.assertNotEqual(incarnation, game.state.cards[target.object_id].logical_object_id)
        offer = self.target_offer(game, "B", action=f"cast:{later.ref}")
        self.assertIn(target.ref, offer["target_schema"]["groups"][0]["legal_refs"])
        self.replay(game, load=True)

    def test_vines_kicked_copy_has_its_own_caster_target_bonus_and_clears_at_cleanup(self):
        from quorune.kicker import KICKER_CAST_OPTION_ID
        from quorune.rules.targeted_turn_target_prohibition import WINDOW

        game = self.session(76343012)
        starting_card = game.state.players["A"].zones["hand"][0]
        game.engine.move_card(starting_card, "library", reason="Prepare a starting hand within maximum size at cleanup")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="original-vines-target")
        copy_target = self.add(game.engine, "Grizzly Bears", seat="C", ref="retargeted-vines-copy")
        vines = self.add(game.engine, "Vines of Vastwood", zone="hand")
        copier = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        response_a = self.add(game.engine, "Unsummon", zone="hand", ref="later-vines-caster-a")
        response_b = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="later-vines-caster-b")
        self.assert_closed(game, vines)
        self.assert_closed(game, copier)
        self.prepare(game, mana={"G": 2, "U": 1})
        game.state.players["B"].mana_pool.update({"R": 2, "U": 1})
        self.checkpoint(game)
        action = self.target_offer(game, "A", action=f"cast:{vines.ref}")
        options = {row["id"]: row for row in action["cost_options"]}
        self.assertEqual(2, options[KICKER_CAST_OPTION_ID]["requirements"]["G"])
        announced = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref],
                                        "cost_option": KICKER_CAST_OPTION_ID, "pay": "auto"})
        self.assertTrue(announced.ok, announced.summary)
        source = game.state.stack[-1]
        self.assertIs(source.context["kicked"], True)
        self.cast_for(game, "B", copier, [source.ref], reserve={"U": 1})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:B"], game.pending_principals())
        game = self.replay(game, load=True)
        new_target = game.act("pilot:B", {"action_id": "choose", "targets": [copy_target.ref]})
        self.assertTrue(new_target.ok, new_target.summary)
        copy_item = game.state.stack[-1]
        self.assertEqual(("B", "spell_copy", [copy_target.ref]), (copy_item.controller, copy_item.kind, copy_item.targets))
        self.assertIs(copy_item.context["kicked"], True)
        self.assertIsNone(self.resolve(game))
        for protected in (target, copy_target):
            self.assertEqual((6, 6), tuple(game.engine._numeric_stat(protected.object_id, field) for field in ("power", "toughness")))
        actor_a = self.target_offer(game, "A", action=f"cast:{response_a.ref}")
        actor_b = self.target_offer(game, "B", action=f"cast:{response_b.ref}")
        self.assertIn(target.ref, actor_a["target_schema"]["groups"][0]["legal_refs"])
        self.assertNotIn(copy_target.ref, actor_a["target_schema"]["groups"][0]["legal_refs"])
        self.assertIn(copy_target.ref, actor_b["target_schema"]["groups"][0]["legal_refs"])
        self.assertNotIn(target.ref, actor_b["target_schema"]["groups"][0]["legal_refs"])
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        for seat in "AB":
            self.assertNotIn(WINDOW, game.state.players[seat].stats)
        for protected in (target, copy_target):
            self.assertEqual(2, game.engine._numeric_stat(protected.object_id, "power"))
        self.replay(game, load=True)

    def test_vines_whole_program_requires_its_same_target_and_caster_kicked_fact(self):
        from copy import deepcopy
        from unittest.mock import patch
        from quorune.rules.targeted_turn_target_prohibition import CAPABILITY, targeted_turn_prohibition_spell

        report, programs = register_card(self.db, self.registry, "Vines of Vastwood")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if CAPABILITY in row.capability_dependencies)
        effects, schema, _ = targeted_turn_prohibition_spell()
        self.assertEqual(effects, tuple(program.effects))
        self.assertEqual(schema, program.target_schema)
        for change in ({"active_zone": "battlefield"}, {"target_schema": {**schema, "controller_relation": "you"}},
                       {"effects": ({**program.effects[0], "cast_fact": True},)}):
            self.assertFalse(bind_semantic_program_runtime(replace(program, **change),
                capability_registry=self.registry, profile=PROFILE)["strict"])
        changed = deepcopy(program.effects[0])
        changed["otherwise_effects"][0]["player"] = "$target.controller.0"
        self.assertFalse(bind_semantic_program_runtime(replace(program, effects=(changed,)),
            capability_registry=self.registry, profile=PROFILE)["strict"])
        record = self.db.lookup("Vines of Vastwood", fuzzy=False)
        with patch("quorune.compiler.targeted_turn_target_prohibition.targeted_turn_prohibition_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)

    def test_complete_original_requires_the_independent_live_targeting_rule(self):
        cap = TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Veilstone Amulet")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        self.assertEqual(turn_creature_target_prohibition_instruction(), dict(program.effects[0]))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup("Veilstone Amulet", fuzzy=False)
        for before, after in (("creatures", "permanents"), ("spells or abilities", "abilities"),
                              ("this turn.", "until your next turn."), ("you cast", "an opponent casts"),
                              ("this turn.", "this turn. Rewrite history.")):
            with self.subTest(unsupported=after):
                synthetic = replace(original, oracle_text=original.oracle_text.replace(before, after))
                result = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

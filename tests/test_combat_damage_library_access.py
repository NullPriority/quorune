"""Native witnesses for combat-damage exile and ordinary current-turn play."""

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
from quorune.combat_damage_library_exile import COMBAT_DAMAGE_EXILE_CAPABILITY, validate_combat_damage_exile
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
SOURCE = "Fallen Shinobi"


class CombatDamageLibraryAccessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "combat-damage-library-access.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "combat-damage-library-access-cards.json",
            fixtures / "defending-player-top-library-exile-cards.json",
            fixtures / "activated-self-transform-cards.json",
            fixtures / "crime-cards.json",
            fixtures / "fixed-overload-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Combat-damage exile", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def offers(self, game, seat="A"):
        packet = game.packet("pilot:" + seat, full=True)
        return {row["id"]: row for row in packet["decision"]["ctx"]["legal"]["actions"]}

    def accept(self, game, seat, action_id, **choices):
        result = game.act("pilot:" + seat, {"action_id": action_id, **choices})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, action_id, **choices):
        before = authoritative_state_hash(game.state)
        count = len(game.commands)
        result = game.act("pilot:" + seat, {"action_id": action_id, **choices})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(count, len(game.commands))

    def reveal_check(self, game, cards, *, visible):
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in cards:
                self.assertEqual(visible, card.ref in packet, f"unexpected visibility for {seat}")

    def damage_setup(self, seed, spell_name, *, lands=3, bounce=False, short=False):
        game = self.session(seed)
        engine = game.engine
        for object_id in tuple(game.state.players["A"].zones["hand"])[5:]:
            engine.move_card(object_id, "library", reason="Prepare hand space for the returning attackers", log=False)
        ninja = self.add(engine, SOURCE, zone="hand", ref="native-shinobi")
        attacker = self.add(engine, "Grizzly Bears", ref="native-returning-unblocked-attacker")
        second_attacker = self.add(engine, "Grizzly Bears", ref="native-separate-attacker")
        enemy = self.add(engine, "Grizzly Bears", seat="C", ref="native-enemy-target")
        for creature in attacker, second_attacker:
            creature.acquired_control_turn_count = -1
        self.add(engine, "Swamp")
        for index in range(lands):
            self.add(engine, "Island", ref=f"native-mana-island-{index}")
        if short:
            for object_id in tuple(game.state.players["B"].zones["library"]):
                engine.move_card(object_id, "graveyard", reason="Prepare short library", log=False)
        spell = self.add(engine, spell_name, seat="B", zone="library", ref="native-exact-spell-top")
        land = None if short else self.add(engine, "Plains", seat="B", zone="library", ref="native-exact-land-top")
        current_top = (spell,) if land is None else (land, spell)
        for card in current_top:
            card.known_to.clear()
            card.revealed_to.clear()
        unrelated = self.add(engine, "Plains", seat="D", zone="exile", ref="native-unrelated-exile")
        response = None
        if bounce:
            response = self.add(engine, "Unsummon", seat="B", zone="hand", ref="native-source-response")
            self.add(engine, "Island", seat="B", ref="native-response-island")
        for card in ninja, spell:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        previous_incarnations = {card.object_id: card.logical_object_id for card in current_top}
        self.reveal_check(game, current_top, visible=False)

        self.attack(game, [(attacker, "B"), (second_attacker, "C")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority"
                   and current.state.step == "declare_blockers" and current.state.combat.blockers_declared
                   and current.state.priority_player == "A")
        activation = f"activate:{ninja.ref}:ab1"
        self.assertIn(activation, self.offers(game))
        payment = {"cost_objects": [attacker.ref], "pay": "auto"}
        self.reject(game, "B", activation, **payment)
        self.accept(game, "A", activation, **payment)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        entered = game.state.cards[ninja.object_id]
        self.assertEqual(("hand", "battlefield", "B", True),
                         (game.state.cards[attacker.object_id].zone, entered.zone, entered.attacking, entered.tapped))
        self.until(game, lambda current: self.trigger_count(current, entered) == 1)
        triggered = game.state.stack[-1]
        self.assertEqual(("A", "B", "player", True, []), (
            triggered.controller, triggered.context["target"], triggered.context["target_kind"],
            triggered.context["combat"], triggered.targets,
        ))
        self.assertEqual((35, 38), (game.state.players["B"].life, game.state.players["C"].life))
        self.reveal_check(game, current_top, visible=False)
        if response is not None:
            self.cast(game, "B", response, [entered.ref])
            self.resolve_top(game)
            self.assertEqual("hand", game.state.cards[ninja.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        expected = {**libraries, "B": libraries["B"][:-len(current_top)]}
        self.assertEqual(expected, {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        impulse = next(event for event in reversed(game.state.events) if event.code == "library.impulse_access")
        self.assertEqual([card.ref for card in current_top], impulse.details["objects"])
        self.reveal_check(game, current_top, visible=True)
        if expected["B"]:
            self.reveal_check(game, (game.state.cards[expected["B"][-1]],), visible=False)
        for original in current_top:
            exiled = game.state.cards[original.object_id]
            self.assertEqual(("exile", "B", False), (exiled.zone, exiled.owner, exiled.face_down))
            self.assertNotEqual(previous_incarnations[original.object_id], exiled.logical_object_id)
            grant = exiled.annotations["temporary_play_permission"]
            self.assertEqual(("A", "exile", True, game.state.turn_sequence),
                             (grant["player"], grant["zone"], grant["without_mana_cost"], grant["turn_sequence"]))
        return game, spell, land, unrelated, second_attacker, enemy

    def test_actual_ninjutsu_damage_exiles_two_and_normal_play_preserves_additional_cost(self):
        game, spell, land, unrelated, payment, target = self.damage_setup(76344001, "Bone Splinters")
        cast = f"cast:{spell.ref}"
        play = f"play-land:{land.ref}"
        self.priority(game, "A")
        self.assertNotIn(cast, self.offers(game))
        self.assertNotIn(play, self.offers(game))
        self.reject(game, "A", play)
        self.reject(game, "A", cast, targets=[target.ref], sacrifice_cards=[payment.ref], pay="auto")
        self.until(game, lambda current: current.state.phase == "postcombat_main"
                   and current.state.priority_player == "A")
        self.assertNotIn(f"play-land:{unrelated.ref}", self.offers(game))
        self.reject(game, "A", f"play-land:{unrelated.ref}")
        self.reject(game, "B", play)
        self.assertIn(play, self.offers(game))
        before_land = game.state.players["A"].land_plays_remaining
        self.accept(game, "A", play)
        played = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", "B", "A"), (played.zone, played.owner, played.controller))
        self.assertEqual(before_land - 1, game.state.players["A"].land_plays_remaining)
        self.assertNotIn("temporary_play_permission", played.annotations)
        self.reject(game, "A", play)

        self.assertEqual(["without_mana_cost"], [row["id"] for row in self.offers(game)[cast]["cost_options"]])
        self.reject(game, "A", cast, targets=[target.ref], pay="auto")
        self.reject(game, "A", cast, targets=[target.ref], sacrifice_cards=[target.ref], pay="auto")
        self.reject(game, "A", cast, targets=["C"], sacrifice_cards=[payment.ref], pay="auto")
        self.accept(game, "A", cast, targets=[target.ref], sacrifice_cards=[payment.ref], pay="auto")
        stacked = game.state.cards[spell.object_id]
        self.assertEqual(("stack", "B", "A"), (stacked.zone, stacked.owner, game.state.stack[-1].controller))
        self.assertNotIn("temporary_play_permission", stacked.annotations)
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.reject(game, "A", cast, targets=[target.ref], sacrifice_cards=[payment.ref], pay="auto")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertIn(spell.object_id, game.state.players["B"].zones["graveyard"])
        self.replay(game, load=True)

    def test_actual_damage_grant_survives_source_departure_and_rejects_separate_alternative(self):
        game, spell, land, _, _, target = self.damage_setup(76344002, "Cyclonic Rift", lands=10, bounce=True)
        cast = f"cast:{spell.ref}"
        self.priority(game, "A")
        self.assertEqual("combat", game.state.phase)
        untapped_islands = [card for card in game.state.cards.values()
                           if card.owner == "A" and card.ref.startswith("native-mana-island-") and not card.tapped]
        self.assertGreaterEqual(len(untapped_islands), 7)
        self.assertEqual(["without_mana_cost"], [row["id"] for row in self.offers(game)[cast]["cost_options"]])
        self.reject(game, "A", cast, cost_option="overload", targets=[], pay="auto")
        self.priority(game, "B")
        self.assertNotIn(cast, self.offers(game, "B"))
        self.reject(game, "B", cast, cost_option="without_mana_cost", targets=[target.ref], pay="auto")
        self.priority(game, "A")
        self.accept(game, "A", cast, cost_option="without_mana_cost", targets=[target.ref], pay="auto")
        self.assertTrue(all(not game.state.cards[card.object_id].tapped for card in untapped_islands))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        turn = game.state.turn_sequence
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.turn_sequence > turn)
        unused = game.state.cards[land.object_id]
        self.assertEqual("exile", unused.zone)
        self.assertNotIn("temporary_play_permission", unused.annotations)
        self.reject(game, "A", f"play-land:{unused.ref}")
        self.replay(game, load=True)

    def test_actual_damage_exiles_as_many_as_possible_and_obeys_land_quota(self):
        game, land, _, _, _, _ = self.damage_setup(76344003, "Plains", short=True)
        self.until(game, lambda current: current.state.phase == "postcombat_main"
                   and current.state.priority_player == "A")
        hand_lands = [game.state.cards[oid] for oid in game.state.players["A"].zones["hand"]
                      if game.state.cards[oid].printed_name == "Plains"]
        self.assertTrue(hand_lands)
        self.accept(game, "A", f"play-land:{hand_lands[0].ref}")
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.assertNotIn(f"play-land:{land.ref}", self.offers(game))
        self.reject(game, "A", f"play-land:{land.ref}")
        self.replay(game, load=True)

    def test_complete_card_requires_original_combat_damage_authority(self):
        original = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual((), ir.material_residuals)
        ninjutsu, trigger = ir.faces[0].nodes
        self.assertEqual("fixed-ninjutsu-activation-v1", ninjutsu.template_id)
        self.assertEqual(original.oracle_text.splitlines()[1], original.oracle_text[trigger.span.start:trigger.span.end])
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        cap = COMBAT_DAMAGE_EXILE_CAPABILITY
        program, = (row for row in semantics.programs_for_oracle(original.oracle_id) if cap in row.capability_dependencies)
        self.assertIsNone(program.target_schema)
        validate_combat_damage_exile(program.effects[0])
        deps = tuple(dep for dep in program.capability_dependencies if dep != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        malformed = replace(program, event_condition={"all": program.event_condition["all"][:1]})
        bound = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertTrue(any("runtime_combat_damage_library_access:invalid:" in issue for issue in bound["blockers"]))
        for old, new in (("combat damage", "damage"), ("that player", "each opponent"),
                         ("top two", "top three"), ("end of turn", "end of your next turn"),
                         ("without paying their mana costs", "by paying 2 life")):
            changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status, changed.oracle_text)

    def test_omitted_exile_producer_restores_the_original_residual(self):
        original = self.db.lookup(SOURCE, fuzzy=False)

        def compile_card():
            return compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)

        self.assertEqual("exact", compile_card().status)
        with patch("quorune.compiler.trigger_result_templates._bound_source_combat_result", return_value=None):
            result = compile_card()
            self.assertNotEqual("exact", result.status)
            self.assertTrue(any(row.span.line == 2 for row in result.material_residuals))

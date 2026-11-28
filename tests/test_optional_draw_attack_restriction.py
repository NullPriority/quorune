"""Native proofs for optional draws and player-bound next-turn attack restrictions."""

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
from quorune.maximum_hand_size import effective_maximum_hand_size
from quorune.next_turn_attack_restriction_model import (
    ATTACK_RESTRICTION_CAPABILITY, ATTACK_RESTRICTION_OPERATION, ResolvedNextTurnAttackRestriction,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OptionalDrawAttackRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-draw-attack-restriction.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "cast-participant-optional-opponent-draw-cards.json",
            fixtures / "redirected-draw-cards.json",
            fixtures / "optional-draw-attack-restriction-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "announced-destruction-controller-tokens-cards.json",
            fixtures / "target-player-sequence-cards.json",
            fixtures / "event-spell-copy-cards.json",
            fixtures / "fixed-distributed-token-cards.json",
            fixtures / "draw-step-exempt-multiplier-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional draw attack restriction", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _tempt_game(self, seed, *, replacements=False):
        game = self.session(seed)
        source = self.add(game.engine, "Tempt with Bunnies", zone="hand")
        if replacements:
            for name in ("Teferi's Ageless Insight", "Anointed Procession"):
                self.assert_closed(game, self.add(game.engine, name, seat="B"))
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        libraries = self.libraries(game)
        self.cast(game, "A", source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        return game, libraries

    def _tempt_tokens(self, game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def _tempt_choose(self, game, actor, answer, previous):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        packet = game.packet(f"pilot:{actor}", full=True)["decision"]
        self.assertEqual([{"player": seat, "choice": choice} for seat, choice in previous], packet["ctx"]["previous_choices"])
        result = game.act(f"pilot:{actor}", {"action_id": "choose", "choice": answer})
        self.assertTrue(result.ok, result.summary)

    def test_tempt_public_order_simultaneous_rewards_and_exact_repetitions_replay(self):
        game, libraries = self._tempt_game(2000001, replacements=True)
        after_initial = self.libraries(game)
        self.assertEqual(libraries["A"][:-1], after_initial["A"])
        self.assertEqual([libraries[seat] for seat in "BCD"], [after_initial[seat] for seat in "BCD"])
        self.assertEqual(["A"], [card.controller for card in self._tempt_tokens(game)])
        before = authoritative_state_hash(game.state)
        for actor, body in (("C", {"choice": "accept"}), ("B", {"choice": "accept", "beneficiary": "B"})):
            self.assertFalse(game.act(f"pilot:{actor}", {"action_id": "choose", **body}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self._tempt_choose(game, "B", "accept", ())
        self.assertEqual(after_initial, self.libraries(game))
        game = self.replay(game, load=True)
        self._tempt_choose(game, "C", "decline", (("B", "accept"),))
        self.assertEqual(after_initial, self.libraries(game))
        self.assertEqual(["A"], [card.controller for card in self._tempt_tokens(game)])
        self._tempt_choose(game, "D", "accept", (("B", "accept"), ("C", "decline")))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"A": 3, "B": 2, "C": 0, "D": 1},
                         {seat: len(libraries[seat]) - len(current) for seat, current in self.libraries(game).items()})
        tokens = self._tempt_tokens(game)
        self.assertEqual({"A": 3, "B": 2, "C": 0, "D": 1},
                         {seat: sum(card.controller == seat for card in tokens) for seat in "ABCD"})
        self.assertEqual(1, len({card.zone_timestamp for card in tokens if card.controller in {"B", "D"}}))
        self.assertEqual(3, len({card.zone_timestamp for card in tokens if card.controller == "A"}))
        for token in tokens:
            data = game.engine._effective_card_data(token)
            self.assertEqual("Rabbit Token", data["name"])
            self.assertEqual(["W"], data["colors"])
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, f) for f in ("power", "toughness")))
        self.replay(game, load=True)

    def test_tempt_all_declines_leave_only_the_original_pair(self):
        game, libraries = self._tempt_game(2000002)
        previous = []
        for seat in "BCD":
            self._tempt_choose(game, seat, "decline", previous)
            previous.append((seat, "decline"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({seat: libraries[seat][:-1] if seat == "A" else libraries[seat] for seat in "ABCD"},
                         self.libraries(game))
        self.assertEqual(["A"], [card.controller for card in self._tempt_tokens(game)])
        self.replay(game, load=True)

    def test_tempt_rejects_missing_ordered_draw_and_authority(self):
        from test_controller_creature_keyword_absence import register_card
        from quorune.rules.tempt_draw_token import CAPABILITY

        report, semantics = register_card(self.db, self.registry, "Tempt with Bunnies")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Tempt with Bunnies", fuzzy=False)
        program, = semantics.programs_for_oracle(record.oracle_id)
        first, token, offer = program.effects
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        for invalid in (
            replace(program, effects=(token, first, offer)),
            replace(program, effects=(first, token, {**offer, "beneficiary": "$context.player"})),
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile="commander_review").to_dict()),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                invalid, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def begin_end_step_draws(self, game, source):
        self.until(game, lambda g: g.state.step == "end_step" and any(
            item.source_object_id == source.object_id for item in g.state.stack
        ))
        self.assertEqual("semantic.choice", self.resolve(game).kind)

    def choose_draw(self, game, seat, answer, previous):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", [seat]), (decision.kind, decision.actors))
        packet = game.packet(f"pilot:{seat}", full=True)["decision"]
        self.assertEqual(seat, packet["ctx"]["drawer"])
        self.assertEqual(
            [{"player": player, "choice": choice} for player, choice in previous],
            packet["ctx"]["previous_choices"],
        )
        self.assertIsNone(game.packet("spectator", full=True)["decision"])
        result = game.act(f"pilot:{seat}", {"action_id": "choose", "choice": answer})
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def libraries(game):
        return {seat: tuple(player.zones["library"]) for seat, player in game.state.players.items()}

    @staticmethod
    def restrictions(game):
        return {
            effect.restricted_player: (effect.protected_player, effect.own_turn)
            for effect in game.state.continuous_effects
            if isinstance(effect, ResolvedNextTurnAttackRestriction)
        }

    def prepare_defenders(self, game):
        engine = game.engine
        walker = self.add(engine, "Generic Protected Walker", ref="doctor-protected-walker")
        walker.counters["loyalty"] = 4
        protected = self.add(engine, "Generic Protected Battle", ref="doctor-protected-battle")
        protected.counters["defense"] = 4
        protected.battle_protector = "C"
        other = self.add(engine, "Generic Protected Battle", seat="C", ref="doctor-unprotected-battle")
        other.counters["defense"] = 4
        other.battle_protector = "A"
        return walker, protected, other

    def expect_attack_targets(self, game, card, *, legal, barred):
        actor = game.state.active_player
        self.assertEqual("combat.attackers", game.state.pending_decision.kind)
        packet = game.packet(f"pilot:{actor}", full=True)["decision"]
        offers = packet["ctx"]["declaration_constraints"]["domains"][card.ref]
        self.assertTrue(set(legal) <= set(offers), offers)
        self.assertTrue(set(barred).isdisjoint(offers), offers)
        attack, = [action for action in packet["legal_actions"] if action["action"] == "attack"]
        assignment, = [field for field in attack["form"]["fields"] if field["name"] == "attackers"]
        row, = [row for row in assignment["rows"] if row["value"] == card.ref]
        choices = {option["value"] for option in row["options"]}
        self.assertTrue(set(legal) <= choices, choices)
        self.assertTrue(set(barred).isdisjoint(choices), choices)

    def _palantir(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Palant\u00edr of Orthanc", ref="palantir-source")
        self.assert_closed(game, source)
        return game, source

    def _palantir_target(self, game, opponent="B"):
        self.target(game, "A", [opponent])

    def _palantir_scry(self, game, cards, *, reload=False):
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        a_packet = json.dumps(game.packet("pilot:A", full=True))
        for card in cards:
            self.assertIn(card.ref, a_packet)
            for principal in ("pilot:B", "pilot:C", "spectator"):
                self.assertNotIn(card.ref, json.dumps(game.packet(principal, full=True)))
        if reload:
            game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose",
                                      "cards": {"top": [card.ref for card in cards], "bottom": []}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["pilot:B"], game.pending_principals())
        return game

    def _palantir_answer(self, game, choice):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "choice": choice}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)

    def test_palantir_uses_the_post_replacement_influence_count_and_public_milled_mana_values(self):
        game, source = self._palantir(61012401)
        self.add(game.engine, "Doubling Season")
        self.add(game.engine, "Rest in Peace")
        self.add(game.engine, "Leyline of the Void", seat="B")
        third = self.add(game.engine, "Grizzly Bears", zone="library", ref="palantir-unmilled")
        second = self.add(game.engine, "Darksteel Myr", zone="library", ref="palantir-mana-three")
        top = self.add(game.engine, "Doubling Season", zone="library", ref="palantir-mana-five")
        self.prepare(game)
        self.checkpoint(game)
        initial = game.state.players["B"].life
        self._palantir_target(game)
        game = self._palantir_scry(game, (top, second), reload=True)
        self.assertEqual(2, game.state.cards[source.object_id].counters["influence"])
        game = self.replay(game, load=True)
        self._palantir_answer(game, "decline")
        for _ in range(4):
            pending = self.resolve(game)
            if pending is None:
                break
            self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
            self.assertEqual(initial, game.state.players["B"].life)
            game = self.replay(game, load=True)
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            result = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(initial - 8, game.state.players["B"].life)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in (top, second)))
        self.assertEqual("library", game.state.cards[third.object_id].zone)
        self.replay(game, load=True)

    def test_palantir_uses_departed_source_counters_and_skips_the_placement(self):
        game, source = self._palantir(61012402)
        source.counters["influence"] = 1
        bastion = self.add(game.engine, "Karn's Bastion")
        for i in range(4):
            self.add(game.engine, "Generic Bound Plains", ref=f"palantir-a-bastion-land-{i}")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        second = self.add(game.engine, "Darksteel Myr", zone="library", ref="palantir-lki-second")
        top = self.add(game.engine, "Grizzly Bears", zone="library", ref="palantir-lki-top")
        self.prepare(game)
        for i in range(2):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"palantir-b-removal-land-{i}")
        self.checkpoint(game)
        initial = game.state.players["B"].life
        self._palantir_target(game)
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in rows if row["id"].startswith(f"activate:{bastion.ref}:")
                   and "proliferate" in row["label"].casefold())
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "objects": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, game.state.cards[source.object_id].counters["influence"])
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        game = self._palantir_scry(game, (top, second))
        self._palantir_answer(game, "decline")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial - 5, game.state.players["B"].life)
        self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[second.object_id].zone)
        self.replay(game, load=True)

    def test_palantir_draw_branch_and_illegal_target_leave_mill_unused(self):
        for illegal in (False, True):
            with self.subTest(illegal=illegal):
                game, source = self._palantir(61012403 + illegal)
                second = self.add(game.engine, "Darksteel Myr", zone="library", ref="palantir-draw-second")
                top = self.add(game.engine, "Grizzly Bears", zone="library", ref="palantir-draw-top")
                if illegal:
                    self.add(game.engine, "Vedalken Orrery", seat="B")
                    leyline = self.add(game.engine, "Leyline of Sanctity", seat="B", zone="hand")
                    for i in range(4):
                        self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"palantir-b-leyline-land-{i}")
                self.prepare(game)
                self.checkpoint(game)
                initial = game.state.players["B"].life
                self._palantir_target(game)
                if illegal:
                    self.cast(game, "B", leyline, [])
                    self.resolve_top(game)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual({}, game.state.cards[source.object_id].counters)
                    self.assertEqual("library", game.state.cards[top.object_id].zone)
                else:
                    game = self._palantir_scry(game, (top, second))
                    self._palantir_answer(game, "draw")
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(1, game.state.cards[source.object_id].counters["influence"])
                    self.assertEqual("hand", game.state.cards[top.object_id].zone)
                self.assertEqual(initial, game.state.players["B"].life)
                self.assertEqual("library", game.state.cards[second.object_id].zone)
                self.replay(game, load=True)

    def test_palantir_only_announced_opponent_departing_skips_the_whole_trigger(self):
        game, source = self._palantir(61012411)
        card = self.add(game.engine, "Grizzly Bears", zone="library", ref="palantir-target-departed")
        self.prepare(game)
        self.checkpoint(game)
        self._palantir_target(game)
        game = self.replay(game, load=True)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual({}, game.state.cards[source.object_id].counters)
        self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_palantir_compiler_requires_the_whole_targeted_end_step_ability(self):
        from quorune.rules.source_counter_mill_choice import END_STEP_MILL_CAPABILITY

        record = self.db.lookup("Palant\u00edr of Orthanc", fuzzy=False)
        def witness():
            ir = compile_oracle_card(record, capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual(("counter", "scry", "offer_draw"), tuple(effect["op"] for effect in node.effects))
            self.assertEqual("opponent", node.target_schema["player_relation"])
            self.assertIn(END_STEP_MILL_CAPABILITY, node.capability_dependencies)
        witness()
        with patch("quorune.compiler.source_counter_mill_templates.source_counter_mill_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()

    def test_native_apnap_draws_and_each_opponents_next_attack_turn(self):
        game = self.session(61012101)
        doctor = self.add(game.engine, "The Second Doctor", seat="C", ref="doctor-original-source")
        game.engine.change_control(doctor.object_id, "A", reason="Pre-action borrowed source fixture")
        self.assert_closed(game, doctor)
        attacker_b = self.add(game.engine, "Generic Bound Body", seat="B", ref="doctor-b-attacker")
        attacker_d = self.add(game.engine, "Generic Bound Body", seat="D", ref="doctor-d-attacker")
        walker, protected, other = self.prepare_defenders(game)
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="library", ref="doctor-private-draw")
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual([None] * 4, [effective_maximum_hand_size(game.engine, seat) for seat in "ABCD"])
        library_before = self.libraries(game)
        next_turns = {seat: game.state.players[seat].turns_begun + 1 for seat in "BD"}
        self.begin_end_step_draws(game, doctor)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "draw"}).ok)
        self.assertFalse(game.act("pilot:A", {
            "action_id": "choose", "choice": "draw", "protected_player": "C",
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        answered = []
        for seat, answer in (("A", "draw"), ("B", "draw"), ("C", "decline")):
            self.choose_draw(game, seat, answer, answered)
            answered.append((seat, answer))
            self.assertEqual(library_before, self.libraries(game))
            self.assertEqual({}, self.restrictions(game))
        game = self.replay(game, load=True)
        offset = len(game.state.events)
        self.choose_draw(game, "D", "draw", answered)
        self.assertEqual(["A", "B", "D"], [event.actor for event in game.state.events[offset:]
                                           if event.code == "card.draw"][:3])
        self.assertEqual({seat: ("A", own_turn) for seat, own_turn in next_turns.items()}, self.restrictions(game))
        self.assertIn(private.object_id, game.state.players["D"].zones["hand"])
        self.assertIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertTrue(private.ref not in json.dumps(game.packet(viewer, full=True)), viewer)

        self.until(game, lambda g: g.state.active_player == "B" and g.state.pending_decision.kind == "combat.attackers")
        barred = ("A", walker.ref, protected.ref)
        allowed = ("C", "D", other.ref)
        self.expect_attack_targets(game, attacker_b, legal=allowed, barred=barred)
        before = authoritative_state_hash(game.state)
        for target in barred:
            rejected = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker_b.ref: target}})
            self.assertFalse(rejected.ok, target)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.expect_attack_targets(game, attacker_b, legal=allowed, barred=barred)
        with patch.object(ResolvedNextTurnAttackRestriction, "applies", return_value=False):
            problem, _, _ = game.engine._attack_declaration_components("B")
            self.assertIn("A", problem.domains[attacker_b.ref], "Removing the duration predicate defeats the offer")
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker_b.ref: other.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(other.ref, game.state.cards[attacker_b.object_id].attacking)
        self.until(game, lambda g: g.state.active_player == "D" and g.state.pending_decision.kind == "combat.attackers")
        self.assertEqual({"D": ("A", next_turns["D"])}, self.restrictions(game))
        self.expect_attack_targets(game, attacker_d, legal=allowed[:1], barred=barred)
        self.until(game, lambda g: g.state.active_player == "A")
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)

    def test_redirected_empty_library_draw_restricts_the_chooser_not_the_recipient(self):
        game = self.session(61012102)
        doctor = self.add(game.engine, "The Second Doctor")
        thief = self.add(game.engine, "Notion Thief", seat="C")
        self.assert_closed(game, doctor)
        self.assert_closed(game, thief)
        for object_id in tuple(game.state.players["B"].zones["library"]):
            game.engine.move_card(object_id, "outside", log=False, reason="Initial empty-library fixture")
        private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="library", ref="doctor-replacement-card")
        self.prepare(game)
        self.checkpoint(game)
        self.begin_end_step_draws(game, doctor)
        answered = []
        for seat, answer in (("A", "decline"), ("B", "draw"), ("C", "decline")):
            self.choose_draw(game, seat, answer, answered)
            answered.append((seat, answer))
        game = self.replay(game, load=True)
        self.choose_draw(game, "D", "decline", answered)
        self.assertIn("B", game.engine.active_seats)
        self.assertFalse(game.state.players["B"].attempted_empty_draw)
        self.assertIn(private.object_id, game.state.players["C"].zones["hand"])
        self.assertEqual({"B": ("A", game.state.players["B"].turns_begun + 1)}, self.restrictions(game))
        self.replay(game, load=True)

    def test_prohibited_draw_is_skipped_and_raw_roles_cannot_be_forged(self):
        game = self.session(61012103)
        doctor = self.add(game.engine, "The Second Doctor")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="D")
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        for source in (doctor, limit, spell):
            self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        self.begin_end_step_draws(game, doctor)
        answered = []
        for seat in "BCD":
            self.choose_draw(game, seat, "decline", answered)
            answered.append((seat, "decline"))
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)

        card = self.db.lookup("The Second Doctor", fuzzy=False)
        trigger, = [program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "step.begin"]
        self.assertIn(ATTACK_RESTRICTION_CAPABILITY, trigger.capability_dependencies)
        effect, = trigger.effects
        for changed in (
            replace(trigger, event_condition=None),
            replace(trigger, effects=({**effect, "protected_player": "$context.player"},)),
            replace(trigger, effects=({**effect, "table_draw_choices": ["draw"]},)),
            replace(trigger, effects=({
                "op": ATTACK_RESTRICTION_OPERATION, "schema_version": 1,
                "protected_player": "$controller", "opponents": ["B"],
            },)),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)
        for wrong in (
            card.oracle_text.replace("your end step", "each end step"),
            card.oracle_text.replace("draw a card", "draw two cards"),
            card.oracle_text.replace("their next turn", "this turn"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=wrong), capability_registry=self.registry,
            ).status)

    def test_arbiter_cannot_supply_the_reserved_offer_or_followup(self):
        game = self.session(61012104)
        unresolved = self.add(game.engine, "Generic Raw Resolution Witness", zone="hand")
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", unresolved)
        self.assertEqual("arbiter.resolve", self.resolve(game).kind)

        restriction = {"op": ATTACK_RESTRICTION_OPERATION, "schema_version": 1,
                       "protected_player": "A", "opponents": ["B"]}
        offer = {"op": "offer_draw", "event_optional_draw_version": 6,
                 "protected_player": "A", "count": 1, "private": True}
        staged = {**offer, "player": "D", "table_draw_order": list("ABCD"),
                  "table_draw_choices": ["decline", "draw", "decline"]}

        def wrapped(effect):
            return {"op": "offer_optional_effect", "player": "A", "effects": [effect]}

        before = authoritative_state_hash(game.state)
        for effects in (restriction, offer, staged, wrapped(restriction), wrapped(staged)):
            with self.subTest(op=effects["op"]):
                result = game.act("arbiter", {"action_id": "resolve", "effects": [effects]})
                self.assertFalse(result.ok)
                self.assertIn("server-issued optional draw choices", result.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
        for symbolic in (
            {**offer, "event_optional_draw_version": "$context.version"},
            wrapped({**restriction, "op": "$context.operation"}),
        ):
            result = game.act("arbiter", {"action_id": "resolve", "effects": [symbolic]})
            self.assertFalse(result.ok)
            self.assertIn("must be literal", result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        rejected = game.act("arbiter", {"action_id": "register_and_resolve", "effects": [offer]})
        self.assertFalse(rejected.ok)
        self.assertIn("server-issued optional draw choices", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual({}, self.restrictions(game))

        library = self.libraries(game)["A"]
        result = game.act("arbiter", {"action_id": "resolve", "effects": [
            {"op": "offer_draw", "player": "$controller", "count": 1, "private": True},
        ]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(library[:-1], self.libraries(game)["A"])
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)


class ApnapCounterAttackTruceTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Counter Payment Test Advisor"
    prepare_defenders = OptionalDrawAttackRestrictionTests.prepare_defenders
    expect_attack_targets = OptionalDrawAttackRestrictionTests.expect_attack_targets

    @classmethod
    def source_fixture(cls):
        return {"cards": [{
            "name": cls.source_name, "oracle_id": "7b404a8a-7d92-428e-9b33-3c750c05a193",
            "type_line": "Creature — Advisor", "mana_cost": "{2}{W}", "cmc": 3,
            "color_identity": ["W"], "power": "1", "toughness": "4",
            "oracle_text": "At the beginning of your upkeep, each player may put two +1/+1 counters on "
                           "a creature they control. If a player does, creatures that player controls "
                           "can't attack you or planeswalkers you control until your next turn.",
        }]}

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "apnap-counter-truce.sqlite3"
        fixture = path.with_suffix(".json")
        fixture.write_text(json.dumps({**cls.source_fixture(), "schema_version": 1}), encoding="utf-8")
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "optional-draw-attack-restriction-cards.json",
            fixtures / "counter-prohibition-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "counter-doubling-cards.json",
            fixtures / "counter-placement-event-cards.json",
            fixture,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("APNAP counter payment", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def begin_upkeep(self, game, source):
        from quorune.engine import TURN_STEPS

        engine = game.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "beginning"
        engine.state.step = "upkeep"
        engine.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()
        triggers = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(1, len(triggers))
        self.assertEqual([], triggers[0].targets)
        self.checkpoint(game)

    def choose(self, game, seat, refs, prior):
        decision = game.packet(f"pilot:{seat}", full=True)["decision"]
        self.assertEqual("choice.apnap", game.state.pending_decision.kind)
        self.assertEqual([seat], game.state.pending_decision.actors)
        self.assertEqual(prior, decision["ctx"]["prior_public_choices"])
        self.assertEqual(0, decision["ctx"]["minimum"])
        self.assertIsNone(game.packet("spectator", full=True)["decision"])
        result = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": refs})
        self.assertTrue(result.ok, result.summary)

    def counter(self, game, card):
        return game.state.cards[card.object_id].counters.get("+1/+1", 0)

    def test_apnap_counter_truce_has_exact_scope_and_rejects_raw_results(self):
        from quorune.errors import GameRuleError
        from quorune.rules.apnap_counter_attack_truce import COMMIT, FIELD, counter_attack_truce_program_dependencies
        from quorune.stack_resolution import arbiter_resolution_effects

        game = self.session(101419)
        source = self.add(game.engine, self.source_name)
        self.assert_closed(game, source)
        card = self.db.lookup(self.source_name)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        program, = [value for value in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if value.event == "step.begin"]
        effect, = program.effects
        self.assertTrue(counter_attack_truce_program_dependencies(program))
        for modified in (
            replace(program, event_condition=None),
            replace(program, effects=({**effect, FIELD: True},)),
            replace(program, effects=({**effect, "players": "opponents"},)),
            replace(program, effects=({**effect, "op": COMMIT, "placements": []},)),
        ):
            result = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)
        for raw in (effect, {"op": COMMIT, "placements": []}):
            with self.assertRaisesRegex(GameRuleError, "server-issued APNAP counter choices"):
                arbiter_resolution_effects([raw])

    def test_apnap_counter_truce_choices_replacement_duration_and_replay(self):
        game = self.session(101420)
        for seat in "ABCD":
            for object_id in game.state.players[seat].zones["hand"][:3]:
                game.engine.move_card(object_id, "library", log=False, reason="Initial hand fixture")
        source = self.add(game.engine, self.source_name)
        first = self.add(game.engine, "Generic Bound Body", ref="truce-a-body")
        second = self.add(game.engine, "Generic Bound Body", seat="B", ref="truce-b-body")
        later = self.add(game.engine, "Generic Bound Body", seat="B", zone="hand", ref="truce-b-later")
        self.add(game.engine, "Generic Bound Body", seat="C", ref="truce-c-body")
        self.add(game.engine, "Hardened Scales", seat="B")
        self.add(game.engine, "Branching Evolution", seat="B")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="truce-b-mana")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        secret = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="truce-private-card")
        walker, _, battle = self.prepare_defenders(game)
        self.assert_closed(game, source)
        game.engine.state.players["B"].mana_pool.update({"U": 1})
        game.engine.turn_steps.schedule_extra_turn("B", source="Counter payment fixture")
        self.begin_upkeep(game, source)
        self.priority(game, "B")
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("choice.apnap", self.resolve(game).kind)

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [second.ref]}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": [second.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose(game, "A", [first.ref], {})
        self.assertEqual(0, self.counter(game, first))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertIn(secret.ref, json.dumps(game.packet("pilot:D", full=True)))
        game = self.replay(game, load=True)
        self.choose(game, "B", [second.ref], {"A": [first.ref]})
        self.assertEqual((0, 0), (self.counter(game, first), self.counter(game, second)))
        self.choose(game, "C", [], {"A": [first.ref], "B": [second.ref]})
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual((0, 0), (self.counter(game, first), self.counter(game, second)))
        game = self.replay(game, load=True)
        offset = len(game.state.events)
        while game.state.pending_decision.kind == "replacement.order":
            principal = game.pending_principals()[0]
            option = game.packet(principal, full=True)["decision"]["ctx"]["options"][0]["id"]
            result = game.act(principal, {"action_id": "choose", "replacement": option})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, self.counter(game, first))
        self.assertIn(self.counter(game, second), {5, 6})
        self.assertEqual(["A", "B"], [event.actor for event in game.state.events[offset:]
                                      if event.code == "counter.add"])
        restriction, = [effect for effect in game.state.continuous_effects
                        if isinstance(effect, ResolvedNextTurnAttackRestriction)]
        self.assertEqual(("B", "A", "A"), (restriction.restricted_player, restriction.protected_player,
                                             restriction.expires_on_next_turn_of))

        first_turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.active_player == "B" and
                   current.state.phase == "precombat_main" and current.state.pending_decision.kind == "priority")
        self.cast_for(game, "B", later)
        self.resolve_top(game)
        for turn in range(2):
            self.until(game, lambda current: current.state.active_player == "B" and
                       current.state.turn_sequence > first_turn and current.state.pending_decision.kind == "combat.attackers")
            first_turn = game.state.turn_sequence
            self.expect_attack_targets(game, second, legal=("C", "D", battle.ref), barred=("A", walker.ref))
            if turn == 1:
                self.expect_attack_targets(game, later, legal=("C", "D", battle.ref), barred=("A", walker.ref))
            before = authoritative_state_hash(game.state)
            for target in ("A", walker.ref):
                result = game.act("pilot:B", {"action_id": "attack", "attackers": {second.ref: target}})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
            result = game.act("pilot:B", {"action_id": "attack", "attackers": {second.ref: "C"}})
            self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: current.state.active_player == "A")
        self.assertFalse(any(isinstance(effect, ResolvedNextTurnAttackRestriction) for effect in game.state.continuous_effects))
        self.assertEqual("battlefield", game.state.cards[later.object_id].zone)
        self.replay(game, load=True)

    def test_apnap_counter_truce_prohibited_choices_are_skipped(self):
        game = self.session(101421)
        source = self.add(game.engine, self.source_name)
        self.add(game.engine, "Solemnity")
        self.add(game.engine, "Generic Bound Body", seat="B")
        self.begin_upkeep(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(isinstance(effect, ResolvedNextTurnAttackRestriction) for effect in game.state.continuous_effects))
        self.replay(game)

    def test_apnap_counter_truce_attack_commit_mutation_is_killed(self):
        with patch("quorune.rules.apnap_counter_attack_truce.apply_until_protected_turn_attack_restrictions"):
            with self.assertRaises((AssertionError, ValueError)):
                self.test_apnap_counter_truce_choices_replacement_duration_and_replay()

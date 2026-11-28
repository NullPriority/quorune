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
from quorune.engine import TURN_STEPS
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_player_targets import DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY
from scripts.build_test_database import build_fixture_database


CAP = DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY


class DamagedPlayerGraveyardTargetsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "damaged-player-graveyard.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "damaged-player-graveyard-target-cards",
            "fixed-public-zone-move-cards", "activation-public-state-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damaged player's graveyard", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def setup_combat(self, seed, *, cards_in_damage_graveyard=True):
        game = self.session(seed)
        ninja = self.add(game.engine, "Skullsnatcher", zone="hand")
        attacker = self.add(game.engine, "Generic Bound Body", ref="ninjutsu-unblocked-attacker")
        candidate_a = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="damaged-grave-a") if cards_in_damage_graveyard else None
        candidate_b = self.add(game.engine, "Murder", seat="B", zone="graveyard", ref="damaged-grave-b") if cards_in_damage_graveyard else None
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard", ref="foreign-grave")
        hidden = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="hidden-grave-decoy")
        exile = self.add(game.engine, "Public Grave Exile", seat="C", zone="hand")
        self.add(game.engine, "Swamp", seat="C")
        self.assert_closed(game, ninja)
        attacker.attacking = "B"
        game.state.players["A"].mana_pool["B"] = 1
        game.state.active_player = "A"
        game.state.started = True
        game.state.phase = "combat"
        game.state.step = "declare_blockers"
        game.state.phase_index = TURN_STEPS.index(("combat", "declare_blockers"))
        game.state.combat = CombatState(
            attackers_declared=True, blockers_declared=True, had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            attack_target_context={attacker.object_id: {"target":"B", "kind":"player", "defending_player":"B"}},
            defending_players=["B"],
        )
        game.engine.permissions.invalidate_current()
        game.state.priority_player = None
        game.state.pending_decision = None
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)
        return game, ninja, attacker, (candidate_a, candidate_b), foreign, hidden, exile

    def trigger_from_normal_damage(self, game, ninja, attacker):
        packet = game.packet("pilot:A", full=True)
        action, = (row for row in packet["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{ninja.ref}:"))
        self.assertEqual([attacker.ref], action["cost_summary"]["choose_cost"][0]["legal_refs"])
        self.assertNotIn(ninja.ref, json.dumps(game.packet("pilot:B", full=True)))
        result = game.act("pilot:A", {"action_id": action["id"], "cost_objects": [attacker.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(("hand", "B", True), (game.state.cards[attacker.object_id].zone,
                                               game.state.cards[ninja.object_id].attacking,
                                               game.state.cards[ninja.object_id].tapped))
        life = game.state.players["B"].life
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "semantic.target")
        self.assertLess(game.state.players["B"].life, life)
        self.assertEqual("B", game.state.stack[-1].context["target"])

    def test_actual_ninjutsu_exiles_only_two_distinct_damaged_player_targets_and_replays(self):
        game, source, attacker, candidates, foreign, hidden, _ = self.setup_combat(7024921)
        self.trigger_from_normal_damage(game, source, attacker)
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertNotIn(foreign.ref, json.dumps(schema))
        self.assertNotIn(hidden.ref, json.dumps(schema))
        before = authoritative_state_hash(game.state)
        for seat, refs in (("B", [candidates[0].ref]), ("A", [foreign.ref]), ("A", [candidates[0].ref] * 2)):
            failure = game.act(f"pilot:{seat}", {"action_id": "choose", "targets": refs})
            self.assertFalse(failure.ok, failure.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [card.ref for card in candidates]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in candidates))
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        self.replay(game, load=True)

    def test_actual_zero_and_partially_stale_target_sets_replay(self):
        for zero in (False, True):
            with self.subTest(zero=zero):
                game, source, attacker, candidates, foreign, _, exile = self.setup_combat(7024930 + zero, cards_in_damage_graveyard=not zero)
                self.trigger_from_normal_damage(game, source, attacker)
                refs = [] if zero else [card.ref for card in candidates]
                result = game.act("pilot:A", {"action_id": "choose", "targets": refs})
                self.assertTrue(result.ok, result.summary)
                if not zero:
                    original = candidates[0]
                    self.cast(game, "C", exile, [original.ref])
                    self.resolve_top(game)
                    stale_counter = game.state.cards[original.object_id].zone_change_counter
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
                if not zero:
                    self.assertEqual(stale_counter, game.state.cards[original.object_id].zone_change_counter)
                    self.assertEqual("exile", game.state.cards[candidates[1].object_id].zone)
                self.replay(game, load=True)

    def test_printed_effect_requires_the_same_damage_player_at_both_boundaries(self):
        record = self.db.lookup("Skullsnatcher", fuzzy=False)
        result = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = (node for face in result.faces for node in face.nodes if CAP in node.capability_dependencies)
        self.assertEqual("$context.target", node.target_schema["owner_seat"])
        self.assertEqual(2, node.target_schema["up_to"])
        game, source, *_ = self.setup_combat(7024910)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in program.capability_dependencies)
        changed = replace(program, target_schema={key:value for key,value in program.target_schema.items() if key != "owner_seat"})
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile="commander_review")["strict"])

    def test_omitting_the_bound_graveyard_owner_is_killed(self):
        from quorune.compiler import event_player_target_results as owner

        original = owner.event_player_target_result

        def without_owner(*args, **kwargs):
            outcome = original(*args, **kwargs)
            if outcome is None:
                return None
            (label, effects, schema, mechanics), source = outcome
            return (label, effects, {key:value for key,value in schema.items() if key != "owner_seat"}, mechanics), source

        with patch.object(owner, "event_player_target_result", without_owner):
            with self.assertRaises((AssertionError, StopIteration, ValueError)):
                self.test_printed_effect_requires_the_same_damage_player_at_both_boundaries()

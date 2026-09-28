from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from declaration_support import compiled_declaration_fragments
from quorune.declaration_costs import normalized_oracle_line
from quorune.declaration_restrictions import (
    parse_declaration_restriction_line,
)
from quorune.engine import StateInvariantError
from quorune.model import CombatState, GameState, TurnEntry
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


class CurrentTurnHistoryRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 3):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def make_combat_session(self, seed: int, *, players: int = 3):
        session = self.make_session(seed, players=players)
        engine = session.engine
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        return session

    @staticmethod
    def creature(
        engine,
        seat: str,
        name: str,
        *,
        oracle_text: str = "",
        keywords: tuple[str, ...] = ("Haste",),
    ):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "oracle_text": oracle_text,
                "ability_fragments": compiled_declaration_fragments(
                    name,
                    oracle_text,
                ),
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=keywords,
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def static_source(engine, seat: str, name: str, oracle_text: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Enchantment",
                "oracle_text": oracle_text,
                "ability_fragments": compiled_declaration_fragments(
                    name,
                    oracle_text,
                ),
            },
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def planeswalker(engine, seat: str, name: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "oracle_text": "",
                "loyalty": "3",
            },
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def deck_card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_parser_exactly_lowers_current_turn_declaration_families(self):
        cases = {
            "This creature can't attack unless you've cast a creature spell this turn.": (
                "intrinsic-cast-creature-spell-this-turn-attack-unless-v1",
                "cast_creature_spell",
                ("attack",),
            ),
            "This creature can't attack unless you've cast a noncreature spell this turn.": (
                "intrinsic-cast-noncreature-spell-this-turn-attack-unless-v1",
                "cast_noncreature_spell",
                ("attack",),
            ),
            "This creature can't attack unless an opponent has been dealt damage this turn.": (
                "intrinsic-opponent-damaged-this-turn-attack-unless-v1",
                "opponent_dealt_damage",
                ("attack",),
            ),
            "Bontu can't attack or block unless a creature died under your control this turn.": (
                "intrinsic-controlled-creature-died-this-turn-attack-block-unless-v1",
                "creature_died_under_control",
                ("attack", "block"),
            ),
            "This creature can't attack a player it has already attacked this turn.": (
                "intrinsic-already-attacked-player-this-turn-v1",
                "attacked_player",
                ("attack",),
            ),
            "Each opponent who cast a spell this turn can't attack with creatures.": (
                "source-opponents-cast-spell-this-turn-attack-v1",
                "cast_spell",
                ("attack",),
            ),
        }
        for text, (template_id, fact, declarations) in cases.items():
            with self.subTest(text=text):
                parsed = parse_declaration_restriction_line(
                    text,
                    card_name=(
                        "Bontu the Glorified"
                        if text.startswith("Bontu")
                        else ""
                    ),
                )
                self.assertTrue(parsed.exact)
                self.assertEqual(template_id, parsed.template.template_id)
                self.assertEqual(fact, parsed.template.condition.fact)
                self.assertEqual(declarations, parsed.declarations)
        self.assertEqual(
            "goblin spells you cast cost {1} less to cast.",
            normalized_oracle_line(
                "Goblin spells you cast cost {1} less to cast.",
                card_name="Goblin Warchief",
            ),
        )

    def test_turn_history_is_serialized_hashed_and_legacy_absence_stays_absent(self):
        session = self.make_session(608020101, players=2)
        engine = session.engine
        before = authoritative_state_hash(engine.state)
        engine._record_turn_history(
            "player_damaged",
            actor="A",
            target="B",
            target_kind="player",
            amount=1,
        )
        self.assertNotEqual(before, authoritative_state_hash(engine.state))
        payload = engine.state.to_dict()
        self.assertEqual(1, payload["turn_history"]["schema_version"])
        restored = GameState.from_dict(payload)
        self.assertEqual(payload["turn_history"], restored.to_dict()["turn_history"])

        legacy_payload = copy.deepcopy(payload)
        legacy_payload.pop("turn_history")
        legacy = GameState.from_dict(legacy_payload)
        self.assertIsNone(legacy.turn_history)
        self.assertNotIn("turn_history", legacy.to_dict())
        self.assertEqual(
            authoritative_state_hash(legacy_payload),
            authoritative_state_hash(legacy),
        )

    def test_empty_history_can_initialize_lazily_but_facts_cannot_cross_turns(self):
        session = self.make_session(608020102, players=2)
        engine = session.engine
        engine.state.turn_sequence += 1
        engine._assert_invariants()

        engine._record_turn_history(
            "player_damaged",
            actor="A",
            target="B",
            target_kind="player",
            amount=1,
        )
        self.assertEqual(
            engine.state.turn_sequence,
            engine.state.turn_history.turn_sequence,
        )
        engine.state.turn_sequence += 1
        with self.assertRaises(StateInvariantError):
            engine._assert_invariants()

    def test_beginning_a_new_turn_resets_history_but_extra_combats_do_not(self):
        session = self.make_session(608020102, players=2)
        engine = session.engine
        engine._record_turn_history(
            "spell_cast",
            actor="A",
            types={"creature"},
        )
        same_turn = engine.state.turn_sequence
        engine.state.combat = CombatState()
        self.assertEqual(same_turn, engine.state.turn_history.turn_sequence)
        self.assertEqual(1, len(engine.state.turn_history.events))

        engine._begin_turn(
            TurnEntry(
                turn_id="history-reset",
                player="B",
                created_sequence=same_turn,
            )
        )
        self.assertEqual(engine.state.turn_sequence, engine.state.turn_history.turn_sequence)
        self.assertEqual([], engine.state.turn_history.events)

    def test_cast_writer_preserves_types_and_mixed_creature_is_not_noncreature(self):
        noncreature_session = self.make_session(608020103, players=2)
        engine = noncreature_session.engine
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        ring = self.deck_card(engine, "A", "Sol Ring")
        engine.move_card(ring.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        engine._cast("A", {"card": ring.ref, "pay": "auto"})
        self.assertTrue(engine._player_cast_spell_this_turn("A", creature=False))
        self.assertFalse(engine._player_cast_spell_this_turn("A", creature=True))

        creature_session = self.make_session(608020104, players=2)
        engine = creature_session.engine
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        brudiclad = self.deck_card(engine, "A", "Brudiclad, Telchor Engineer")
        engine.move_card(brudiclad.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update({"C": 4, "U": 1, "R": 1})
        engine._cast("A", {"card": brudiclad.ref, "pay": "auto"})
        event = engine._current_turn_history("spell_cast")[-1]
        self.assertIn("artifact", event.types)
        self.assertIn("creature", event.types)
        self.assertTrue(engine._player_cast_spell_this_turn("A", creature=True))
        self.assertFalse(engine._player_cast_spell_this_turn("A", creature=False))

    def test_creature_and_noncreature_cast_conditions_unlock_independently(self):
        session = self.make_combat_session(608020105, players=2)
        engine = session.engine
        creature_gate = self.creature(
            engine,
            "A",
            "Creature Gate",
            oracle_text=(
                "This creature can't attack unless you've cast a creature "
                "spell this turn."
            ),
        )
        noncreature_gate = self.creature(
            engine,
            "A",
            "Noncreature Gate",
            oracle_text=(
                "This creature can't attack unless you've cast a noncreature "
                "spell this turn."
            ),
        )
        self.assertNotIn(creature_gate.ref, engine._attack_declaration_problem("A").domains)
        self.assertNotIn(noncreature_gate.ref, engine._attack_declaration_problem("A").domains)

        engine._record_turn_history("spell_cast", actor="A", types={"creature"})
        domains = engine._attack_declaration_problem("A").domains
        self.assertIn(creature_gate.ref, domains)
        self.assertNotIn(noncreature_gate.ref, domains)
        engine._record_turn_history("spell_cast", actor="A", types={"instant"})
        domains = engine._attack_declaration_problem("A").domains
        self.assertIn(creature_gate.ref, domains)
        self.assertIn(noncreature_gate.ref, domains)

    def test_only_damage_not_life_loss_or_prevented_damage_unlocks_attack(self):
        session = self.make_combat_session(608020106, players=3)
        engine = session.engine
        goblin = self.creature(
            engine,
            "A",
            "Bloodcrazed Goblin",
            oracle_text=(
                "This creature can't attack unless an opponent has been "
                "dealt damage this turn."
            ),
        )
        engine.apply_effect({"op": "lose_life", "player": "B", "amount": 2}, actor="A")
        self.assertNotIn(goblin.ref, engine._attack_declaration_problem("A").domains)
        engine.state.players["B"].stats[
            "protection_from_everything_until_next_turn"
        ] = True
        engine.apply_effect({"op": "damage", "target": "B", "amount": 2}, actor="A")
        self.assertNotIn(goblin.ref, engine._attack_declaration_problem("A").domains)

        engine.state.players["B"].stats.pop(
            "protection_from_everything_until_next_turn"
        )
        engine.apply_effect({"op": "damage", "target": "B", "amount": 1}, actor="C")
        self.assertEqual(
            ("B", "C"),
            engine._attack_declaration_problem("A").domains[goblin.ref],
        )

    def test_controlled_creature_death_uses_lki_and_survives_token_cleanup(self):
        session = self.make_combat_session(608020107, players=2)
        engine = session.engine
        bontu = self.creature(
            engine,
            "A",
            "Bontu the Glorified",
            oracle_text=(
                "Bontu the Glorified can't attack or block unless a creature "
                "died under your control this turn."
            ),
        )
        opponent_creature = self.creature(engine, "B", "Opponent Victim")
        engine.move_card(
            opponent_creature.object_id,
            "graveyard",
            reason="test death",
            semantic_events=True,
        )
        self.assertNotIn(bontu.ref, engine._attack_declaration_problem("A").domains)
        attacking_creature = self.creature(engine, "B", "Incoming Attacker")
        attacking_creature.attacking = "A"
        engine.state.active_player = "B"
        engine.state.phase_index = 6
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers={attacking_creature.object_id: "A"},
            attackers_declared=True,
            defending_players=["A"],
        )
        self.assertNotIn(
            bontu.ref,
            engine._block_declaration_problem("A").domains,
        )

        victim = self.creature(engine, "A", "Controlled Victim")
        old_identity = victim.logical_object_id
        engine.move_card(
            victim.object_id,
            "graveyard",
            reason="test death",
            semantic_events=True,
        )
        death = engine._current_turn_history("creature_died")[-1]
        self.assertEqual("A", death.actor)
        self.assertEqual(old_identity, death.object_incarnation)
        engine.move_card(victim.object_id, "outside", reason="token cleanup")
        self.assertIn(
            bontu.ref,
            engine._block_declaration_problem("A").domains,
        )
        engine.state.phase_index = 5
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine.state.active_player = "A"
        self.assertIn(bontu.ref, engine._attack_declaration_problem("A").domains)

    def test_same_creature_cannot_attack_same_player_twice_and_replays(self):
        session = self.make_combat_session(608020108, players=3)
        engine = session.engine
        port = self.creature(
            engine,
            "A",
            "Port Razer",
            oracle_text=(
                "This creature can't attack a player it has already attacked "
                "this turn."
            ),
        )
        walker = self.planeswalker(engine, "B", "Target Walker")
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {port.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        attack = engine._current_turn_history("creature_attacked")[-1]
        self.assertEqual("player", attack.target_kind)
        self.assertEqual("B", attack.target)
        self.assertGreaterEqual(
            set(attack.types),
            {"creature", "test"},
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "turn-history-attack"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.combat = CombatState()
        port.tapped = False
        port.attacking = None
        domains = engine._attack_declaration_problem("A").domains[port.ref]
        self.assertNotIn("B", domains)
        self.assertIn("C", domains)
        self.assertIn(walker.ref, domains)

    def test_angelic_arbiter_uses_prior_casts_and_only_restricts_opponents(self):
        session = self.make_combat_session(608020109, players=3)
        engine = session.engine
        attacker = self.creature(engine, "A", "Opponent Attacker")
        engine._record_turn_history("spell_cast", actor="A", types={"instant"})
        arbiter = self.static_source(
            engine,
            "B",
            "Angelic Arbiter",
            "Each opponent who cast a spell this turn can't attack with creatures.",
        )
        self.assertNotIn(attacker.ref, engine._attack_declaration_problem("A").domains)

        controller_attacker = self.creature(engine, "B", "Controller Attacker")
        engine._record_turn_history("spell_cast", actor="B", types={"instant"})
        self.assertIn(
            controller_attacker.ref,
            engine._attack_declaration_problem("B").domains,
        )
        engine.move_card(arbiter.object_id, "graveyard", reason="test removal")
        self.assertIn(attacker.ref, engine._attack_declaration_problem("A").domains)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import keep_all, load_assets, make_session, pass_current
from quorune.counter_placement import (
    CounterPlacementRequest,
    place_counters,
)
from quorune.counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    plan_counter_removals,
)
from quorune.damage import (
    commit_prepared_damage_batch,
    damage_proposal,
    prepare_damage_batch,
)
from quorune.destruction import destroy_permanent_refs
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry


class KeywordCounterCompositionTests(unittest.TestCase):
    """CR 122.1b keyword counters composed with their executable owners."""

    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, step: str):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase = "combat"
        engine.state.step = step
        engine.state.combat = CombatState()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def token(
        engine,
        controller: str,
        name: str,
        *,
        power: int = 2,
        toughness: int = 2,
        colors: tuple[str, ...] = ("G",),
        temporary_keywords: tuple[str, ...] = (),
    ):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": str(power),
                "toughness": str(toughness),
                "keywords": [],
                "colors": list(colors),
            },
            temporary_keywords=temporary_keywords,
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def add_registered_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        self.assertIsNotNone(record, name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        value = CardInstance(
            object_id=f"keyword-assurance:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[value.object_id] = value
        engine.state.players[seat].zones["battlefield"].append(
            value.object_id
        )
        return value

    def prepare_red_elemental_blast(self, session):
        engine = session.engine
        spell = self.card(engine, "A", "Red Elemental Blast")
        engine.move_card(spell.object_id, "hand")
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        action = next(
            value
            for value in session.packet("pilot:A", full=True)["decision"][
                "legal_actions"
            ]
            if value.get("card") == spell.ref
        )
        return spell, action

    @staticmethod
    def place_keyword_counter(engine, card, counter_name: str) -> None:
        results = place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=card.object_id,
                    counter_name=counter_name,
                    amount=1,
                    placing_player=card.controller,
                    source_ref=card.ref,
                ),
            ),
            reason="keyword-counter composition witness",
        )
        if len(results) != 1 or results[0].placed != 1:
            raise AssertionError("Keyword-counter placement did not commit")

    @staticmethod
    def set_combat(engine, attacker, *blockers) -> None:
        attacker.attacking = "B"
        for blocker in blockers:
            blocker.blocking = attacker.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=bool(blockers),
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
            blockers=(
                {attacker.object_id: [card.object_id for card in blockers]}
                if blockers
                else {}
            ),
        )

    def assert_replays(self, session, name: str) -> None:
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / name
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])
        self.assertEqual(len(session.commands), replay["commands"])

    def test_flying_counter_feeds_offer_and_command_block_legality(self):
        session = self.session(122_001_001, step="declare_blockers")
        engine = session.engine
        engine.state.phase_index = 6
        attacker = self.token(engine, "A", "Counter-granted flyer")
        ground = self.token(engine, "B", "Ground blocker")
        reach = self.token(
            engine,
            "B",
            "Reach blocker",
            temporary_keywords=("Reach",),
        )
        self.place_keyword_counter(engine, attacker, "flying")
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )

        engine._begin_blocker_decisions()
        decision = session.packet("pilot:B", full=True)["decision"]
        self.assertNotIn(ground.ref, decision["ctx"]["legal_blocks"])
        self.assertIn(attacker.ref, decision["ctx"]["legal_blocks"][reach.ref])
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:B",
            {"a": "block", "blk": {ground.ref: attacker.ref}},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", {"a": "block", "blk": {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assert_replays(session, "keyword-counter-flying-block")

    def test_vigilance_counter_feeds_four_player_attack_tap_owner(self):
        session = self.session(122_001_002, step="declare_attackers")
        engine = session.engine
        engine.state.phase_index = 5
        attacker = self.token(
            engine,
            "A",
            "Counter-granted vigilant attacker",
            temporary_keywords=("Haste",),
        )
        self.place_keyword_counter(engine, attacker, "vigilance")
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(attacker.tapped)
        self.assertEqual("B", attacker.attacking)
        self.assert_replays(session, "keyword-counter-vigilance-attack")

    def test_double_strike_counter_feeds_both_damage_steps(self):
        session = self.session(122_001_003, step="combat_damage")
        engine = session.engine
        engine.state.phase_index = 7
        attacker = self.token(
            engine,
            "A",
            "Counter-granted double striker",
        )
        self.place_keyword_counter(engine, attacker, "double strike")
        self.set_combat(engine, attacker)

        engine._enter_step()
        self.assertTrue(engine.state.combat.first_strike_step)
        self.assertEqual(38, engine.state.players["B"].life)
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        while engine.state.combat.damage_step_index == 0:
            pass_current(session)

        self.assertEqual(1, engine.state.combat.damage_step_index)
        self.assertEqual(36, engine.state.players["B"].life)
        self.assert_replays(session, "keyword-counter-double-strike")

    def test_lifelink_counter_feeds_final_damage_result(self):
        session = self.session(122_001_004, step="combat_damage")
        engine = session.engine
        engine.state.phase_index = 7
        attacker = self.token(
            engine,
            "A",
            "Counter-granted lifelinker",
            power=3,
            toughness=6,
        )
        first = self.token(engine, "B", "First blocker", power=1)
        second = self.token(engine, "B", "Second blocker", power=1)
        self.place_keyword_counter(engine, attacker, "lifelink")
        self.set_combat(engine, attacker, first, second)
        engine.state.players["A"].life = 20

        engine._begin_combat_damage()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {
                        "source": attacker.ref,
                        "target": first.ref,
                        "amount": 2,
                    },
                    {
                        "source": attacker.ref,
                        "target": second.ref,
                        "amount": 1,
                    },
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(23, engine.state.players["A"].life)
        self.assert_replays(session, "keyword-counter-lifelink-damage")

    def test_indestructible_counter_feeds_canonical_destruction(self):
        session = self.session(122_001_005, step="combat_damage")
        engine = session.engine
        permanent = self.token(
            engine,
            "B",
            "Counter-granted indestructible permanent",
        )
        self.place_keyword_counter(engine, permanent, "indestructible")

        protected = destroy_permanent_refs(
            engine,
            (permanent.ref,),
            actor="A",
            reason="keyword-counter Indestructible witness",
        )

        self.assertEqual(
            (permanent.object_id,),
            protected.indestructible_object_ids,
        )
        self.assertEqual("battlefield", permanent.zone)
        removal = plan_counter_removals(
            engine,
            (
                CounterRemoval(
                    object_id=permanent.object_id,
                    counter_name="indestructible",
                    amount=1,
                    expected_logical_object_id=permanent.logical_object_id,
                ),
            ),
        )
        commit_counter_removals(engine, removal)

        destroyed = destroy_permanent_refs(
            engine,
            (permanent.ref,),
            actor="A",
            reason="removed keyword-counter witness",
        )
        self.assertEqual(
            (permanent.object_id,),
            destroyed.destroyed_object_ids,
        )

    def test_deathtouch_counter_feeds_assignment_and_final_damage_result(self):
        session = self.session(122_001_006, step="combat_damage")
        engine = session.engine
        engine.state.phase_index = 7
        attacker = self.token(
            engine,
            "A",
            "Counter-granted deathtoucher",
            power=2,
            toughness=8,
        )
        first = self.token(
            engine,
            "B",
            "First high-toughness blocker",
            power=1,
            toughness=8,
        )
        second = self.token(
            engine,
            "B",
            "Second high-toughness blocker",
            power=1,
            toughness=8,
        )
        self.place_keyword_counter(engine, attacker, "deathtouch")
        self.set_combat(engine, attacker, first, second)
        engine._begin_combat_damage()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {
                        "source": attacker.ref,
                        "target": first.ref,
                        "amount": 1,
                    },
                    {
                        "source": attacker.ref,
                        "target": second.ref,
                        "amount": 1,
                    },
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("outside", first.zone)
        self.assertEqual("outside", second.zone)
        self.assertEqual("battlefield", attacker.zone)
        self.assert_replays(session, "keyword-counter-deathtouch")

    def test_keyword_damage_composes_with_exact_counter_replacement_and_residuals(
        self,
    ):
        session = self.session(122_001_015, step="combat_damage")
        engine = session.engine
        engine.state.phase_index = 7
        doubling_record = self.db.lookup("Doubling Season")
        compiled = compile_oracle_card(
            doubling_record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertEqual("exact", compiled.status)
        self.assertFalse(compiled.material_residuals)
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        registry = load_default_capability_registry()
        program = compile_best_available_card_program(
            self.db, doubling_record, semantic_registry=SemanticRegistry(),
            capability_registry=registry, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(
            program, capability_registry=registry, profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertIn(
            "ability:replacement:counter-quantity:capability:"
            "undeclared_runtime_dependency:counter.placement.quantity_replacement",
            binding["blockers"],
        )
        unresolved = compile_oracle_card(
            self.db.lookup("Securitron Squadron"), capability_registry=registry,
            capability_profile="commander_review",
        )
        blockers = {
            blocker
            for residual in unresolved.material_residuals
            for blocker in residual.blockers
        }
        self.assertGreaterEqual(
            blockers,
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            },
        )

        attacker = self.token(
            engine,
            "A",
            "Counter-granted damage source",
            power=1,
            toughness=5,
            temporary_keywords=("Infect",),
        )
        for counter_name in ("deathtouch", "lifelink"):
            self.place_keyword_counter(engine, attacker, counter_name)
        doubling = self.add_registered_permanent(
            engine,
            seat="B",
            name="Doubling Season",
            ref="ASSURANCE-DOUBLING",
        )
        created = engine.create_token(
            "B",
            name="Indestructible damage recipient",
            characteristics={
                "type_line": "Token Creature — Test",
                "colors": ["W"],
                "power": "0",
                "toughness": "5",
                "keywords": ["Indestructible"],
            },
        )
        self.assertEqual(2, len(created))
        target = engine._resolve_object(
            "B", created[0], zones={"battlefield"}
        )
        engine.state.players["A"].life = 20
        proposal = damage_proposal(
            engine,
            proposal_id="damage:classified-residual-assurance",
            actor="A",
            source_ref=attacker.ref,
            target=target.ref,
            amount=1,
            combat=False,
            reason="classified residual interaction assurance",
        )
        commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(engine, (proposal,)),
        )
        self.assertEqual(21, engine.state.players["A"].life)
        self.assertEqual(1, target.counters["-1/-1"])
        self.assertEqual(0, target.marked_damage)
        self.assertTrue(target.deathtouch_damage)
        self.assertEqual("battlefield", target.zone)
        placed = place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=target.object_id,
                    counter_name="stun",
                    amount=1,
                    placing_player="A",
                    source_ref=attacker.ref,
                ),
            ),
            reason="classified residual interaction assurance",
        )
        self.assertEqual(2, placed[0].placed)
        self.assertEqual(2, target.counters["stun"])
        replacement = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
            and event.details.get("source") == doubling.ref
        )
        self.assertEqual(1, replacement.details["requested"])
        self.assertEqual(2, replacement.details["resolved"])

    def test_trample_counter_feeds_offer_and_command_spill_legality(self):
        session = self.session(122_001_007, step="combat_damage")
        engine = session.engine
        engine.state.phase_index = 7
        attacker = self.token(
            engine,
            "A",
            "Counter-granted trampler",
            power=5,
            toughness=5,
        )
        blocker = self.token(engine, "B", "Trample blocker")
        self.place_keyword_counter(engine, attacker, "trample")
        self.set_combat(engine, attacker, blocker)
        engine._begin_combat_damage()
        decision = session.packet("pilot:A", full=True)["decision"]
        damage_sources = decision["legal_actions"][0]["form"]["fields"][0][
            "combat"
        ]["damage_sources"]
        self.assertEqual(
            [blocker.ref, "B"],
            damage_sources[attacker.ref]["targets"],
        )
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {
                        "source": attacker.ref,
                        "target": blocker.ref,
                        "amount": 1,
                    },
                    {"source": attacker.ref, "target": "B", "amount": 4},
                ],
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {
                        "source": attacker.ref,
                        "target": blocker.ref,
                        "amount": 2,
                    },
                    {"source": attacker.ref, "target": "B", "amount": 3},
                ],
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(37, engine.state.players["B"].life)
        self.assertEqual(
            "outside",
            engine.state.cards[blocker.object_id].zone,
        )
        self.assert_replays(session, "keyword-counter-trample")

    def test_menace_counter_feeds_offer_and_command_block_legality(self):
        session = self.session(122_001_008, step="declare_blockers")
        engine = session.engine
        engine.state.phase_index = 6
        attacker = self.token(engine, "A", "Counter-granted menace")
        first = self.token(engine, "B", "First menace blocker")
        second = self.token(engine, "B", "Second menace blocker")
        self.place_keyword_counter(engine, attacker, "menace")
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        decision = session.packet("pilot:B", full=True)["decision"]
        self.assertEqual(
            {attacker.ref: 2},
            decision["ctx"]["minimum_blockers"],
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:B",
            {"a": "block", "blk": {first.ref: attacker.ref}},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act(
            "pilot:B",
            {
                "a": "block",
                "blk": {
                    first.ref: attacker.ref,
                    second.ref: attacker.ref,
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assert_replays(session, "keyword-counter-menace")

    def test_hexproof_counter_feeds_offer_and_resolution_revalidation(self):
        session = self.session(122_001_009, step="combat_damage")
        engine = session.engine
        opposing = self.token(
            engine,
            "B",
            "Opposing counter-granted hexproof",
            colors=("U",),
        )
        controlled = self.token(
            engine,
            "A",
            "Controlled counter-granted hexproof",
            colors=("U",),
        )
        later_protected = self.token(
            engine,
            "B",
            "Later counter-granted hexproof",
            colors=("U",),
        )
        self.place_keyword_counter(engine, opposing, "hexproof")
        self.place_keyword_counter(engine, controlled, "hexproof")
        _, action = self.prepare_red_elemental_blast(session)

        legal_refs = action["target_schema"]["legal_refs"]
        self.assertNotIn(opposing.ref, legal_refs)
        self.assertIn(controlled.ref, legal_refs)
        self.assertIn(later_protected.ref, legal_refs)
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                session.packet(f"pilot:{seat}", full=True)["decision"]
            )

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [opposing.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [later_protected.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        stack_ref = engine.state.stack[-1].ref

        self.place_keyword_counter(engine, later_protected, "hexproof")
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(16):
            if not any(item.ref == stack_ref for item in engine.state.stack):
                break
            pass_current(session)

        self.assertFalse(
            any(item.ref == stack_ref for item in engine.state.stack)
        )
        self.assertEqual(
            "battlefield",
            engine.state.cards[later_protected.object_id].zone,
        )
        self.assert_replays(session, "keyword-counter-hexproof-revalidation")


if __name__ == "__main__":
    unittest.main()

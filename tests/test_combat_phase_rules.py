from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import ROOT, keep_all, load_assets, make_session
from declaration_support import compiled_declaration_fragments
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.aura import SimpleEnchantSpec
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import GameRuleError, TURN_STEPS
from quorune.model import CombatState, DecisionGroup
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CombatPhaseRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 2):
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

    @staticmethod
    def enter_step(session, step: str) -> None:
        engine = session.engine
        engine.state.phase_index = TURN_STEPS.index(("combat", step))
        engine._enter_step()

    @staticmethod
    def token(
        engine,
        controller: str,
        name: str,
        *,
        haste: bool = False,
    ):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=(["Haste"] if haste else []),
        )[0]
        return next(
            card
            for card in engine.state.cards.values()
            if card.ref == ref
        )

    def test_contract_traces_every_cr_506_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "combat-phase.json"
            ).read_text(encoding="utf-8")
        )
        expected = {
            "506",
            "506.1",
            "506.2",
            "506.2a",
            "506.2b",
            "506.3",
            "506.3a",
            "506.3b",
            "506.3c",
            "506.3d",
            "506.3e",
            "506.3f",
            "506.3g",
            "506.4",
            "506.4a",
            "506.4b",
            "506.4c",
            "506.4d",
            "506.4e",
            "506.5",
            "506.6",
            "506.7",
            "506.7a",
            "506.7b",
            "506.7c",
            "506.7d",
            "506.7e",
            "506.7f",
            "506.7g",
        }
        self.assertEqual(expected, set(contract["rule_references"]))

    def test_empty_combat_step_sequence_and_replay_are_exact(self):
        session = self.make_session(50601)
        engine = session.engine

        self.enter_step(session, "beginning_combat")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)

        for _ in range(3):
            for seat in ("A", "B"):
                result = session.act(
                    f"pilot:{seat}",
                    {
                        "a": "pass",
                        "reason": "Pass the represented combat boundary.",
                    },
                )
                self.assertTrue(result.ok, result.summary)

        self.assertEqual(
            ("postcombat_main", "main"),
            (engine.state.phase, engine.state.step),
        )
        combat_steps = [
            event.summary.split("combat/", 1)[1].rstrip(".")
            for event in engine.state.events
            if event.code == "step.begin"
            and "combat/" in event.summary
        ]
        self.assertEqual(
            ["beginning_combat", "declare_attackers", "end_combat"],
            combat_steps[-3:],
        )
        self.assertNotIn("declare_blockers", combat_steps[-3:])
        self.assertNotIn("combat_damage", combat_steps[-3:])

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "combat-phase"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(6, replay["commands"])

    def test_supported_attacking_and_defending_roles_are_authoritative(
        self,
    ):
        session = self.make_session(50602, players=4)
        engine = session.engine
        active = self.token(
            engine,
            "A",
            "Active Attacker",
            haste=True,
        )
        nonactive = self.token(
            engine,
            "B",
            "Nonactive Creature",
            haste=True,
        )

        self.enter_step(session, "beginning_combat")
        self.assertEqual(["B", "C", "D"], engine.state.combat.defending_players)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        self.enter_step(session, "declare_attackers")
        payload = engine.state.pending_decision.payload_by_actor["A"]

        self.assertEqual(
            {active.ref},
            {candidate["id"] for candidate in payload["candidates"]},
        )
        self.assertEqual(
            {"B", "C", "D"},
            set(payload["defenders"]),
        )
        with self.assertRaisesRegex(GameRuleError, "Could not find"):
            engine._complete_attackers(
                DecisionGroup(
                    decision_id="cr506-wrong-attacker",
                    kind="combat.attackers",
                    role="pilot",
                    actors=["A"],
                    allowed_actions=["attack"],
                    responses={
                        "A": {
                            "attackers": {
                                nonactive.ref: "C",
                            }
                        }
                    },
                )
            )

    def test_zone_and_control_changes_remove_objects_from_combat(
        self,
    ):
        session = self.make_session(50603, players=4)
        engine = session.engine
        attacker_one = self.token(engine, "A", "Attacker One")
        attacker_two = self.token(engine, "A", "Attacker Two")
        blocker_one = self.token(engine, "B", "Blocker One")
        blocker_two = self.token(engine, "B", "Blocker Two")
        attacker_one.attacking = "B"
        attacker_two.attacking = "B"
        blocker_one.blocking = attacker_one.object_id
        blocker_two.blocking = attacker_two.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={
                attacker_one.object_id: "B",
                attacker_two.object_id: "B",
            },
            defending_players=["B", "C", "D"],
            blockers={
                attacker_one.object_id: [blocker_one.object_id],
                attacker_two.object_id: [blocker_two.object_id],
            },
        )

        engine.move_card(
            attacker_one.object_id,
            "graveyard",
            reason="CR 506 zone-change witness",
        )
        self.assertNotIn(
            attacker_one.object_id,
            engine.state.combat.attackers,
        )
        self.assertIsNone(attacker_one.attacking)
        self.assertEqual(
            attacker_one.object_id,
            blocker_one.blocking,
        )

        engine.change_control(
            attacker_two.object_id,
            "C",
            reason="CR 506 control-change witness",
        )
        self.assertNotIn(
            attacker_two.object_id,
            engine.state.combat.attackers,
        )
        self.assertIsNone(attacker_two.attacking)
        self.assertEqual(
            attacker_two.object_id,
            blocker_two.blocking,
        )

        engine.change_control(
            blocker_two.object_id,
            "C",
            reason="CR 506 blocker control-change witness",
        )
        self.assertIsNone(blocker_two.blocking)
        self.assertNotIn(
            blocker_two.object_id,
            engine.state.combat.blockers[attacker_two.object_id],
        )

    def test_postdeclaration_restriction_does_not_remove_attacker(self):
        session = self.make_session(50607)
        engine = session.engine
        attacker = self.token(engine, "A", "Already Attacking", haste=True)
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            attack_target_context={
                attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        aura_ref = engine.create_token(
            "B",
            name="Late Restraint",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": (
                    "Enchant creature\n"
                    "Enchanted creature can't attack."
                ),
                "ability_fragments": [
                    ability_fragment_to_dict(SimpleEnchantSpec("creature")),
                    *compiled_declaration_fragments(
                        "Late Restraint",
                        "Enchanted creature can't attack.",
                    ),
                ],
            },
            aura_target_ref=attacker.ref,
        )[0]
        aura = engine._resolve_object(
            "A", aura_ref, zones={"battlefield"}
        )
        self.assertFalse(engine._stabilize())

        self.assertEqual("B", attacker.attacking)
        self.assertEqual(
            "B", engine.state.combat.attackers[attacker.object_id]
        )

    def test_phasing_and_type_loss_remove_combatants_during_stabilization(
        self,
    ):
        session = self.make_session(50604)
        engine = session.engine
        attacker = self.token(engine, "A", "Phasing Attacker")
        blocker = self.token(engine, "B", "Dormant Blocker")
        attacker.attacking = "B"
        blocker.blocking = attacker.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
            blockers={attacker.object_id: [blocker.object_id]},
        )
        attacker.phased_out = True
        blocker.annotations["copy_overrides"] = {
            "name": "Dormant Blocker",
            "type_line": "Artifact",
        }

        self.assertFalse(engine._stabilize())

        self.assertIsNone(attacker.attacking)
        self.assertIsNone(blocker.blocking)
        self.assertFalse(engine.state.combat.attackers)
        self.assertEqual(
            [],
            engine.state.combat.blockers[attacker.object_id],
        )

    def test_tapping_and_untapping_do_not_remove_a_creature_from_combat(
        self,
    ):
        session = self.make_session(50605)
        engine = session.engine
        attacker = self.token(engine, "A", "Tapped Attacker")
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )

        attacker.tapped = True
        self.assertFalse(engine._remove_invalid_combat_objects())
        self.assertEqual("B", attacker.attacking)
        self.assertIn(attacker.object_id, engine.state.combat.attackers)

        attacker.tapped = False
        self.assertFalse(engine._remove_invalid_combat_objects())
        self.assertEqual("B", attacker.attacking)
        self.assertIn(attacker.object_id, engine.state.combat.attackers)


class PrintedCombatRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'printed-combat-removal.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                                ROOT / 'tests/fixtures/combat-removal-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Printed combat removal', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def declare_blocks(self, session, assignments):
        self.until(session, lambda s: s.state.pending_decision.kind == 'combat.blockers')
        command = {'action_id': 'block', 'blocks': {blocker.ref: attacker.ref for blocker, attacker in assignments}}
        result = session.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)

    def activation(self, session, seat, source):
        self.priority(session, seat)
        legal = session.packet('pilot:' + seat, full=True)['decision']['legal_actions']
        return next(action for action in legal if action['id'].startswith(f'activate:{source.ref}:')
                    and 'target_schema' in action)

    def test_actual_gustcloak_runner_decline_and_stun_replacement_preserve_combat_rules(self):
        session = self.session(50677501)
        runner = self.add(session.engine, 'Gustcloak Runner')
        blockers = [self.add(session.engine, 'Grizzly Bears', seat='B', ref=f'runner-blocker-{i}') for i in range(2)]
        runner.acquired_control_turn_count = -1
        runner.counters['stun'] = 1
        self.assert_closed(session, runner)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(runner, 'B')])
        self.declare_blocks(session, [(blocker, runner) for blocker in blockers])
        self.assertEqual(1, self.trigger_count(session, runner))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(['pilot:A'], session.pending_principals())
        self.assertIsNone(session.packet('pilot:B', full=True)['decision'])
        for choice in ('decline', 'apply'):
            with self.subTest(choice=choice):
                branch = self.replay(session, load=True)
                command = {'action_id': 'choose', 'choice': choice}
                before = authoritative_state_hash(branch.state)
                self.assertFalse(branch.act('pilot:B', command).ok)
                self.assertEqual(before, authoritative_state_hash(branch.state))
                applied = branch.act('pilot:A', command)
                self.assertTrue(applied.ok, applied.summary)
                current = branch.state.cards[runner.object_id]
                self.assertTrue(current.tapped)
                self.assertEqual(int(choice == 'decline'), current.counters.get('stun', 0))
                self.assertEqual(choice == 'decline', runner.object_id in branch.state.combat.attackers)
                for blocker in blockers:
                    self.assertEqual(runner.object_id, branch.state.cards[blocker.object_id].blocking)
                self.replay(branch, load=True)

    def test_actual_gustcloak_savior_does_not_untap_the_returned_attacker(self):
        session = self.session(50677502)
        savior = self.add(session.engine, 'Gustcloak Savior')
        attacker = self.add(session.engine, 'Grizzly Bears', ref='savior-attacker')
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B', ref='savior-blocker')
        self.add(session.engine, 'Imposing Sovereign', seat='B')
        blink = self.add(session.engine, 'Planar Incision', seat='C', zone='hand')
        for i in range(2):
            self.add(session.engine, 'Island', seat='C', ref=f'savior-island-{i}')
        attacker.acquired_control_turn_count = -1
        self.assert_closed(session, savior)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(attacker, 'B')])
        self.declare_blocks(session, [(blocker, attacker)])
        trigger, = session.state.stack
        self.assertEqual(savior.object_id, trigger.source_object_id)
        self.assertEqual(attacker.logical_object_id, trigger.context['event_counter_recipient']['logical_object_id'])
        identity = attacker.logical_object_id
        self.cast_for(session, 'C', blink, [attacker.ref])
        self.resolve_top(session)
        returned = session.state.cards[attacker.object_id]
        self.assertNotEqual(identity, returned.logical_object_id)
        self.assertTrue(returned.tapped)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertTrue(session.state.cards[attacker.object_id].tapped)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_actual_labyrinth_keeps_the_attacker_blocked_and_hollowhenge_removes_the_other_attacker(self):
        session = self.session(50677503)
        attackers = [self.add(session.engine, 'Grizzly Bears', ref=f'combat-attacker-{i}') for i in range(2)]
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B')
        idle = self.add(session.engine, 'Imposing Sovereign', seat='B')
        labyrinth = self.add(session.engine, 'Labyrinth of Skophos', seat='B')
        spirit = self.add(session.engine, 'Hollowhenge Spirit', seat='C', zone='hand')
        for seat in 'BC':
            for i in range(4):
                self.add(session.engine, 'Generic Bound Plains', seat=seat, ref=f'combat-payment-{seat}-{i}')
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        for source in (labyrinth, spirit):
            self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        first, second = attackers
        self.attack(session, [(attacker, 'B') for attacker in attackers])
        self.declare_blocks(session, [(blocker, first)])
        action = self.activation(session, 'B', labyrinth)
        self.assertEqual({first.ref, second.ref, blocker.ref}, set(action['target_schema']['legal_refs']))
        command = {'action_id': action['id'], 'targets': [blocker.ref], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {**command, 'targets': [idle.ref]}).ok)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertIn(first.object_id, session.state.combat.attackers)
        self.assertEqual([], session.state.combat.blockers[first.object_id])
        self.assertIsNone(session.state.cards[blocker.object_id].blocking)

        self.cast_for(session, 'C', spirit)
        self.resolve_top(session)
        self.target(session, 'C', [second.ref])
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertTrue(session.state.cards[second.object_id].tapped)
        self.assertNotIn(second.object_id, session.state.combat.attackers)
        life = session.state.players['B'].life
        self.until(session, lambda s: s.state.step == 'end_combat')
        self.assertEqual(life, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_actual_reconnaissance_removes_then_untaps_after_damage_in_end_of_combat(self):
        session = self.session(50677504)
        source = self.add(session.engine, 'Reconnaissance')
        attacker = self.add(session.engine, 'Grizzly Bears', ref='reconnaissance-attacker')
        self.add(session.engine, 'Grizzly Bears', seat='B', ref='reconnaissance-blocker')
        attacker.acquired_control_turn_count = -1
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        life = session.state.players['B'].life
        self.attack(session, [(attacker, 'B')])
        self.declare_blocks(session, [])
        self.until(session, lambda s: s.state.step == 'end_combat')
        self.assertEqual(life - 2, session.state.players['B'].life)
        self.assertTrue(session.state.cards[attacker.object_id].tapped)
        action = self.activation(session, 'A', source)
        self.assertEqual([attacker.ref], action['target_schema']['legal_refs'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [attacker.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        after = session.state.event_sequence
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertNotIn(attacker.object_id, session.state.combat.attackers)
        self.assertFalse(session.state.cards[attacker.object_id].tapped)
        self.assertEqual(life - 2, session.state.players['B'].life)
        events = [event.code for event in session.state.events if event.event_id > after
                  and event.details.get('object') == attacker.ref]
        self.assertEqual(['combat.remove', 'permanent.untap'], [code for code in events
                         if code in {'combat.remove', 'permanent.untap'}])
        self.replay(session, load=True)

    def test_combat_removal_requires_the_exact_announcement_and_runtime_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.combat_removal import COMBAT_REMOVAL_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.semantics import SemanticRegistry

        source = self.db.lookup('Reconnaissance', fuzzy=False)
        profile = 'commander_review'
        self.assertEqual('exact', compile_oracle_card(source, capability_registry=self.registry,
                                                     capability_profile=profile).status)
        with patch('quorune.compiler.combat_removal_templates.combat_removal_effect_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(source, capability_registry=self.registry,
                                                            capability_profile=profile).status)
        semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, (source,), capability_registry=self.registry,
            capability_profile=profile, promote_exact_effect_programs=True)
        program = next(program for program in semantics.programs_for_oracle(source.oracle_id) if program.event == 'activate')
        self.assertEqual('trusted', program.trust_level)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != COMBAT_REMOVAL_CAPABILITY)
        altered = replace(program, capability_dependencies=dependencies, coverage=(),
            capability_closure=self.registry.closure(dependencies, profile=profile).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=profile)
        self.assertIn('capability:undeclared_runtime_dependency:' + COMBAT_REMOVAL_CAPABILITY, binding['blockers'])
        malformed = replace(program, target_schema={**program.target_schema, 'combat_state': 'blocking'})
        self.assertFalse(bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=profile)['strict'])


if __name__ == "__main__":
    unittest.main()

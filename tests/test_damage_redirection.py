from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from dataclasses import replace
import json
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.controller_damage_redirection import CAPABILITY
from scripts.build_test_database import build_fixture_database
from damage_replacement_support import DamageReplacementPipelineBase
from inherited_composition_fixture import InheritedNativeFixture
from quorune.damage import (
    commit_prepared_damage_batch,
    prepare_damage_batch,
)
from quorune.damage_prevention import (
    ChosenDamageSource,
    DamageModifierDuration,
    DamageRedirectionEffect,
    DamageSubject,
)
from quorune.model import StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram


class DamageRedirectionTests(DamageReplacementPipelineBase):
    def palisade(self, engine, *, ref: str = "b-palisade"):
        record = self.db.lookup("Palisade Giant")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="trusted",
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        return self.add_permanent(
            engine, seat="B", name=record.name, ref=ref
        )

    def redirection(
        self,
        engine,
        *,
        subject: str = "B",
        destination: str = "C",
        chosen_source=None,
    ) -> DamageRedirectionEffect:
        effect = DamageRedirectionEffect(
            redirection_id="fixture-redirection",
            source_id="effect:fixture-redirection",
            controller="B",
            subject=DamageSubject(subject, "player", subject),
            destination=DamageSubject(destination, "player", destination),
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            chosen_source=chosen_source,
            label="Fixture redirection",
        )
        engine.state.damage_redirections.append(effect)
        return effect

    def test_redirection_replaces_complete_recipient_and_is_consumed(self):
        engine = self.session(614090, players=4).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        effect = self.redirection(engine)
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=3),),
        )
        event = prepared.events[0]
        self.assertEqual("C", event.affected_player)
        self.assertEqual("C", event.payload["target"])
        self.assertEqual("C", event.payload["target_controller"])
        self.assertIn(effect.effect_id, event.applied_effects)
        self.assertEqual(1, len(engine.state.damage_redirections))

        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(37, engine.state.players["C"].life)
        self.assertEqual("C", result.events[0].target)
        self.assertEqual([], engine.state.damage_redirections)

    def test_departed_destination_makes_redirection_do_nothing(self):
        engine = self.session(614091, players=4).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        self.redirection(engine)
        engine.state.players["C"].in_game = False
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=2),),
        )
        self.assertEqual("B", prepared.events[0].payload["target"])
        commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual(1, len(engine.state.damage_redirections))

    def test_chosen_source_redirection_uses_physical_identity(self):
        engine = self.session(614092, players=4).engine
        chosen = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="chosen"
        )
        other = self.add_permanent(
            engine, seat="A", name="White Knight", ref="other"
        )
        self.redirection(
            engine,
            chosen_source=ChosenDamageSource(
                ref=chosen.ref,
                object_id=chosen.object_id,
            ),
        )

        first = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=other, target="B", amount=1),),
        )
        commit_prepared_damage_batch(engine, first)
        self.assertEqual(39, engine.state.players["B"].life)
        self.assertEqual(40, engine.state.players["C"].life)
        self.assertEqual(1, len(engine.state.damage_redirections))

        second = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=chosen,
                    target="B",
                    amount=2,
                    event_id="damage:chosen",
                ),
            ),
        )
        commit_prepared_damage_batch(engine, second)
        self.assertEqual(39, engine.state.players["B"].life)
        self.assertEqual(38, engine.state.players["C"].life)

    def test_redirection_round_trip_rejects_unknown_fields(self):
        engine = self.session(614093, players=4).engine
        effect = self.redirection(engine)
        restored = DamageRedirectionEffect.from_dict(effect.to_dict())
        self.assertEqual(effect, restored)
        malformed = effect.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            DamageRedirectionEffect.from_dict(malformed)

    def test_static_redirection_compiles_and_runs_from_card_program(self):
        engine = self.session(614094, players=4).engine
        palisade = self.palisade(engine)
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        program = next(
            program
            for program in engine.semantics.programs_for_oracle(
                palisade.oracle_id
            )
            if program.handlers
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual(
            "replacement.damage.redirect-to-source.v1",
            program.handlers[0]["handler_id"],
        )

        result = commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(
                engine,
                (self.proposal(engine, source=source, target="B", amount=3),),
            ),
        )
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(3, palisade.marked_damage)
        self.assertEqual(palisade.ref, result.events[0].target)

    def test_static_redirection_uses_complete_recipient_snapshot(self):
        engine = self.session(614095, players=4).engine
        palisade = self.palisade(engine)
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=2),),
        )
        event = prepared.events[0]
        self.assertIsNone(event.affected_player)
        self.assertEqual(palisade.object_id, event.affected_object.object_id)
        self.assertEqual(palisade.ref, event.payload["target"])
        self.assertEqual("permanent", event.payload["target_kind"])
        self.assertEqual(palisade.object_id, event.payload["target_object_id"])
        self.assertEqual(
            palisade.logical_object_id,
            event.payload["target_logical_object_id"],
        )
        self.assertIn("creature", event.payload["target_types"])

    def test_static_redirection_stops_when_source_leaves(self):
        engine = self.session(614096, players=4).engine
        palisade = self.palisade(engine)
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        engine.move_card(palisade.object_id, "graveyard", log=False)
        result = commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(
                engine,
                (self.proposal(engine, source=source, target="B", amount=2),),
            ),
        )
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual("B", result.events[0].target)

    def test_static_redirection_does_not_replace_damage_to_its_source(self):
        engine = self.session(614099, players=4).engine
        palisade = self.palisade(engine)
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        prepared = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine, source=source, target=palisade, amount=2
                ),
            ),
        )
        self.assertEqual((), prepared.events[0].applied_effects)
        commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(2, palisade.marked_damage)

    def test_static_redirection_redirects_simultaneous_multiplayer_damage(self):
        engine = self.session(614097, players=4).engine
        palisade = self.palisade(engine)
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="c-source"
        )
        result = commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=source_a,
                        target="B",
                        amount=2,
                        event_id="damage:a",
                    ),
                    self.proposal(
                        engine,
                        source=source_c,
                        target="B",
                        amount=3,
                        event_id="damage:c",
                    ),
                ),
            ),
        )
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(5, palisade.marked_damage)
        self.assertEqual(
            [palisade.ref, palisade.ref],
            [event.target for event in result.events],
        )

    def test_static_redirection_command_replays_exactly(self):
        session = self.session(614098)
        engine = session.engine
        palisade = self.palisade(engine)
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        program = SemanticProgram(
            key="test:static-redirection-replay",
            label="Static redirection replay",
            effects=[
                {
                    "op": "damage",
                    "source": "$source",
                    "target": "B",
                    "amount": 2,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="static-redirection-replay",
                ref="S-static-redirection-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                source_object_id=source.object_id,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(2, palisade.marked_damage)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "static-redirection-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

class ChosenSourceShamanTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-source-redirection.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/finite-damage-redirection-cards.json",
            ROOT / "tests/fixtures/chosen-damage-redirection-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen-source redirection",
                                  [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def _action(self, game, source, seat, slot, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        identity = f"activate:{source.ref}:{slot}"
        action, = (row for row in actions if row["id"] == identity)
        return {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}

    def _activate(self, game, source, seat, slot, target):
        result = game.act("pilot:" + seat, self._action(game, source, seat, slot, target))
        self.assertTrue(result.ok, result.summary)

    def _reject(self, game, seat, command):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:" + seat, command)
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose_source(self, game, source_ref):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)
        offer = packet["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertIn(source_ref, offer["legal_refs"])
        command = {"action_id": "choose", "source": source_ref}
        self._reject(game, "C", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _marked(self, game, *cards):
        return tuple(game.state.cards[card.object_id].marked_damage for card in cards)

    def test_shaman_complete_program_and_declared_paid_result_rejects_malformed_scope(self):
        from dataclasses import replace
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.chosen_damage_redirection import CAPABILITY, VERSION_FIELD
        from quorune.rules.finite_damage_redirection import FINITE_DAMAGE_REDIRECTION_CAPABILITY
        from test_controller_creature_keyword_absence import register_card

        name = "Shaman en-Kor"
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        free, paid = sorted((program for program in semantics.programs_for_oracle(card.oracle_id)
                             if program.event == "activate"), key=lambda program: program.ability_id)
        self.assertIn(FINITE_DAMAGE_REDIRECTION_CAPABILITY, free.capability_dependencies)
        self.assertIn(CAPABILITY, paid.capability_dependencies)
        self.assertEqual(("{0}", "{1}{W}"), (free.cost_schema["text"], paid.cost_schema["text"]))
        effect, = paid.effects
        with self.assertRaisesRegex(ValueError, "Runtime handler event must match"):
            replace(paid, event="resolve")
        for changed in (
            replace(paid, target_schema=free.target_schema),
            replace(paid, cost_schema=free.cost_schema),
            replace(paid, effects=({**effect, "redirection": {**effect["redirection"], VERSION_FIELD: True}},)),
            replace(paid, effects=({**effect, "redirection": {**effect["redirection"], "maximum_damage": 1}},)),
            replace(paid, effects=({**effect, "redirection": {**effect["redirection"], "destination": "$target.0"}},)),
        ):
            proof = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(proof["strict"], proof)
        for before, after in (
            ("{1}{W}", "{2}{W}"),
            ("damage to target creature this turn, that damage", "damage to target creature you control this turn, that damage"),
            ("that damage is dealt to this creature", "that damage is dealt to this creature's controller"),
        ):
            modified = replace(card, oracle_text=card.oracle_text.replace(before, after))
            result = compile_oracle_card(modified, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", result.status, modified.oracle_text)

    def test_shaman_withdrawing_whole_damage_source_capability_keeps_the_zero_cost_branch(self):
        from common import ROOT
        import json
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.chosen_damage_redirection import CAPABILITY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.finite_damage_redirection import FINITE_DAMAGE_REDIRECTION_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests

        document = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in document["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Fixture: chosen redirection authority withdrawn"])
        registry = CapabilityRegistry(document)
        proof = build_index(
            registry_value=document,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(proof, registry=registry))
        card = self.db.lookup("Shaman en-Kor", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(CAPABILITY in blocker for residual in ir.material_residuals for blocker in residual.blockers))
        self.assertTrue(any(node.exact and FINITE_DAMAGE_REDIRECTION_CAPABILITY in node.capability_dependencies
                            for face in ir.faces for node in face.nodes))

    def test_shaman_dropped_choice_result_mutant_does_not_promote_the_activation(self):
        from unittest.mock import patch
        from quorune.compiler import activated_mana_nodes
        from quorune.rules.chosen_damage_redirection import MECHANIC, VERSION_FIELD
        from test_controller_creature_keyword_absence import register_card

        original = activated_mana_nodes._compiled_activated_effect

        def omit_binding(*args, **kwargs):
            template, effects, schema, mechanics = original(*args, **kwargs)
            if MECHANIC not in mechanics:
                return template, effects, schema, mechanics
            effect, = effects
            updated = {key: value for key, value in effect["redirection"].items() if key != VERSION_FIELD}
            return template, ({**effect, "redirection": updated},), schema, mechanics

        before, _ = register_card(self.db, self.registry, "Shaman en-Kor")
        self.assertTrue(before["strict_capability_ready"], before["blockers"])
        with patch.object(activated_mana_nodes, "_compiled_activated_effect", side_effect=omit_binding):
            changed, _ = register_card(self.db, self.registry, "Shaman en-Kor")
        self.assertFalse(changed["strict_capability_ready"], changed)

    def test_actual_shaman_stack_source_redirects_the_whole_event_after_other_damage_replay(self):
        game = self.session(1116480)
        shaman = self.add(game.engine, "Shaman en-Kor")
        watched = self.add(game.engine, "Lancers en-Kor", seat="B")
        land = self.add(game.engine, "Plains", seat="B")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        private = self.add(game.engine, "Shock", seat="C", zone="hand", ref="private-shaman-source")
        for card in (shaman, watched, bolt, shock):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", bolt, [watched.ref], reserve={"R": 1})
        chosen = game.state.stack[-1].ref
        self._reject(game, "A", self._action(game, shaman, "A", "ab2", land))
        self._activate(game, shaman, "A", "ab2", watched)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertFalse(game.state.damage_redirections)
        game = self.replay(game, load=True)
        self.assertNotIn(private.ref, str(game.packet("pilot:A", full=True)))
        self._reject(game, "A", {"action_id": "choose", "source": private.ref})
        self._choose_source(game, chosen)
        rule, = game.state.damage_redirections
        self.assertEqual((watched.logical_object_id, shaman.logical_object_id, 3, None),
                         (rule.subject.logical_object_id, rule.destination.logical_object_id,
                          rule.chosen_source.snapshot_version, rule.maximum_damage))
        self.cast(game, "B", shock, [watched.ref])
        self.resolve_top(game)
        self.assertEqual((0, 2), self._marked(game, shaman, watched))
        self.assertEqual([rule], game.state.damage_redirections)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[shaman.object_id].zone)
        self.assertEqual(2, game.state.cards[watched.object_id].marked_damage)
        dealt, = (event.details["damage_event"] for event in game.state.events
                  if event.code == "effect.damage" and event.details.get("target") == shaman.ref)
        self.assertEqual((rule.destination.logical_object_id, 3),
                         (dealt["target_logical_object_id"], dealt["amount"]))
        self.assertFalse(game.state.damage_redirections)
        self.replay(game, load=True)

    def test_actual_shaman_same_permanent_uses_paid_rule_once_and_retains_zero_cost_point(self):
        game = self.session(1116481)
        shaman = self.add(game.engine, "Shaman en-Kor")
        watched = self.add(game.engine, "Lancers en-Kor")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        untapper = self.add(game.engine, "Aphetto Alchemist", seat="B")
        haste = self.add(game.engine, "Fervor", seat="B")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        for card in (shaman, watched, pinger, untapper, haste, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self._activate(game, shaman, "A", "ab2", watched)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose_source(game, pinger.ref)
        self._activate(game, pinger, "B", "ab1", watched)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 0), self._marked(game, shaman, watched))
        self.assertFalse(game.state.damage_redirections)
        self._activate(game, untapper, "B", "ab1", pinger)
        self.assertIsNone(self.resolve(game))
        self._activate(game, pinger, "B", "ab1", watched)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 1), self._marked(game, shaman, watched))
        self._activate(game, shaman, "A", "ab1", watched)
        self.assertIsNone(self.resolve(game))
        free, = game.state.damage_redirections
        self.assertEqual(1, free.maximum_damage)
        self.assertIsNone(free.chosen_source)
        self.cast(game, "B", bolt, [shaman.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[watched.object_id].marked_damage)
        self.assertEqual("graveyard", game.state.cards[shaman.object_id].zone)
        self.assertFalse(game.state.damage_redirections)
        self.replay(game, load=True)

    def test_actual_shaman_redirection_preserves_all_three_original_incarnations(self):
        for index, changed in enumerate(("subject", "destination", "dealer")):
            with self.subTest(changed=changed):
                game = self.session(1116482 + index)
                shaman = self.add(game.engine, "Shaman en-Kor")
                watched = self.add(game.engine, "Lancers en-Kor")
                pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
                self.add(game.engine, "Fervor", seat="B")
                blink = self.add(game.engine, "Flicker of Fate", zone="hand")
                for card in (shaman, watched, pinger, blink):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 2, "W": 2})
                self.checkpoint(game)
                self._activate(game, shaman, "A", "ab2", watched)
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self._choose_source(game, pinger.ref)
                rule, = game.state.damage_redirections
                blinked = {"subject": watched, "destination": shaman, "dealer": pinger}[changed]
                original = game.state.cards[blinked.object_id].logical_object_id
                self.cast(game, "A", blink, [blinked.ref])
                self.assertIsNone(self.resolve(game))
                self.assertNotEqual(original, game.state.cards[blinked.object_id].logical_object_id)
                game = self.replay(game, load=True)
                self._activate(game, pinger, "B", "ab1", watched)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((0, 1), self._marked(game, shaman, watched))
                self.assertEqual([rule], game.state.damage_redirections)
                self.replay(game, load=True)

    def test_actual_shaman_lost_announced_target_gates_source_choice_and_unused_rule_expires(self):
        from test_zero_mana_damage_and_life_gain_permission import ZeroManaDamageAndLifeGainPermissionTests

        game = self.session(1116485)
        shaman = self.add(game.engine, "Shaman en-Kor")
        watched = self.add(game.engine, "Lancers en-Kor")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        self.add(game.engine, "Fervor", seat="B")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        for card in (shaman, watched, pinger, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self._activate(game, shaman, "A", "ab2", watched)
        self.cast(game, "B", blink, [watched.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.damage_redirections)
        self._activate(game, shaman, "A", "ab2", watched)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose_source(game, pinger.ref)
        self.assertEqual(1, len(game.state.damage_redirections))
        turn = game.state.turn_sequence
        game = self.replay(game, load=True)
        ZeroManaDamageAndLifeGainPermissionTests._advance(
            self, game, lambda session: session.state.turn_sequence > turn and session.state.active_player == "B",
        )
        self.assertFalse(game.state.damage_redirections)
        self._activate(game, pinger, "B", "ab1", watched)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 1), self._marked(game, shaman, watched))
        self.replay(game, load=True)


    def _arm_test_bolt(self, game, shaman, watched, bolt):
        self.cast(game, "B", bolt, [watched.ref])
        chosen = game.state.stack[-1].ref
        self._activate(game, shaman, "A", "ab2", watched)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose_source(game, chosen)

    def test_actual_shaman_protection_on_new_destination_prevents_the_redirected_damage_replay(self):
        from quorune.ability_fragments import ability_fragment_from_dict, protection_specs
        from test_chosen_aura_protection import ChosenAuraProtectionTests

        game = self.session(1116486)
        shaman = self.add(game.engine, "Shaman en-Kor")
        watched = self.add(game.engine, "Lancers en-Kor")
        ward = self.add(game.engine, "Flickering Ward", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        for card in (shaman, watched, ward, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 2})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", ward, [shaman.ref], reserve={"C": 1, "W": 1})
        ChosenAuraProtectionTests.choose(self, game, "R")
        facts = game.engine._effective_card_data(game.state.cards[shaman.object_id])
        protection, = protection_specs(ability_fragment_from_dict(row) for row in facts["ability_fragments"])
        self.assertEqual("R", protection.quality)
        self._arm_test_bolt(game, shaman, watched, bolt)
        rule, = game.state.damage_redirections
        wire = rule.to_dict()
        self.assertEqual(1, wire["chosen_source_scope_version"])
        self.assertEqual(rule, DamageRedirectionEffect.from_dict(wire))
        legacy = {key: value for key, value in wire.items() if key != "chosen_source_scope_version"}
        self.assertEqual(legacy, DamageRedirectionEffect.from_dict(legacy).to_dict())
        for invalid in (True, None, 2):
            with self.assertRaises(ValueError):
                DamageRedirectionEffect.from_dict({**wire, "chosen_source_scope_version": invalid})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", (0, 0)),
                         (game.state.cards[shaman.object_id].zone, self._marked(game, shaman, watched)))
        prevented, = (event.details["damage_event"] for event in game.state.events
                      if event.code == "effect.damage.prevented" and event.details.get("target") == shaman.ref)
        self.assertEqual((shaman.logical_object_id, 0, 3),
                         (prevented["target_logical_object_id"], prevented["amount"], prevented["prevented_amount"]))
        self.assertFalse(game.state.damage_redirections)
        self.replay(game, load=True)

    def test_actual_shaman_phased_out_destination_neither_redirects_nor_consumes_replay(self):
        game = self.session(1116487)
        shaman = self.add(game.engine, "Shaman en-Kor")
        watched = self.add(game.engine, "Lancers en-Kor")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        phase = self.add(game.engine, "Reality Ripple", seat="C", zone="hand")
        for card in (shaman, watched, bolt, phase):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        self._arm_test_bolt(game, shaman, watched, bolt)
        rule, = game.state.damage_redirections
        self.cast(game, "C", phase, [shaman.ref])
        self.resolve_top(game)
        current = game.state.cards[shaman.object_id]
        self.assertTrue(current.phased_out)
        self.assertEqual(rule.destination.logical_object_id, current.logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[watched.object_id].zone)
        self.assertEqual([rule], game.state.damage_redirections)
        dealt, = (event.details["damage_event"] for event in game.state.events
                  if event.code == "effect.damage" and event.details.get("target") == watched.ref)
        self.assertEqual((rule.subject.logical_object_id, 3),
                         (dealt["target_logical_object_id"], dealt["amount"]))
        self.replay(game, load=True)

class ControllerDamageRedirectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-damage-redirection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-damage-redirection-cards.json"], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Controller damage redirection", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, seat, permanent):
        command = {"action_id": "choose", "objects": [permanent.ref]}
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def cast_sacrifice(self, game, spell, destination, seat="A"):
        self.cast_for(game, seat, spell, [])
        self.assertEqual([], list(game.state.stack[-1].targets))
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(["pilot:" + seat], list(game.pending_principals()))
        self.choose(game, seat, destination)
        self.assertIsNone(self.resolve(game))

    def test_native_resolution_choice_redirects_through_cleanup_and_replays(self):
        game = self.session(6145111)
        spell = self.add(game.engine, "Gideon's Sacrifice", zone="hand")
        chosen = self.add(game.engine, "Black Knight")
        illegal = self.add(game.engine, "Grizzly Bears", seat="C")
        first = self.add(game.engine, "Shock", seat="B", zone="hand", ref="controller-redirection-first")
        secret = self.add(game.engine, "Shock", seat="D", zone="hand", ref="controller-redirection-private")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [])
        self.assertEqual([], list(game.state.stack[-1].targets))
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        self.reject(game, "C", {"action_id": "choose", "objects": [chosen.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": [illegal.ref]})
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.choose(game, "A", chosen)
        self.assertIsNone(self.resolve(game))
        effect, = game.state.damage_redirections
        self.assertEqual(chosen.logical_object_id, effect.destination.logical_object_id)
        self.cast_for(game, "B", first, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        start_turn = game.state.turn_sequence
        self.until(game, lambda s: s.state.turn_sequence > start_turn)
        self.assertEqual([], game.state.damage_redirections)
        self.replay(game, load=True)

    def test_no_eligible_choice_and_blinked_destination_leave_damage_unchanged(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                game = self.session(6145120 + blink)
                spell = self.add(game.engine, "Gideon's Sacrifice", zone="hand")
                chosen = self.add(game.engine, "Grizzly Bears", seat="A" if blink else "C")
                flicker = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                self.add(game.engine, "Mountain", seat="B")
                self.prepare(game, mana={"W": 1})
                game.state.players["B"].mana_pool.update({"C": 1, "R": 1, "W": 1})
                self.checkpoint(game)
                if blink:
                    self.cast_sacrifice(game, spell, chosen)
                    old_incarnation = game.state.cards[chosen.object_id].logical_object_id
                    self.cast_for(game, "B", flicker, [chosen.ref], reserve={"R": 1})
                    self.assertIsNone(self.resolve(game))
                    self.assertNotEqual(old_incarnation,
                                        game.state.cards[chosen.object_id].logical_object_id)
                else:
                    self.cast_for(game, "A", spell, [])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual([], game.state.damage_redirections)
                self.cast_for(game, "B", shock, ["A"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(38, game.state.players["A"].life)
                self.replay(game, load=True)

    def test_current_recipient_scope_and_protection_use_the_redirected_destination(self):
        game = self.session(6145131)
        spell = self.add(game.engine, "Gideon's Sacrifice", zone="hand")
        knight = self.add(game.engine, "Silver Knight")
        friendly = self.add(game.engine, "Grizzly Bears", ref="friendly-recipient")
        enemy = self.add(game.engine, "Grizzly Bears", seat="C", ref="enemy-recipient")
        shocks = [self.add(game.engine, "Shock", seat="B", zone="hand", ref=f"controlled-shock-{i}")
                  for i in range(3)]
        self.prepare(game, mana={"W": 1})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_sacrifice(game, spell, knight)
        for shock, target in zip(shocks, (friendly.ref, "A", enemy.ref), strict=True):
            self.cast_for(game, "B", shock, [target])
            pending = self.resolve(game)
            if pending is not None:
                self.fail(pending.kind)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("battlefield", game.state.cards[friendly.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[knight.object_id].zone)
        self.assertEqual(0, game.state.cards[knight.object_id].marked_damage)
        self.assertEqual("graveyard", game.state.cards[enemy.object_id].zone)
        self.replay(game, load=True)

    def _simultaneous_native_witness(self):
        game = self.session(6145141)
        spells = [self.add(game.engine, "Gideon's Sacrifice", zone="hand", ref=f"controller-shield-{i}")
                  for i in range(2)]
        chosen = [self.add(game.engine, "Black Knight"), self.add(game.engine, "Gideon Jura")]
        chosen[1].counters["loyalty"] = 6
        friendly = self.add(game.engine, "Grizzly Bears")
        quake = self.add(game.engine, "Earthquake", zone="hand")
        self.prepare(game, mana={"W": 2, "C": 1, "R": 1})
        self.checkpoint(game)
        for spell, recipient in zip(spells, chosen, strict=True):
            self.cast_sacrifice(game, spell, recipient)
        self.priority(game, "A")
        offered = self.action(game, "A", quake)
        cast = game.act("pilot:A", {"action_id": offered["id"], "x": 1, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        effects = [effect.effect_id for effect in game.state.damage_redirections]
        command = {"action_id": "choose", "replacement": effects[1]}
        self.reject(game, "C", command)
        game = self.replay(game, load=True)
        decision = game.act("pilot:A", command)
        self.assertTrue(decision.ok, decision.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(0, game.state.cards[chosen[0].object_id].marked_damage)
        self.assertEqual(3, game.state.cards[chosen[1].object_id].counters["loyalty"])
        self.assertEqual(0, game.state.cards[friendly.object_id].marked_damage)
        self.replay(game, load=True)

    def test_one_controller_choice_applies_across_the_simultaneous_occurrence(self):
        self._simultaneous_native_witness()

    def test_independent_row_redirection_mutant_is_killed(self):
        with patch("quorune.replacement.ordering.exclusive_damage_scope_effects", side_effect=lambda _journal, effects: effects):
            with self.assertRaisesRegex(AssertionError, "replacement.order"):
                self._simultaneous_native_witness()

    def test_aura_replay_uses_its_departure_attachment_and_retains_that_destination(self):
        game = self.session(6145151)
        aura = self.add(game.engine, "Saving Grace", zone="hand")
        chosen = self.add(game.engine, "Grizzly Bears", ref="enchanted-creature")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="enemy-enchant-target")
        self.add(game.engine, "Mountain", seat="B")
        self.assert_closed(game, aura)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "R": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        self.reject(game, "A", {"action_id": self.action(game, "A", aura)["id"], "targets": [other.ref], "pay": "auto"})
        self.cast_for(game, "A", aura, [chosen.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(chosen.object_id, "toughness"))
        self.cast_for(game, "B", destroy, [aura.ref], reserve={"R": 1})
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(chosen.logical_object_id, game.state.damage_redirections[0].destination.logical_object_id)
        self.cast_for(game, "B", shock, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        self.replay(game, load=True)

    def test_withdrawn_capability_and_unsupported_wording_fail_closed(self):
        card = self.db.lookup("Gideon's Sacrifice", fuzzy=False)
        for text in (
            card.oracle_text.replace("creature or planeswalker", "creature or battle"),
            card.oracle_text.replace("All damage", "All combat damage"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status)
        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in original["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["controller redirection authority is unavailable"],
        )
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index

        source = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = {entry["test_id"] for entry in source["declarations"]}
        disabled = CapabilityRegistry(original)
        evidence = build_index(registry_value=original, declaration_source=source, discovered_test_ids=known)
        disabled.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=disabled))
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=disabled).status)
        self.assertEqual("exact", compile_oracle_card(
            self.db.lookup("Shock", fuzzy=False), capability_registry=disabled,
        ).status)



class WatchedDeathRedirectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "watched-death.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "controller-damage-redirection-cards.json", fixtures / "heroic-sacrifice-cards.json"],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Watched death redirection", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def assert_rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose_trigger(self, game, targets):
        result = game.act("pilot:A", {"action_id": "choose", "targets": targets})
        self.assertTrue(result.ok, result.summary)

    def _death_scene(self, seed, *, counters=True):
        game = self.session(seed)
        spell = self.add(game.engine, "Heroic Sacrifice", zone="hand")
        chosen = self.add(game.engine, "Grizzly Bears", ref="watched-bear")
        recipient = self.add(game.engine, "Grizzly Bears", ref="receiving-bear")
        enemy = self.add(game.engine, "Grizzly Bears", seat="C", ref="enemy-bear")
        loyalty = self.add(game.engine, "Gideon Jura")
        loyalty.counters["loyalty"] = 6
        if counters:
            chosen.counters.update({"+1/+1": 1, "stun": 2})
        shock = self.add(game.engine, "Shock", seat="B", zone="hand", ref="shield-scope-shock")
        poke = self.add(game.engine, "Shock", seat="B", zone="hand", ref="redirected-poke")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="watched-death-hidden")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"R": 3, "W": 1, "C": 1})
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        self.assert_rejected(game, "A", {"action_id": action["id"], "targets": [enemy.ref], "pay": "auto"})
        self.cast_for(game, "A", spell, [chosen.ref])
        self.assertIsNone(self.resolve(game))
        watched = game.state.cards[chosen.object_id].logical_object_id
        self.assertEqual(watched, game.state.damage_redirections[-1].destination.logical_object_id)
        watch, = game.state.delayed_triggers
        self.assertEqual([chosen.object_id], watch.referred_object_ids)
        self.cast_for(game, "B", shock, [loyalty.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.cards[loyalty.object_id].counters["loyalty"])
        self.assertEqual(0, game.state.cards[chosen.object_id].marked_damage)
        if counters:
            self.cast_for(game, "B", poke, [recipient.ref])
            self.assertIsNone(self.resolve(game))
            self.assertEqual(0, game.state.cards[recipient.object_id].marked_damage)
            self.assertEqual(2, game.state.cards[chosen.object_id].marked_damage)
        self.cast_for(game, "B", bolt, ["A"])
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        return game, recipient, enemy, blink

    def _assert_counter_then_draw(self, game, recipient, *, expect_counters):
        hand_count = len(game.state.players["A"].zones["hand"])
        first_event = game.state.event_sequence
        self.choose_trigger(game, [recipient.ref] if expect_counters else [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_count + 1, len(game.state.players["A"].zones["hand"]))
        after = [event.code for event in game.state.events if event.event_id > first_event and
                 event.code in {"counter.add", "card.draw"}]
        self.assertEqual(["counter.add", "counter.add", "card.draw"] if expect_counters else ["card.draw"], after)
        self.assertEqual({"+1/+1": 1, "stun": 2} if expect_counters else {},
                         game.state.cards[recipient.object_id].counters)

    def test_watched_redirect_death_applies_lki_counter_kinds_before_draw_and_replays(self):
        game, recipient, enemy, _ = self._death_scene(6145211)
        self.assert_rejected(game, "C", {"action_id": "choose", "targets": [recipient.ref]})
        self.assert_rejected(game, "A", {"action_id": "choose", "targets": [enemy.ref]})
        game = self.replay(game, load=True)
        self._assert_counter_then_draw(game, recipient, expect_counters=True)
        self.replay(game, load=True)

    def test_watched_death_zero_target_draws_with_or_without_counters(self):
        for counters in (True, False):
            with self.subTest(counters=counters):
                game, recipient, _, _ = self._death_scene(6145220 + counters, counters=counters)
                game = self.replay(game, load=True)
                self._assert_counter_then_draw(game, recipient, expect_counters=False)
                self.replay(game, load=True)

    def test_lost_delayed_target_stops_both_counters_and_draw(self):
        game, recipient, _, blink = self._death_scene(6145231)
        self.choose_trigger(game, [recipient.ref])
        original = game.state.cards[recipient.object_id].logical_object_id
        hand_count = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "B", blink, [recipient.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[recipient.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_count, len(game.state.players["A"].zones["hand"]))
        self.assertEqual({}, game.state.cards[recipient.object_id].counters)
        self.replay(game, load=True)

    def test_illegal_original_spell_target_creates_neither_rule(self):
        game = self.session(6145241)
        spell = self.add(game.engine, "Heroic Sacrifice", zone="hand")
        chosen = self.add(game.engine, "Grizzly Bears")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [chosen.ref])
        self.cast_for(game, "B", blink, [chosen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.damage_redirections)
        self.assertEqual([], game.state.delayed_triggers)
        self.replay(game, load=True)

    def test_legally_changed_spell_target_binds_the_new_incarnation(self):
        game = self.session(6145251)
        spell = self.add(game.engine, "Heroic Sacrifice", zone="hand")
        original = self.add(game.engine, "Grizzly Bears")
        replacement = self.add(game.engine, "Spellskite")
        self.assert_closed(game, replacement)
        self.prepare(game, mana={"C": 1, "W": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [original.ref])
        spell_item = game.state.stack[-1]
        self.priority(game, "A")
        action, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{replacement.ref}:"))
        option, = (item for item in action["cost_options"] if item.get("life_payment"))
        result = game.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                     "targets": [spell_item.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertTrue(game.act("pilot:A", {"action_id": "choose", "choice": "slot:0"}).ok)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(replacement.logical_object_id, game.state.damage_redirections[0].destination.logical_object_id)
        self.assertEqual([replacement.object_id], game.state.delayed_triggers[0].referred_object_ids)
        self.replay(game, load=True)

    def test_watcher_expires_before_a_later_turn_death(self):
        game = self.session(6145261)
        spell = self.add(game.engine, "Heroic Sacrifice", zone="hand")
        chosen = self.add(game.engine, "Grizzly Bears")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [chosen.ref])
        self.assertIsNone(self.resolve(game))
        created = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > created)
        self.assertEqual([], game.state.damage_redirections)
        self.cast_for(game, "B", bolt, [chosen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_missing_counter_kind_mutation_is_detected_by_native_order_witness(self):
        with patch("quorune.rules.watched_death_counter_draw.permanent_counter_snapshot",
                   return_value={"+1/+1": 1}):
            game, recipient, _, _ = self._death_scene(6145271)
        with self.assertRaisesRegex(AssertionError, "counter.add"):
            self._assert_counter_then_draw(game, recipient, expect_counters=True)

    def test_heroic_contract_rejects_unsupported_scope_and_missing_delayed_capability(self):
        from quorune.rules.controller_damage_redirection import WATCHED_DEATH_CAPABILITY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index

        card = self.db.lookup("Heroic Sacrifice", fuzzy=False)
        for text in (card.oracle_text.replace("creatures you control", "permanents you control"),
                     card.oracle_text.replace("up to one", "one")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == WATCHED_DEATH_CAPABILITY).update(
            status="blocked", blockers=["watched departure result unavailable"],
        )
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = {entry["test_id"] for entry in declarations["declarations"]}
        disabled = CapabilityRegistry(raw)
        evidence = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
        disabled.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=disabled))
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=disabled).status)
        gideon = self.db.lookup("Gideon's Sacrifice", fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(gideon, capability_registry=disabled).status)


if __name__ == "__main__":
    unittest.main()

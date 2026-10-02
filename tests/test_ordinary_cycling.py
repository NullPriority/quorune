from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler import cycling_nodes as cycling_nodes_module
from quorune.cycling_abilities import (
    CyclingAbilityError,
    OrdinaryCyclingAbilitySpec,
    compile_ordinary_cycling_ability,
)
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime.cycling_abilities import (
    ordinary_cycling_specs_from_descriptors,
)
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession


def cycling_record(
    base: CardRecord,
    oracle_text: str,
    *,
    keywords: tuple[str, ...] = ("Cycling",),
) -> CardRecord:
    return replace(
        base,
        oracle_id="00000000-0000-4000-8000-000000000029",
        name="Ordinary Cycling Fixture",
        oracle_text=oracle_text,
        keywords=keywords,
        faces=(),
    )


class OrdinaryCyclingModelTests(unittest.TestCase):
    def test_descriptor_is_strict_immutable_and_round_trips(self):
        spec = compile_ordinary_cycling_ability(
            material_line="Cycling {2}{U}",
            oracle_line=(
                "Cycling {2}{U} ({2}{U}, Discard this card: Draw a card.)"
            ),
            line_index=1,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        payload = spec.to_dict()
        payload["mana_cost"]["GENERIC"] = 99
        self.assertEqual(2, spec.mana_cost["GENERIC"])
        self.assertEqual(spec, OrdinaryCyclingAbilitySpec.from_dict(spec.to_dict()))
        ability = spec.to_activated_ability()
        self.assertEqual(("hand",), ability.zones)
        self.assertTrue(ability.discard_source)
        self.assertEqual("Draw a card.", ability.effect_text)
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaises(CyclingAbilityError):
            OrdinaryCyclingAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["mana_cost"]["GENERIC"] = True
        with self.assertRaises(CyclingAbilityError):
            OrdinaryCyclingAbilitySpec.from_dict(malformed)
        descriptor = {
            "handler_id": "ability.activated.cycling.v1",
            "schema_version": 1,
            "event": "activate",
            "ability": spec.to_dict(),
        }
        self.assertEqual(
            (spec,), ordinary_cycling_specs_from_descriptors([descriptor])
        )
        descriptor["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            ordinary_cycling_specs_from_descriptors([descriptor])


class OrdinaryCyclingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.base = cls.db.lookup("Zagoth Triome")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, text: str, *, keywords=("Cycling",)):
        return compile_oracle_card(
            cycling_record(self.base, text, keywords=tuple(keywords)),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_ordinary_cycling_compiles_typed_hand_activation(self):
        text = "Cycling {2}{U} ({2}{U}, Discard this card: Draw a card.)"
        ir = self.compile(text)

        self.assertEqual("exact", ir.status)
        self.assertEqual(1, len(ir.faces[0].nodes))
        node = ir.faces[0].nodes[0]
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("ordinary-cycling-activation-v1", node.template_id)
        self.assertEqual("hand", node.active_zone)
        self.assertEqual("activate", node.event)
        self.assertEqual(
            ("activation.cycling.hand", "zone.draw.library_to_hand"),
            node.capability_dependencies,
        )
        self.assertTrue(node.cost["discard_source"])
        self.assertEqual(2, node.cost["mana"]["GENERIC"])
        self.assertEqual(1, node.cost["mana"]["U"])
        self.assertEqual(text, text[node.span.start : node.span.end])
        self.assertEqual(
            "ability.activated.cycling.v1",
            node.handlers[0]["handler_id"],
        )
        no_reminder = self.compile("Cycling {2}{U}")
        self.assertEqual("exact", no_reminder.status)

    def test_unsupported_cycling_variants_remain_precise_residuals(self):
        for text in (
            "Cycling {X}{2}",
            "Cycling {W/U}",
            "Cycling—Sacrifice a land.",
            "Cycling—Pay 2 life.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertEqual(
                    "unsupported_cycling_cost",
                    ir.faces[0].residuals[0].kind,
                )
        residual_cases = (
            ("Forestcycling {2}", ("Forestcycling",)),
            (
                "Whenever you cycle or discard a card, draw a card.",
                ("Cycling",),
            ),
            (
                "Cycling abilities you activate cost {1} less to activate.",
                ("Cycling",),
            ),
            ("Players can't cycle cards.", ("Cycling",)),
        )
        for text, keywords in residual_cases:
            with self.subTest(text=text):
                self.assertTrue(self.compile(text, keywords=keywords).material_residuals)

    def test_generated_cycling_program_is_capability_closed(self):
        record = cycling_record(self.base, "Cycling {2}")
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        program = registry.get(f"{record.oracle_id}:ability:ab1")
        self.assertIsNotNone(program)
        assert program is not None
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual("hand", program.active_zone)
        self.assertEqual(
            ["activation.cycling.hand", "zone.draw.library_to_hand"],
            program.capability_dependencies,
        )
        self.assertEqual(1, result["runtime_handlers_promoted"])

    def test_ordinary_cycling_compiler_mutant_is_killed(self):
        def assert_exact() -> None:
            ir = self.compile("Cycling {2}")
            node = ir.faces[0].nodes[0]
            self.assertTrue(node.exact)
            self.assertTrue(node.cost["discard_source"])

        assert_exact()
        with mock.patch.object(
            cycling_nodes_module,
            "compile_ordinary_cycling_ability",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class OrdinaryCyclingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def install_generic_program(self, engine, record: CardRecord) -> None:
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(2, result["runtime_handlers_promoted"])
        engine.semantics = registry
        engine._semantic_trust_cache.clear()

    def prepare(
        self,
        session,
        *,
        seat: str = "A",
        active_player: str | None = None,
        mana: int = 3,
        expect_action: bool = True,
    ):
        engine = session.engine
        source = self.card(engine, seat, "Xander's Lounge")
        engine.move_card(source.object_id, "hand", log=False)
        self.install_generic_program(engine, self.db.lookup(source.printed_name))
        engine.state.players[seat].mana_pool["C"] = mana
        engine.state.active_player = active_player or seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()
        action_id = f"activate:{source.ref}:ab3"
        packet = session.packet(f"pilot:{seat}", full=True)
        actions = packet["decision"]["ctx"]["legal"]["actions"]
        action_ids = {action["id"] for action in actions}
        if expect_action:
            self.assertIn(action_id, action_ids)
        else:
            self.assertNotIn(action_id, action_ids)
        return source, action_id

    @staticmethod
    def resolve_until(session, predicate, *, limit: int = 32) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Cycling resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Cycling did not resolve within the bounded loop")

    def test_generic_cycling_discards_before_stack_draw_and_replays_exactly(self):
        session = self.session(7022901)
        engine = session.engine
        source, action_id = self.prepare(session)
        before_draws = len(engine.state.players["A"].draw_history)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(before_draws, len(engine.state.players["A"].draw_history))
        self.assertTrue(engine.state.stack)
        self.resolve_until(
            session,
            lambda: len(engine.state.players["A"].draw_history) == before_draws + 1,
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "ordinary-cycling-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_cycling_discard_replacement_suspends_before_mutation_and_replays(self):
        session = self.session(7022906, players=4)
        engine = session.engine
        source, action_id = self.prepare(session)
        voidwalker_record = self.db.lookup("Dauthi Voidwalker")
        voidwalker = CardInstance(
            object_id="fixture:cycling-voidwalker",
            ref="B-cycling-voidwalker",
            oracle_id=voidwalker_record.oracle_id,
            printed_name=voidwalker_record.name,
            owner="B",
            controller="B",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[voidwalker.object_id] = voidwalker
        engine.state.players["B"].zones["battlefield"].append(
            voidwalker.object_id
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (voidwalker_record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="Cycling discard replacement ordering witness",
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        mana_before = sum(engine.state.players["A"].mana_pool.values())

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual("hand", source.zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            mana_before,
            sum(engine.state.players["A"].mana_pool.values()),
        )
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        assert projected is not None
        selected = projected["ctx"]["options"][0]["id"]

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "cycling-discard-replacement"
            session.save(game_dir)
            restarted = CommanderSession.load(self.db, game_dir)
            restarted_packet = StateProjector(
                self.db, restarted.engine.state
            )._decision("pilot:A")
            assert restarted_packet is not None
            self.assertEqual(
                selected,
                restarted_packet["ctx"]["options"][0]["id"],
            )
            result = restarted.act(
                "pilot:A",
                {"a": "choose", "replacement": selected},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(
                "exile",
                restarted.engine.state.cards[source.object_id].zone,
            )
            self.assertTrue(restarted.engine.state.stack)
            expected_hash = authoritative_state_hash(restarted.engine.state)
            restarted.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_changed_cycling_oracle_fails_closed_without_runtime_reparse(self):
        session = self.session(7022902)
        engine = session.engine
        source, _ = self.prepare(session)
        original_data = engine._effective_card_data

        def changed(card):
            data = dict(original_data(card))
            if getattr(card, "object_id", card) == source.object_id:
                data["executable_oracle_text"] = "Cycling {1}"
                data["activated_abilities"] = []
            return data

        with mock.patch.object(
            engine, "_effective_card_data", side_effect=changed
        ), mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            abilities = engine._activated_abilities(source)
            self.assertFalse(
                any(
                    ability.ability_id == "ab3" or ability.discard_source
                    for ability in abilities
                )
            )

    def test_cycling_descriptor_exists_in_every_zone_but_activates_only_from_hand(self):
        session = self.session(7022904)
        engine = session.engine
        source, action_id = self.prepare(session)

        for zone in ("hand", "battlefield", "graveyard", "exile", "library"):
            with self.subTest(zone=zone):
                engine.move_card(source.object_id, zone, log=False)
                abilities = engine._activated_abilities(source)
                cycling = next(
                    ability for ability in abilities if ability.ability_id == "ab3"
                )
                self.assertEqual(("hand",), cycling.zones)

        engine.move_card(source.object_id, "battlefield", log=False)
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertNotIn(action_id, {action["id"] for action in actions})

    def test_unpayable_cycling_is_not_offered_and_rejects_without_mutation(self):
        session = self.session(7022905)
        engine = session.engine
        source, action_id = self.prepare(
            session,
            mana=0,
            expect_action=False,
        )
        before = authoritative_state_hash(engine.state)

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", source.zone)

    def test_four_player_cycling_draw_is_private_and_seat_scoped(self):
        session = self.session(7022903, players=4)
        engine = session.engine
        source, action_id = self.prepare(session, active_player="B")
        self.assertEqual("B", engine.state.active_player)
        self.assertEqual("A", engine.state.priority_player)
        hidden_before = json.dumps(
            session.packet("pilot:D", full=True), sort_keys=True
        )
        self.assertNotIn(source.object_id, hidden_before)
        drawn_object_id = engine.state.players["A"].zones["library"][-1]
        before = {
            seat: len(player.draw_history)
            for seat, player in engine.state.players.items()
        }

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.resolve_until(
            session,
            lambda: len(engine.state.players["A"].draw_history)
            == before["A"] + 1,
        )
        self.assertEqual(
            before["A"] + 1,
            len(engine.state.players["A"].draw_history),
        )
        for seat in ("B", "C", "D"):
            self.assertEqual(
                before[seat], len(engine.state.players[seat].draw_history)
            )
        opposing = json.dumps(
            session.packet("pilot:D", full=True), sort_keys=True
        )
        self.assertNotIn(drawn_object_id, opposing)


if __name__ == "__main__":
    unittest.main()

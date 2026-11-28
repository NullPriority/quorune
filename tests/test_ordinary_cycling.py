from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, load_assets, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
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
                "Whenever you cycle or exile a card, draw a card.",
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
            "quorune.compiler.activated_ability_catalog.parse_compiled_activated_abilities",
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


class SharkTyphoonCyclingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "variable-cycling-shark.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/variable-cycling-shark-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        leader = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Variable Cycling", [
            DeckEntry(leader, 1, "commander"), DeckEntry("Plains", 30),
        ], [leader])

    def sharks(self, game):
        return tuple(card for card in game.state.cards.values()
                     if card.zone == "battlefield" and card.is_token
                     and "shark" in game.engine._effective_type_parts(card)[1])

    def register_for_profile(self, game, profile, *cards):
        records = tuple(self.db.by_oracle_id(card.oracle_id) for card in cards)
        register_generated_programs(
            self.db, game.engine.semantics, records, trust_level="provisional",
            capability_registry=self.registry, capability_profile=profile,
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )

    def cycling_scene(self, seed, x, profile, *, redirect=False, blue=True):
        game = self.session(seed)
        game.state.config.review_profile = profile
        original_registration = self.register_fixture_cards_on_add
        self.register_fixture_cards_on_add = False
        try:
            card = self.add(game.engine, "Shark Typhoon", zone="hand", ref="OWN-CYCLING-SOURCE")
            support = [self.add(game.engine, "Rest in Peace", seat="B")] if redirect else []
        finally:
            self.register_fixture_cards_on_add = original_registration
        self.register_for_profile(game, profile, card, *support)
        programs = game.engine.semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual({"activate", "card.cycled.self", "spell.cast"}, {program.event for program in programs})
        for program in programs:
            self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
            self.assertEqual(profile, program.capability_closure["profile"])
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1 + x, **({"U": 1} if blue else {})})
        self.checkpoint(game)
        return game, card

    def reject(self, game, seat, payload):
        previous = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, payload)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(previous, authoritative_state_hash(game.state))

    def verify_cycling_value(self, seed, x, profile, *, redirect=False):
        from quorune.rules.cycling_activation_x import PaidCyclingX, RECEIPT_FIELD

        game, card = self.cycling_scene(seed, x, profile, redirect=redirect)
        action = "activate:" + card.ref + ":ab2"
        self.assertIn(action, {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]})
        for invalid in (-1, True, "0", 1 + x):
            self.reject(game, "A", {"action_id": action, "x": invalid, "pay": "auto"})
        self.reject(game, "B", {"action_id": action, "x": x, "pay": "auto"})
        old_library = tuple(game.state.players["A"].zones["library"])
        old_hand = tuple(game.state.players["A"].zones["hand"])
        chosen = game.act("pilot:A", {"action_id": action, "x": x, "pay": "manual",
                                      "payment": {"C": 1 + x, "U": 1}})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("exile" if redirect else "graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertEqual(0, game.state.players["A"].mana_pool["U"])
        cycling, creating = game.state.stack
        self.assertEqual(("activated_ability", "triggered_ability"), (cycling.kind, creating.kind))
        receipt = PaidCyclingX.from_dict(creating.context[RECEIPT_FIELD])
        self.assertEqual((cycling.stack_id, card.ref, x), (receipt.activation_stack_id, receipt.source_ref, receipt.x))
        self.assertEqual(x, cycling.x_value)

        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([cycling.ref], [item.ref for item in game.state.stack])
        self.assertEqual(old_library, tuple(game.state.players["A"].zones["library"]))
        self.assertEqual(tuple(value for value in old_hand if value != card.object_id),
                         tuple(game.state.players["A"].zones["hand"]))
        changes = [event for event in game.state.events if event.code == "token.create"]
        self.assertEqual(1, len(changes))
        self.assertEqual(1, len(changes[0].details["objects"]))
        if x:
            token, = self.sharks(game)
            self.assertEqual((x, x), (game.engine._numeric_stat(token.object_id, "power"),
                                     game.engine._numeric_stat(token.object_id, "toughness")))
            self.assertIn("flying", game.engine._combat_keywords(token))
            self.assertEqual(["U"], list(game.engine._effective_card_data(token)["colors"]))
        else:
            self.assertFalse(self.sharks(game))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(old_library[:-1], tuple(game.state.players["A"].zones["library"]))
        self.assertEqual(old_library[-1], game.state.players["A"].zones["hand"][-1])
        draw_ref = game.state.cards[old_library[-1]].ref
        self.assertIn(draw_ref, json.dumps(game.packet("pilot:A", full=True)))
        for viewer in "BCD":
            self.assertNotIn(draw_ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_shark_cycling_zero_creates_then_loses_its_token_before_draw(self):
        self.verify_cycling_value(702_029_301, 0, "commander_review")

    def test_actual_shark_positive_cycling_retains_x_through_discard_redirection(self):
        self.verify_cycling_value(702_029_302, 2, "traditional", redirect=True)

    def test_actual_shark_cycling_zero_still_requires_the_printed_blue_cost(self):
        game, card = self.cycling_scene(702_029_303, 0, "commander_review", blue=False)
        action = "activate:" + card.ref + ":ab2"
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action, {row["id"] for row in offers})
        self.reject(game, "A", {"action_id": action, "x": 0, "pay": "auto"})
        self.assertEqual("hand", game.state.cards[card.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_shark_noncreature_cast_uses_spell_mana_value_and_replays(self):
        game = self.session(702_029_304)
        self.add(game.engine, "Shark Typhoon")
        ring = self.add(game.engine, "Sol Ring", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", ring, reserve={"C": 1, "G": 1})
        self.assertEqual(2, len(game.state.stack))
        self.resolve_top(game)
        shark, = self.sharks(game)
        self.assertEqual((1, 1), (game.engine._numeric_stat(shark.object_id, "power"),
                                 game.engine._numeric_stat(shark.object_id, "toughness")))
        self.resolve_top(game)
        self.cast(game, "A", bear)
        self.assertEqual(1, len(game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.sharks(game)))
        self.replay(game, load=True)

    def test_shark_variable_price_codec_and_own_trigger_minimum_are_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.cycling_abilities import VARIABLE_CYCLING_CAPABILITY, ordinary_cycling_handler_descriptor
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Shark Typhoon", fuzzy=False)
        cost = compile_ordinary_cycling_ability(
            material_line="Cycling {X}{1}{U}", oracle_line="Cycling {X}{1}{U}", line_index=1,
        )
        descriptor = ordinary_cycling_handler_descriptor(cost)
        self.assertEqual(2, descriptor["schema_version"])
        self.assertEqual((cost,), ordinary_cycling_specs_from_descriptors([descriptor]))
        for bad in (True, 0, 2, None):
            malformed = {**descriptor["ability"], "announced_x_symbols": bad}
            with self.assertRaises(CyclingAbilityError):
                OrdinaryCyclingAbilitySpec.from_dict(malformed)
        with self.assertRaises(ValueError):
            ordinary_cycling_specs_from_descriptors([{**descriptor, "schema_version": 1}])
        report, registry = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        with mock.patch("quorune.compiler.cycling_nodes.cycling_x_token_node", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)
        for program in registry.programs_for_oracle(card.oracle_id):
            if program.event not in {"activate", "card.cycled.self"}:
                continue
            deps = tuple(value for value in program.capability_dependencies if value != VARIABLE_CYCLING_CAPABILITY)
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result["blockers"])
        for altered in (
            card.oracle_text.replace("Cycling {X}{1}{U}", "Cycling {2}{U}"),
            card.oracle_text.replace("When you cycle this card", "Whenever you cycle a card"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=altered), capability_registry=self.registry,
                capability_profile="commander_review",
            ).status)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.life_templates import fixed_life_effect_template
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.compiler import fractional_player_templates as player_result_templates
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.fixed_controller_effect_shapes import (
    fixed_life_node_capabilities,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fixed-player-life-cards.json"
DRAIN_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-player-life-drain-card.json"
)
LIFE_CAPABILITY_ID = "life.change.effect"
TARGET_CAPABILITY_ID = "target.revalidate_resolution"


def trusted_registry(value: dict | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry(
        value
        or json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(text: str, *, type_line: str = "Sorcery") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000119003",
        name="Fixed Player Life Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-player-life.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
            DRAIN_FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedPlayerLifeCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        self.capabilities = trusted_registry(self.registry_value)

    def compile(self, text: str, *, capabilities=None, type_line="Sorcery"):
        return compile_oracle_card(
            fixture_card(text, type_line=type_line),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    @staticmethod
    def life_node(ir):
        return next(
            node
            for face in ir.faces
            for node in face.nodes
            if "cr-119-life" in node.mechanics
        )

    def test_fixed_player_life_templates_are_closed_and_capability_shaped(self):
        cases = (
            (
                "Target player gains 5 life.",
                "gain-life-target-player-v1",
                {"op": "life", "player": "$target.0", "delta": 5},
                "any",
            ),
            (
                "Target player loses 2 life.",
                "lose-life-target-player-v1",
                {"op": "lose_life", "player": "$target.0", "amount": 2},
                "any",
            ),
            (
                "Target opponent loses 3 life.",
                "lose-life-target-opponent-v1",
                {"op": "lose_life", "player": "$target.0", "amount": 3},
                "opponent",
            ),
            (
                "Target opponent loses 3 life and you gain 3 life.",
                "drain-target-opponent-v1",
                {"op": "drain_opponent", "target": "$target.0", "amount": 3},
                "opponent",
            ),
            (
                "Each opponent loses 2 life and you gain 2 life.",
                "drain-each-opponent-v1",
                {"op": "drain_each_opponent", "amount": 2},
                None,
            ),
        )
        for text, template_id, effect, relation in cases:
            with self.subTest(text=text):
                template = fixed_life_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                actual_template, effects, schema, mechanics = template.compiled()
                self.assertEqual(template_id, actual_template)
                self.assertEqual((effect,), effects)
                self.assertEqual(
                    relation,
                    None if schema is None else schema["player_relation"],
                )
                expected_capabilities = {LIFE_CAPABILITY_ID}
                if schema is not None:
                    expected_capabilities.add(TARGET_CAPABILITY_ID)
                self.assertEqual(
                    expected_capabilities,
                    set(
                        fixed_life_node_capabilities(
                            effects=effects,
                            target_schema=schema,
                            mechanic_ids=mechanics,
                        )
                    ),
                )

    def test_spell_and_activated_player_life_nodes_are_exact_and_spanned(self):
        cases = (
            ("Target player gains 5 life.", "Sorcery", "stack"),
            ("{T}: Target player loses 1 life.", "Artifact", "battlefield"),
            (
                "{2}{B}: Target opponent loses 3 life and you gain 3 life.",
                "Creature — Cleric",
                "battlefield",
            ),
            (
                "{1}{B}: Each opponent loses 2 life and you gain 2 life.",
                "Creature — Cleric",
                "battlefield",
            ),
        )
        for text, type_line, active_zone in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = self.life_node(ir)
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertEqual(active_zone, node.active_zone)
                self.assertEqual(text, node.text)
                self.assertIn(LIFE_CAPABILITY_ID, node.capability_dependencies)
                if node.target_schema is not None:
                    self.assertIn(
                        TARGET_CAPABILITY_ID,
                        node.capability_dependencies,
                    )

    def test_dynamic_and_simultaneous_unowned_life_shapes_remain_residual(self):
        for text in (
            "Target player loses 2 life and you gain 2 life.",
            "Target opponent loses 2 life and you gain 3 life.",
        ):
            with self.subTest(leaf_text=text):
                self.assertIsNone(fixed_life_effect_template(text))

        for text in (
            "Target player loses X life.",
            "Each player who controls a Zombie loses 1 life.",
            "Target player's life total becomes 10.",
            "Exchange your life total with this creature's power.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_life_effect_template(text))
                self.assertTrue(self.compile(text).material_residuals)

    def test_life_capability_dependencies_and_compiler_mutation_fail_closed(self):
        text = "Target player gains 5 life."
        for blocked in (LIFE_CAPABILITY_ID, TARGET_CAPABILITY_ID):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item for item in value["capabilities"] if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused player-life dependency mutation"]
                node = self.life_node(
                    self.compile(text, capabilities=trusted_registry(value))
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)

        def assert_exact() -> None:
            ir = self.compile(text)
            nodes = [
                node
                for face in ir.faces
                for node in face.nodes
                if "cr-119-life" in node.mechanics
            ]
            self.assertEqual(1, len(nodes))
            self.assertTrue(nodes[0].exact)

        assert_exact()
        with mock.patch.object(
            player_result_templates,
            "fixed_life_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class EachPlayerLifeCompilerTests(unittest.TestCase):
    """CR 101.4/119.3: one instruction affects all participating players.

    The controller is included, a player who left the game is excluded, and
    the canonical life batch commits before state-based stabilization. This
    grammar does not turn each player's separate object count into one total.
    """

    def test_each_player_fixed_loss_preserves_scope_and_whole_program_admission(self):
        from types import SimpleNamespace
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        registry = load_default_capability_registry()
        for text, type_line in (
            ("Each player loses 2 life.", "Sorcery"),
            ("{2}{B}: Each player loses 1 life.", "Creature — Human"),
            ("{B}, {T}, Discard a card: Each player loses 2 life.", "Creature — Human"),
            ("At the beginning of your upkeep, each player loses 1 life.", "Enchantment"),
        ):
            with self.subTest(text=text):
                card = fixture_card(text, type_line=type_line)
                ir = compile_oracle_card(card, capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("all_active_players", node.effects[0]["scope"])
                self.assertNotIn("player", node.effects[0])
                self.assertIsNone(node.target_schema)
                self.assertEqual(text, node.text)
                program = compile_best_available_card_program(
                    SimpleNamespace(rulings=lambda _record: ()), card,
                    semantic_registry=SemanticRegistry(), capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertTrue(bind_card_program_runtime(
                    program, capability_registry=registry, profile="commander_review",
                )["strict_capability_ready"])

    def test_each_player_global_quantity_uses_one_shared_public_count(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(fixture_card(
            "{T}: Each player loses 1 life for each Zombie on the battlefield.",
            type_line="Creature — Zombie",
        ), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effect = ir.faces[0].nodes[0].effects[0]
        self.assertEqual("all_active_players", effect["scope"])
        self.assertEqual("all_zones", effect["amount"]["quantity"]["scope"])
        self.assertEqual(["zombie"], effect["amount"]["quantity"]["query"]["subtypes_all"])

    def test_each_player_shapes_and_separate_player_quantities_fail_closed(self):
        registry = load_default_capability_registry()
        for text in (
            "Each player loses 1 life for each creature they control.",
            "Each player who controls a Zombie loses 1 life.",
            "{B}: Each player loses X life.",
            "Each player loses 1 life and you gain that much life.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(
                    fixture_card(text, type_line="Creature — Human"),
                    capability_registry=registry,
                ).status)
        for effect in (
            {"op": "lose_life", "scope": "all_active_players", "amount": True},
            {"op": "lose_life", "scope": "all_active_players", "player": "$controller", "amount": 1},
            {"op": "lose_life", "scope": "some_players", "amount": 1},
        ):
            self.assertEqual((), fixed_life_node_capabilities(
                effects=(effect,), target_schema=None,
                mechanic_ids=("cr-119-life", "cr-101-the-magic-golden-rules"),
            ))

    def test_each_player_producer_and_authority_omissions_fail_closed(self):
        text = "Each player loses 1 life."
        registry = load_default_capability_registry()
        with mock.patch.object(player_result_templates, "fixed_life_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                fixture_card(text), capability_registry=registry,
            ).status)
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        row = next(row for row in value["capabilities"] if row["id"] == LIFE_CAPABILITY_ID)
        row["status"] = "blocked"
        row["blockers"] = ["focused all-player loss authority omission"]
        self.assertNotEqual("exact", compile_oracle_card(
            fixture_card(text), capability_registry=CapabilityRegistry(value),
        ).status)


class FixedPlayerLifeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(self, session, *, name: str, ref: str, seat="B", zone="hand"):
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=[seat] if zone == "hand" else list(engine.seats),
            revealed_to=[] if zone == "hand" else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat="B") -> None:
        engine = session.engine
        engine.state.active_player = seat
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

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(12):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Fixed player-life effect did not resolve")

    def test_target_player_gain_uses_live_replacement_owner(self):
        session = self.session(11903001, players=2)
        engine = session.engine
        spell = self.add_card(
            session,
            name="Soothing Balm",
            ref="LIFE-GAIN",
        )
        self.add_card(
            session,
            name="Boon Reflection",
            ref="LIFE-BOON-A",
            seat="A",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool["W"] = 2
        self.prepare_main(session)

        action = next(
            row
            for row in engine._priority_action_hints("B")["actions"]
            if row.get("card") == spell.ref
        )
        self.assertEqual({"A", "B"}, set(action["target_schema"]["legal_refs"]))
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": spell.ref, "targets": ["A"], "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(50, engine.state.players["A"].life)
        self.assertEqual("graveyard", spell.zone)

    def test_target_opponent_drain_replacement_and_replay_are_exact(self):
        session = self.session(11903002, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            name="Specter of the Fens",
            ref="LIFE-DRAIN",
            zone="battlefield",
        )
        self.add_card(
            session,
            name="Boon Reflection",
            ref="LIFE-BOON-B",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool["B"] = 6
        self.prepare_main(session)

        action = next(
            row
            for row in engine._priority_action_hints("B")["actions"]
            if row.get("source") == source.ref
            and row.get("ability") == "ab2"
        )
        self.assertNotIn("B", action["target_schema"]["legal_refs"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"action_id": action["id"], "targets": ["A"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual(38, engine.state.players["A"].life)
        self.assertEqual(44, engine.state.players["B"].life)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-player-life-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_each_opponent_drain_is_one_four_player_life_batch(self):
        session = self.session(11903003, players=4)
        engine = session.engine
        kaya = self.add_card(
            session,
            name="Kaya, Ghost Assassin",
            ref="LIFE-KAYA",
            zone="battlefield",
        )
        kaya.counters["loyalty"] = 5
        self.add_card(
            session,
            name="Boon Reflection",
            ref="LIFE-BOON-TABLE",
            zone="battlefield",
        )
        self.prepare_main(session)

        action = next(
            row
            for row in engine._priority_action_hints("B")["actions"]
            if row.get("source") == kaya.ref and row.get("ability") == "ab2"
        )
        engine.permissions.invalidate_current()
        engine._activate("B", action)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(4, kaya.counters["loyalty"])
        self.assertEqual(44, engine.state.players["B"].life)
        self.assertEqual(
            {"A": 38, "C": 38, "D": 38},
            {
                seat: engine.state.players[seat].life
                for seat in ("A", "C", "D")
            },
        )


class EachPlayerLifeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "each-player-life.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/each-player-life-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Each player life", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def assert_whole_admission(self, name):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertTrue(bind_card_program_runtime(
            program, capability_registry=self.registry, profile="commander_review",
        )["strict_capability_ready"])

    def all_player_life_event(self, session):
        return next(event for event in reversed(session.state.events)
                    if event.code == "effect.life" and "players" in event.details)

    def test_original_guildmage_includes_controller_and_stabilizes_one_batch_after_concession(self):
        session = self.session(342001)
        engine = session.engine
        source = self.add(engine, "Orzhov Guildmage")
        self.assert_whole_admission(source.printed_name)
        self.ready(session, source, {"B": 3})
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":") and row.get("ability") == "ab2")
        for seat, life in zip("ABCD", (3, 1, 1, 3)):
            session.state.players[seat].life = life
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual([3, 1, 1, 3], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(0, session.state.players["A"].mana_pool.get("B", 0))
        for seat in "ABC":
            self.assertTrue(session.act("pilot:" + seat, {"action_id": "pass"}).ok)
        conceded = session.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([2, 0, 0, 3], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual([True, False, False, False], [session.state.players[seat].in_game for seat in "ABCD"])
        event = self.all_player_life_event(session)
        self.assertEqual(["A", "B", "C"], event.details["players"])
        self.assertEqual({"A", "B", "C"}, {row["player"] for row in event.details["life_events"]})
        self.assertEqual(3, len(event.details["life_events"]))
        self.replay(session, load=True)

    def test_original_shepherd_uses_global_resolution_count_and_zero_after_source_bounce(self):
        for amount in (4, 0):
            with self.subTest(amount=amount):
                session = self.session(342010 + amount)
                engine = session.engine
                source = self.add(engine, "Shepherd of Rot")
                source.acquired_control_turn_count = -1
                self.add(engine, "Generic Bound Body", seat="D", ref="non-zombie")
                if amount:
                    self.add(engine, "Shepherd of Rot", seat="B", ref="foreign-zombie-B")
                    self.add(engine, "Shepherd of Rot", seat="C", ref="foreign-zombie-C")
                    kindred = self.add(engine, "Generic Kindred Zombie Enchantment", seat="D", ref="foreign-zombie-kindred")
                    self.assert_whole_admission(kindred.printed_name)
                bounce = self.add(engine, "Unsummon", zone="hand")
                self.assert_whole_admission(source.printed_name)
                action = self.ready(session, source, {"U": 1})
                self.checkpoint(session)
                accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                if not amount:
                    actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    response = next(row for row in actions if row.get("card") == bounce.ref)
                    accepted = session.act("pilot:A", {"action_id": response["id"], "targets": [source.ref], "pay": "auto"})
                    self.assertTrue(accepted.ok, accepted.summary)
                self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual([40 - amount] * 4, [session.state.players[seat].life for seat in "ABCD"])
                event = self.all_player_life_event(session)
                self.assertEqual(list("ABCD"), event.details["players"])
                self.assertEqual(amount, event.details["requested_amount"])
                if not amount:
                    self.assertEqual("hand", session.state.cards[source.object_id].zone)
                    self.assertEqual(0, session.state.players["A"].mana_pool.get("U", 0))
                self.replay(session, load=True)

    def test_original_supplicant_pays_private_discard_before_all_player_loss(self):
        session = self.session(342020)
        source = self.add(session.engine, "Skirsdag Supplicant")
        source.acquired_control_turn_count = -1
        chosen = self.add(session.engine, "Generic Bound Plains", zone="hand", ref="discard-price")
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": action["id"], "cost_cards": [chosen.ref], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [chosen.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[chosen.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("B", 0))
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in "ABCD"])
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn("hand", packet["state"]["players"]["A"])
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([38] * 4, [session.state.players[seat].life for seat in "ABCD"])
        self.replay(session, load=True)

    def test_original_smallpox_keeps_zero_life_players_in_resolution_choices(self):
        """CR 704.4: life, discard, creature and land happen before SBAs."""
        session = self.session(342030)
        engine = session.engine
        creatures = {}
        lands = {}
        discard = {}
        for seat in "ABCD":
            creatures[seat] = self.add(engine, "Generic Bound Body", seat=seat, ref="pox-creature-" + seat)
            lands[seat] = self.add(engine, "Generic Bound Plains", seat=seat, ref="pox-land-" + seat)
            discard[seat] = self.add(engine, "Generic Bound Plains", seat=seat, zone="hand", ref="pox-discard-" + seat)
            session.state.players[seat].life = 1 if seat in "BC" else 2
        source = self.add(engine, "Smallpox", zone="hand")
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {"B": 2})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual([1, 0, 0, 1], [session.state.players[seat].life for seat in "ABCD"])
        self.assertTrue(all(session.state.players[seat].in_game for seat in "ABCD"))
        for group, selected in (("discard", discard), ("creature", creatures), ("land", lands)):
            for seat in "ABCD":
                with self.subTest(group=group, seat=seat):
                    self.assertEqual("pilot:" + seat, session.pending_principals()[0])
                    self.assertTrue(session.state.players[seat].in_game)
                    if group == "discard":
                        for other in "ABCD":
                            if other != seat:
                                self.assertIsNone(session.packet("pilot:" + other, full=True)["decision"])
                    if group == "creature" and seat == "B":
                        self.replay(session, load=True)
                    accepted = session.act("pilot:" + seat, {"action_id": "choose", "cards": [selected[seat].ref]})
                    self.assertTrue(accepted.ok, accepted.summary)
                    if group != "land" or seat != "D":
                        self.assertTrue(all(session.state.players[player].in_game for player in "ABCD"))
        self.assertFalse(session.state.players["B"].in_game)
        self.assertFalse(session.state.players["C"].in_game)
        self.assertTrue(session.state.players["A"].in_game)
        self.assertTrue(session.state.players["D"].in_game)
        for seat in "AD":
            for selected in (discard, creatures, lands):
                self.assertEqual("graveyard", session.state.cards[selected[seat].object_id].zone)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_original_caustic_death_trigger_keeps_departed_controller_and_all_players(self):
        session = self.session(342040)
        engine = session.engine
        observer = self.add(engine, "Caustic Hound", seat="B")
        source = self.add(engine, "Murder", zone="hand")
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, source, {"B": 3})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [observer.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual("graveyard", session.state.cards[observer.object_id].zone)
        self.assertEqual([36] * 4, [session.state.players[seat].life for seat in "ABCD"])
        event = self.all_player_life_event(session)
        self.assertEqual("B", event.actor)
        self.assertEqual(list("ABCD"), event.details["players"])
        self.assertEqual({"B"}, {row["source_controller"] for row in event.details["life_events"]})
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()

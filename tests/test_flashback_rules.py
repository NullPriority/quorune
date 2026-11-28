from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from inherited_composition_fixture import InheritedNativeFixture
from quorune.compiler import flashback_nodes as flashback_nodes_module
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.flashback import (
    compile_fixed_mana_flashback,
    FixedManaFlashbackSpec,
    FlashbackError,
    flashback_handler_descriptor,
    FLASHBACK_CAPABILITY_ID,
    FLASHBACK_CAST_ANNOTATION,
    FLASHBACK_CAST_OPTION_ID,
    FLASHBACK_HANDLER_ID,
    SACRIFICE_FLASHBACK_CAPABILITY_ID,
    SACRIFICE_FLASHBACK_HANDLER_ID,
    FixedSacrificeFlashbackSpec,
)
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.flashback import default_fixed_mana_flashback_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "flashback-cards.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "flashback.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedManaFlashbackModelTests(unittest.TestCase):
    def test_flashback_descriptor_and_registry_are_strict(self):
        spec = compile_fixed_mana_flashback(
            material_line="Flashback {2}{U}",
            oracle_line=(
                "Flashback {2}{U} (You may cast this card from your graveyard "
                "for its flashback cost. Then exile it.)"
            ),
            line_index=1,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual("ab2", spec.ability_id)
        self.assertEqual(
            {
                "GENERIC": 2,
                "W": 0,
                "U": 1,
                "B": 0,
                "R": 0,
                "G": 0,
                "C": 0,
            },
            dict(spec.to_dict()["mana_cost"]),
        )
        self.assertEqual(spec, FixedManaFlashbackSpec.from_dict(spec.to_dict()))
        self.assertEqual(
            (spec,),
            default_fixed_mana_flashback_registry().lower(
                flashback_handler_descriptor(spec),
                None,
            ),
        )
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(FlashbackError, "closed schema"):
            FixedManaFlashbackSpec.from_dict(malformed)

        life_spec = compile_fixed_mana_flashback(
            material_line="Flashback—{1}{U}, Pay 3 life",
            oracle_line=(
                "Flashback—{1}{U}, Pay 3 life. (You may cast this card from "
                "your graveyard for its flashback cost. Then exile it.)"
            ),
            line_index=1,
        )
        self.assertIsNotNone(life_spec)
        assert life_spec is not None
        self.assertEqual(3, life_spec.life_payment)
        self.assertEqual(
            [
                {
                    "schema_version": 1,
                    "kind": "fixed_life_payment",
                    "amount": 3,
                }
            ],
            life_spec.cast_cost_option()["_additional_option_costs"],
        )


class FixedManaFlashbackCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Think Twice")
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record=None, *, capabilities=None):
        return compile_oracle_card(
            record or self.record,
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    @staticmethod
    def flashback_node(ir):
        return next(
            node
            for node in ir.faces[0].nodes
            if "flashback" in node.mechanics
        )

    def test_fixed_mana_flashback_compiles_source_spanned_typed_program(self):
        ir = self.compile()
        node = self.flashback_node(ir)
        self.assertTrue(node.exact, ir.material_residuals)
        self.assertEqual("keyword_ability", node.kind)
        self.assertEqual("all", node.active_zone)
        self.assertEqual("cast.cost", node.event)
        self.assertEqual("ordinary-fixed-mana-flashback-v1", node.template_id)
        self.assertEqual((FLASHBACK_CAPABILITY_ID,), node.capability_dependencies)
        self.assertEqual(FLASHBACK_HANDLER_ID, node.handlers[0]["handler_id"])
        self.assertEqual(
            self.record.oracle_text[node.span.start : node.span.end],
            node.text,
        )

        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            (self.record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        card_program = registry.card_program_for_oracle(self.record.oracle_id)
        self.assertIsNotNone(card_program)
        assert card_program is not None
        self.assertTrue(card_program.trust_closure["trusted"])
        program = next(
            program
            for program in registry.programs_for_oracle(self.record.oracle_id)
            if program.event == "cast.cost"
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual(
            "exact",
            program.provenance["card_program_admission"]["oracle_ir_status"],
        )

    def test_nonordinary_flashback_costs_remain_residual(self):
        unsupported = (
            self.db.lookup("Devil's Play"),
            replace(
                self.record,
                oracle_text=(
                    "Draw a card.\n"
                    "Flashback—Sacrifice a creature. (You may cast this card "
                    "from your graveyard for its flashback cost. Then exile it.)"
                ),
            ),
        )
        for record in unsupported:
            with self.subTest(name=record.name):
                ir = self.compile(record)
                node = self.flashback_node(ir)
                self.assertFalse(node.exact)
                self.assertFalse(node.lowerable)
                self.assertEqual("ordinary-flashback-residual-v1", node.template_id)
                self.assertTrue(ir.material_residuals)

    def test_actual_dread_return_cost_binding_and_dependency_omission(self):
        record = self.db.lookup("Dread Return")
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = self.flashback_node(ir)
        self.assertEqual(SACRIFICE_FLASHBACK_HANDLER_ID, node.handlers[0]["handler_id"])
        self.assertEqual((SACRIFICE_FLASHBACK_CAPABILITY_ID,), node.capability_dependencies)
        self.assertEqual(record.oracle_text[node.span.start:node.span.end], node.text)
        spec = FixedSacrificeFlashbackSpec.from_dict(node.handlers[0]["flashback"])
        option = spec.cast_cost_option()
        self.assertFalse(any(option["requirements"].values()))
        self.assertEqual(3, option["_additional_option_costs"][0]["count"])
        with self.assertRaises(FlashbackError):
            FixedSacrificeFlashbackSpec.from_dict({**spec.to_dict(), "count": 2})
        for blocked in (SACRIFICE_FLASHBACK_CAPABILITY_ID, FLASHBACK_CAPABILITY_ID,
                        "casting.additional_cost.fixed_sacrifice"):
            value = deepcopy(self.registry_value)
            row = next(row for row in value["capabilities"] if row["id"] == blocked)
            row.update(status="blocked", blockers=["focused Dread Return mutation"])
            registry = CapabilityRegistry(value)
            registry.mark_evidence_verified(self.capabilities.evidence_fingerprint)
            with self.subTest(blocked=blocked):
                self.assertFalse(self.flashback_node(self.compile(record, capabilities=registry)).exact)
        with mock.patch.object(flashback_nodes_module, "compile_fixed_sacrifice_flashback", return_value=None):
            self.assertTrue(self.compile(record).material_residuals)

    def test_fixed_life_flashback_adds_the_existing_payment_capability(self):
        ir = self.compile(self.db.lookup("Deep Analysis"))
        node = self.flashback_node(ir)
        self.assertTrue(node.exact, ir.material_residuals)
        self.assertEqual(
            (
                "casting.additional_cost.fixed_life_payment",
                FLASHBACK_CAPABILITY_ID,
            ),
            node.capability_dependencies,
        )
        spec = FixedManaFlashbackSpec.from_dict(
            node.handlers[0]["flashback"]
        )
        self.assertEqual(3, spec.life_payment)

    def test_discard_and_mana_flashback_have_separate_source_spans(self):
        record = CardRecord(
            oracle_id="ordinary-discard-with-flashback", name="Example Instant",
            mana_cost="{2}{R}", mana_value=3, type_line="Instant",
            oracle_text=(
                "As an additional cost to cast this spell, discard a card.\n"
                "Draw two cards.\nFlashback {3}{R}"
            ),
            power=None, toughness=None, loyalty=None, defense=None, colors=("R",),
            color_identity=("R",), keywords=("Flashback",), produced_mana=(),
            layout="normal", released_at=None, legalities={}, faces=(), raw={},
        )
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        payment, flashback = ir.faces[0].nodes
        self.assertEqual("resolve", payment.event)
        self.assertEqual("discard_cards", payment.cost["additional_costs"][0]["choice_field"])
        self.assertEqual("cast.cost", flashback.event)
        for node in (payment, flashback):
            self.assertEqual(node.text, record.oracle_text[node.span.start:node.span.end])
        for unsupported in (
            record.oracle_text.replace("{3}{R}", "{X}{R}"),
            record.oracle_text.replace("Draw two cards.", "Target creature sings."),
        ):
            self.assertNotEqual("exact", self.compile(replace(record, oracle_text=unsupported)).status)

    def test_flashback_dependency_and_compiler_mutations_fail_closed(self):
        capability = next(
            row
            for row in self.registry_value["capabilities"]
            if row["id"] == FLASHBACK_CAPABILITY_ID
        )
        for blocked in (FLASHBACK_CAPABILITY_ID, *capability["dependencies"]):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item for item in value["capabilities"] if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused Flashback dependency mutation"]
                node = self.flashback_node(
                    self.compile(capabilities=CapabilityRegistry(value))
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)

        value = deepcopy(self.registry_value)
        life_payment = next(
            item
            for item in value["capabilities"]
            if item["id"] == "casting.additional_cost.fixed_life_payment"
        )
        life_payment["status"] = "blocked"
        life_payment["blockers"] = ["focused Flashback life-payment mutation"]
        life_node = self.flashback_node(
            self.compile(
                self.db.lookup("Deep Analysis"),
                capabilities=CapabilityRegistry(value),
            )
        )
        self.assertFalse(life_node.exact)
        self.assertTrue(life_node.residual_ids)

        def assert_exact() -> None:
            node = self.flashback_node(self.compile())
            self.assertTrue(node.exact)
            self.assertEqual(FLASHBACK_HANDLER_ID, node.handlers[0]["handler_id"])

        assert_exact()
        with mock.patch.object(
            flashback_nodes_module,
            "compile_fixed_mana_flashback",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedManaFlashbackRuntimeTests(unittest.TestCase):
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

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "B",
        zone: str = "graveyard",
        controller: str | None = None,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats) if zone != "hand" else [seat],
            revealed_to=list(engine.seats) if zone != "hand" else [],
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
    def prepare_main(session, seat: str = "B") -> None:
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
    def cast_action(engine, card, seat: str = "B"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("card") == card.ref and action.get("action") == "cast"
        )

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Flashed-back spell did not resolve")

    def issued_cast_action(self, session, card, seat="B"):
        before = authoritative_state_hash(session.state)
        packet = session.packet(f"pilot:{seat}", full=True)
        actions = packet["decision"]["ctx"]["legal"]["actions"]
        self.assertEqual(before, authoritative_state_hash(session.state))
        if session.commands:
            self.assertEqual(session.commands[-1]["after_state_hash"], before)
        return next(action for action in actions
                    if action.get("action") == "cast" and action.get("card") == card.ref)

    def test_actual_dread_return_pays_three_sacrifices_and_replays(self):
        session = self.session(70234010, players=4)
        engine = session.engine
        spell = self.add_card(session, name="Dread Return", ref="DREAD-SPELL", zone="hand")
        first = self.add_card(session, name="Grizzly Bears", ref="DREAD-FIRST")
        second = self.add_card(session, name="Grizzly Bears", ref="DREAD-SECOND")
        fuel = [self.add_card(session, name="Grizzly Bears", ref=f"DREAD-PAY-{i}", zone="battlefield")
                for i in range(3)]
        foreign = self.add_card(session, name="Grizzly Bears", ref="DREAD-FOREIGN",
                                seat="C", zone="battlefield")
        land = self.add_card(session, name="Swamp", ref="DREAD-LAND", zone="battlefield")
        engine.state.players["B"].mana_pool["B"] = 4
        self.prepare_main(session)
        normal = self.issued_cast_action(session, spell)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.assertEqual({"normal"}, {option["id"] for option in normal["cost_options"]})
        receipt = session.act("pilot:B", {"action_id": normal["id"], "targets": [first.ref], "pay": "auto"})
        self.assertTrue(receipt.ok, receipt.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual("graveyard", spell.zone)
        self.assertEqual("battlefield", first.zone)
        self.assertEqual("B", first.controller)
        self.assertTrue(all(card.zone == "battlefield" for card in fuel))
        self.assertFalse(any(engine.state.players["B"].mana_pool.values()))

        flashback = self.issued_cast_action(session, spell)
        option, = flashback["cost_options"]
        self.assertEqual(FLASHBACK_CAST_OPTION_ID, option["id"])
        self.assertFalse(any(option["requirements"].values()))
        schema = option["choice_schema"]["sacrifice_cards"]
        self.assertEqual(3, schema["count"])
        self.assertEqual({first.ref, *(card.ref for card in fuel)}, set(schema["legal_refs"]))
        refs = [card.ref for card in fuel]
        command = {"action_id": flashback["id"], "cost_option": FLASHBACK_CAST_OPTION_ID,
                   "targets": [second.ref], "pay": "auto"}
        for values in (refs[:2], [refs[0], refs[0], refs[2]],
                       [*refs[:2], foreign.ref], [*refs[:2], land.ref]):
            before = authoritative_state_hash(engine.state)
            rejected = session.act("pilot:B", {**command, "sacrifice_cards": values})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
        receipt = session.act("pilot:B", {**command, "sacrifice_cards": refs})
        self.assertTrue(receipt.ok, receipt.summary)
        spell = engine.state.cards[spell.object_id]
        second = engine.state.cards[second.object_id]
        fuel = [engine.state.cards[card.object_id] for card in fuel]
        self.assertEqual("stack", spell.zone)
        self.assertIs(True, spell.annotations.get(FLASHBACK_CAST_ANNOTATION))
        self.assertTrue(all(card.zone == "graveyard" for card in fuel))
        self.assertEqual("graveyard", second.zone)
        self.resolve_stack_with_passes(session)
        self.assertEqual("battlefield", second.zone)
        self.assertEqual("B", second.controller)
        self.assertEqual("exile", spell.zone)
        self.assertNotIn(FLASHBACK_CAST_ANNOTATION, spell.annotations)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertNotIn(spell.object_id, packet)
        self.assert_session_replays(session)

    def assert_session_replays(self, session):
        expected = authoritative_state_hash(session.engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "dread-return-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_actual_dread_return_requires_existing_graveyard_target_and_full_payment(self):
        session = self.session(70234011, players=2)
        engine = session.engine
        spell = self.add_card(session, name="Dread Return", ref="DREAD-BLOCKED")
        fuel = [self.add_card(session, name="Grizzly Bears", ref=f"DREAD-BLOCKED-{index}",
                              zone="battlefield") for index in range(3)]
        self.prepare_main(session)
        self.assertFalse(any(action.get("card") == spell.ref
                             for action in engine._priority_action_hints("B")["actions"]))
        self.add_card(session, name="Grizzly Bears", ref="DREAD-TARGET")
        engine.move_card(fuel[0].object_id, "exile", reason="fixture setup")
        self.prepare_main(session)
        self.assertFalse(any(action.get("card") == spell.ref
                             for action in engine._priority_action_hints("B")["actions"]))
        self.add_card(session, name="Grizzly Bears", ref="DREAD-BLOCKED-RESERVE", zone="battlefield")
        self.prepare_main(session, "A")
        self.assertFalse(any(action.get("card") == spell.ref
                             for action in engine._priority_action_hints("A")["actions"]))
        self.prepare_main(session)
        self.assertEqual(FLASHBACK_CAST_OPTION_ID, self.cast_action(engine, spell)["cost_options"][0]["id"])

    def test_actual_dread_return_loses_target_and_still_exiles(self):
        session = self.session(70234012, players=2)
        engine = session.engine
        spell = self.add_card(session, name="Dread Return", ref="DREAD-FIZZLE")
        target = self.add_card(session, name="Grizzly Bears", ref="DREAD-FIZZLE-TARGET")
        fuel = [self.add_card(session, name="Grizzly Bears", ref=f"DREAD-FIZZLE-PAY-{i}", zone="battlefield")
                for i in range(3)]
        removal = self.add_card(session, name="Purify the Grave", ref="DREAD-RESPONSE", seat="A", zone="hand")
        engine.state.players["A"].mana_pool["W"] = 1
        self.prepare_main(session)
        action = self.issued_cast_action(session, spell)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act("pilot:B", {"action_id": action["id"], "targets": [target.ref],
                                          "sacrifice_cards": [card.ref for card in fuel], "pay": "auto",
                                          "cost_option": FLASHBACK_CAST_OPTION_ID})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.act("pilot:B", {"action_id": "pass"}).ok)
        response = self.issued_cast_action(session, removal, "A")
        accepted = session.act("pilot:A", {"action_id": response["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual("exile", target.zone)
        self.assertEqual("exile", spell.zone)
        self.assertTrue(all(card.zone == "graveyard" for card in fuel))
        self.assert_session_replays(session)

    def test_flashback_cast_resolves_to_exile_and_replays(self):
        session = self.session(70234001, players=4)
        engine = session.engine
        card = self.add_card(session, name="Think Twice", ref="FLASH1")
        engine.state.players["B"].mana_pool["U"] = 3
        self.prepare_main(session)

        action = self.cast_action(engine, card)
        options = {row["id"]: row for row in action["cost_options"]}
        self.assertEqual({FLASHBACK_CAST_OPTION_ID}, set(options))
        self.assertEqual(2, options[FLASHBACK_CAST_OPTION_ID]["requirements"]["GENERIC"])
        self.assertEqual(1, options[FLASHBACK_CAST_OPTION_ID]["requirements"]["U"])
        self.assertFalse(
            any(
                row.get("card") == card.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        starting_hand = len(engine.state.players["B"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"action_id": action["id"], "cost_option": FLASHBACK_CAST_OPTION_ID},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", card.zone)
        self.assertIs(True, card.annotations.get(FLASHBACK_CAST_ANNOTATION))
        self.resolve_stack_with_passes(session)

        self.assertEqual("exile", card.zone)
        self.assertNotIn(FLASHBACK_CAST_ANNOTATION, card.annotations)
        self.assertEqual(
            starting_hand + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertNotIn(card.object_id, packet)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "flashback-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_laughing_mad_both_cast_routes_require_discard_and_replay(self):
        for index, (origin, option_id, generic, destination) in enumerate((
            ("hand", "normal", 2, "graveyard"),
            ("graveyard", FLASHBACK_CAST_OPTION_ID, 3, "exile"),
        )):
            with self.subTest(origin=origin):
                session = self.session(70234010 + index)
                engine = session.engine
                spell = self.add_card(session, name="Laughing Mad", zone=origin, ref="laughing-mad")
                payment = self.add_card(session, name="Think Twice", zone="hand", ref="laughing-payment")
                player = engine.state.players["B"]
                player.mana_pool["R"] = generic + 1
                self.prepare_main(session)
                action = self.cast_action(engine, spell)
                options = {row["id"]: row for row in action["cost_options"]}
                self.assertEqual({option_id}, set(options))
                option = options[option_id]
                self.assertEqual(generic, option["requirements"]["GENERIC"])
                self.assertEqual(1, option["requirements"]["R"])
                offered_payment = option["choice_schema"]["discard_cards"]
                self.assertIn(payment.ref, offered_payment["legal_refs"])
                self.assertNotIn(spell.ref, offered_payment["legal_refs"])
                starting_hand = len(player.zones["hand"])
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                before = authoritative_state_hash(engine.state)
                request = {"action_id": action["id"], "cost_option": option_id}
                rejected = session.act("pilot:B", {**request, "discard_cards": [spell.ref]})
                self.assertFalse(rejected.ok, rejected.summary)
                self.assertEqual(before, authoritative_state_hash(engine.state))
                cast = session.act("pilot:B", {**request, "discard_cards": [payment.ref]})
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual("stack", engine.state.cards[spell.object_id].zone)
                self.assertEqual("graveyard", engine.state.cards[payment.object_id].zone)
                self.assertEqual(0, engine.state.players["B"].mana_pool["R"])
                self.resolve_stack_with_passes(session)
                self.assertEqual(destination, engine.state.cards[spell.object_id].zone)
                self.assertEqual(starting_hand + (origin == "graveyard"), len(engine.state.players["B"].zones["hand"]))
                with tempfile.TemporaryDirectory() as temporary:
                    game_dir = Path(temporary) / "laughing-mad-replay"
                    session.save(game_dir)
                    replay = replay_record(game_dir, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(authoritative_state_hash(engine.state), replay["final_state_hash"])

    def test_countered_flashback_uses_self_replacement_before_competing_destination(self):
        session = self.session(70234002, players=4)
        engine = session.engine
        card = self.add_card(session, name="Think Twice", ref="FLASH2")
        voidwalker = self.add_card(
            session,
            name="Dauthi Voidwalker",
            ref="FLASH-VOIDWALKER",
            seat="A",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool["U"] = 3
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {
                "card": card.ref,
                "from": "graveyard",
                "cost_option": FLASHBACK_CAST_OPTION_ID,
                "pay": "auto",
            },
        )
        item = engine.state.stack[-1]
        before_events = len(engine.state.events)
        engine._counter_stack_item(
            item.ref,
            reason="Flashback counter interaction",
            countered_by="A",
        )

        self.assertEqual("exile", card.zone)
        applied = [
            event
            for event in engine.state.events[before_events:]
            if event.code == "replacement.apply"
        ]
        self.assertEqual([card.ref], [event.details["source"] for event in applied])
        self.assertFalse(
            any(event.details.get("source") == voidwalker.ref for event in applied)
        )

    def test_flashback_and_independent_graveyard_permission_offer_distinct_costs(self):
        session = self.session(70234003, players=2)
        engine = session.engine
        card = self.add_card(session, name="Think Twice", ref="FLASH3")
        card.annotations["cast_from"] = ["graveyard"]
        engine.state.players["B"].mana_pool["U"] = 3
        self.prepare_main(session)
        action = self.cast_action(engine, card)
        self.assertEqual(
            {"normal", FLASHBACK_CAST_OPTION_ID},
            {row["id"] for row in action["cost_options"]},
        )

    def test_fixed_life_flashback_pays_life_and_exiles(self):
        session = self.session(70234007, players=2)
        engine = session.engine
        card = self.add_card(session, name="Deep Analysis", ref="FLASH7")
        engine.state.players["B"].mana_pool["U"] = 2
        self.prepare_main(session)
        starting_life = engine.state.players["B"].life
        starting_hand = len(engine.state.players["B"].zones["hand"])
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {
                "card": card.ref,
                "from": "graveyard",
                "cost_option": FLASHBACK_CAST_OPTION_ID,
                "targets": ["B"],
                "pay": "auto",
            },
        )
        self.assertEqual(starting_life - 3, engine.state.players["B"].life)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("exile", card.zone)
        self.assertEqual(
            starting_hand + 2,
            len(engine.state.players["B"].zones["hand"]),
        )

    def test_normal_cast_does_not_apply_flashback_departure(self):
        session = self.session(70234006, players=2)
        engine = session.engine
        card = self.add_card(
            session,
            name="Think Twice",
            ref="FLASH6",
            zone="hand",
        )
        engine.state.players["B"].mana_pool["U"] = 2
        self.prepare_main(session)
        action = self.cast_action(engine, card)
        self.assertEqual(
            {"normal"},
            {row["id"] for row in action["cost_options"]},
        )
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": card.ref, "cost_option": "normal", "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", card.zone)
        self.assertNotIn(FLASHBACK_CAST_ANNOTATION, card.annotations)

    def test_partial_card_withholds_flashback_permission(self):
        session = self.session(70234004, players=2)
        engine = session.engine
        card = self.add_card(session, name="Alter Reality", ref="FLASH4")
        engine.state.players["B"].mana_pool["U"] = 2
        self.prepare_main(session)
        self.assertFalse(
            any(
                row.get("card") == card.ref
                for row in engine._priority_action_hints("B")["actions"]
            )
        )

    def test_stale_flashback_contract_rolls_back(self):
        session = self.session(70234005, players=2)
        engine = session.engine
        card = self.add_card(session, name="Think Twice", ref="FLASH5")
        engine.state.players["B"].mana_pool["U"] = 3
        self.prepare_main(session)
        before_pool = dict(engine.state.players["B"].mana_pool)
        with mock.patch(
            "quorune.rules.casting.flashback_grants.compiled_fixed_mana_flashback_spec",
            return_value=None,
        ):
            with self.assertRaisesRegex(GameRuleError, "contract changed"):
                engine._cast(
                    "B",
                    {
                        "card": card.ref,
                        "from": "graveyard",
                        "cost_option": FLASHBACK_CAST_OPTION_ID,
                        "pay": "auto",
                    },
                )
        self.assertEqual("graveyard", card.zone)
        self.assertEqual(before_pool, engine.state.players["B"].mana_pool)
        self.assertFalse(engine.state.stack)


if __name__ == "__main__":
    unittest.main()

class GrantedFlashbackCompilerTests(unittest.TestCase):
    def test_granted_flashback_keeps_the_original_entry_target_and_rejects_mutants(self):
        from test_granted_skulk import record
        from quorune.compiler.target_graveyard_flashback import target_graveyard_flashback_template
        from quorune.rules.target_graveyard_flashback import target_graveyard_flashback_runtime_dependencies
        from quorune.semantics import SemanticProgram
        from quorune.temporary_graveyard_flashback import CAPABILITY

        core = ("Target instant or sorcery card in your graveyard gains flashback until end of turn. "
                "The flashback cost is equal to its mana cost.")
        registry = load_default_capability_registry()

        def compile_text(text):
            return compile_oracle_card(record("When this creature enters, " + text, "Creature — Wizard"),
                                       capability_registry=registry, capability_profile="commander_review")

        ir = compile_text(core)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for invalid in (
            core.replace("your graveyard", "a graveyard"), core.replace("until end of turn", "until your next turn"),
            core.replace("equal to its mana cost", "equal to its mana cost plus {1}"),
        ):
            with self.subTest(invalid=invalid):
                self.assertNotEqual("exact", compile_text(invalid).status)

        _, effects, schema, coverage = target_graveyard_flashback_template(core)
        program = SemanticProgram(key="grant", label="Grant", event="permanent.enter.self",
                                  active_zone="battlefield", effects=list(effects), target_schema=schema, coverage=list(coverage))
        self.assertTrue(target_graveyard_flashback_runtime_dependencies(program))
        for invalid in (
            replace(program, active_zone="graveyard"), replace(program, target_schema={**schema, "count": 0}),
            replace(program, effects=[{**effects[0], "schema_version": True}]),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                target_graveyard_flashback_runtime_dependencies(invalid)
        from quorune.rules.target_graveyard_flashback import _SOURCE_COMBAT_DAMAGE_TO_PLAYER

        combat = replace(program, event="damage.dealt.self", event_condition=dict(_SOURCE_COMBAT_DAMAGE_TO_PLAYER))
        self.assertTrue(target_graveyard_flashback_runtime_dependencies(combat))
        bad_terms = [dict(term) for term in combat.event_condition["all"]]
        bad_terms[1]["value"] = False
        with self.assertRaisesRegex(ValueError, "sealed combat damage"):
            target_graveyard_flashback_runtime_dependencies(replace(combat, event_condition={"all": bad_terms}))



class GrantedFlashbackRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "granted.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scryfall-exact-lists.json", FIXTURE_PATH,
                                ROOT / "tests/fixtures/announced-mana-value-target-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Mishra, Eminent One"
        cls.deck = DeckDefinition("Granted Flashback", [DeckEntry(commander, 1, "commander"), DeckEntry("Island", 30)],
                                  [commander])

    def _grant(self, game, source, card, invalid=()):
        self.assert_closed(game, source)
        self.cast(game, "A", source)
        choice = self.resolve(game)
        self.assertEqual("semantic.target", choice.kind)
        for ref in invalid:
            before = authoritative_state_hash(game.state)
            denied = game.act("pilot:A", {"action_id": "choose", "targets": [ref]})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "targets": [card.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def _grant_cast_offer(self, game, card):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(action for action in actions if action["id"] == "cast:" + card.ref)

    def test_actual_snapcaster_offers_distinct_flashback_prices_and_replays_exile(self):
        game = self.session(70234011)
        snapcaster = self.add(game.engine, "Snapcaster Mage", zone="hand")
        chosen = self.add(game.engine, "Think Twice", zone="graveyard", ref="snap-think-own")
        foreign = self.add(game.engine, "Think Twice", zone="graveyard", seat="B", ref="snap-think-foreign")
        nonspell = self.add(game.engine, "Mishra, Eminent One", zone="graveyard", ref="snap-nonspell")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"U": 3, "C": 4})
        self.checkpoint(game)
        self._grant(game, snapcaster, chosen, (foreign.ref, nonspell.ref))
        game = self.replay(game, load=True)
        action = self._grant_cast_offer(game, chosen)
        options = {row["id"]: row for row in action["cost_options"]}
        granted, = (key for key in options if key.startswith("granted-flashback:"))
        self.assertEqual(1, options[granted]["requirements"]["GENERIC"])
        self.assertEqual(2, options[FLASHBACK_CAST_OPTION_ID]["requirements"]["GENERIC"])
        result = game.act("pilot:A", {"action_id": action["id"], "cost_option": granted, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[chosen.object_id].annotations[FLASHBACK_CAST_ANNOTATION])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[chosen.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        self.replay(game, load=True)

    def test_actual_snapcaster_uses_chosen_printed_x_and_expires_with_incarnation(self):
        from quorune.temporary_graveyard_flashback import current_flashback_grants

        game = self.session(70234012)
        snapcaster = self.add(game.engine, "Snapcaster Mage", zone="hand")
        chosen = self.add(game.engine, "Vault Passage", zone="graveyard")
        target = self.add(game.engine, "Copper Vault", seat="B")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"U": 1, "W": 1, "C": 5})
        self.checkpoint(game)
        self._grant(game, snapcaster, chosen)
        game = self.replay(game, load=True)
        old_card = game.state.cards[chosen.object_id]
        self.assertTrue(current_flashback_grants(game.engine, "A", old_card))
        self.assertFalse(current_flashback_grants(game.engine, "B", old_card))
        self.assertFalse(current_flashback_grants(game.engine, "A", replace(
            old_card, zone_change_counter=old_card.zone_change_counter + 1,
        )))
        action = self._grant_cast_offer(game, old_card)
        granted, = (row["id"] for row in action["cost_options"] if row["id"].startswith("granted-flashback:"))
        request = {"action_id": action["id"], "targets": [target.ref], "cost_option": granted, "x": 2, "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**request, "x": 1}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", request)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, game.state.stack[-1].x_value)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual("exile", game.state.cards[chosen.object_id].zone)
        self.replay(game, load=True)

    def test_actual_lost_in_memories_grants_flashback_on_attached_creatures_combat_damage(self):
        from quorune.temporary_graveyard_flashback import current_flashback_grants

        game = self.session(70234013)
        aura = self.add(game.engine, "Lost in Memories", zone="hand")
        attacker = self.add(game.engine, "River Sentry", ref="memory-attacker")
        attacker.acquired_control_turn_count = -1
        for index in range(3):
            self.add(game.engine, "Island", ref=f"memory-island-{index}")
        chosen = self.add(game.engine, "Think Twice", zone="graveyard", ref="memory-think-own")
        self.assert_closed(game, aura)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"U": 1, "R": 1, "C": 3})
        before = game.state.players["B"].life
        self.checkpoint(game)
        self.cast(game, "A", aura, [attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(current_flashback_grants(game.engine, "A", game.state.cards[chosen.object_id]))
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda candidate: candidate.state.pending_decision is not None
                   and candidate.state.pending_decision.kind == "semantic.target")
        self.assertEqual(before - 3, game.state.players["B"].life)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(current_flashback_grants(game.engine, "A", game.state.cards[chosen.object_id]))
        action = self._grant_cast_offer(game, chosen)
        granted, = (row["id"] for row in action["cost_options"] if row["id"].startswith("granted-flashback:"))
        result = game.act("pilot:A", {"action_id": action["id"], "cost_option": granted, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[chosen.object_id].zone)
        self.replay(game, load=True)

from __future__ import annotations

from dataclasses import replace
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, load_assets, make_session
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.entry_state_templates import static_entry_state_handler
from quorune.engine import GameRuleError
from quorune.entry_counters import capture_prospective_entry_characteristics
from quorune.model import CardInstance
from quorune.oracle_ir import generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import prepare_zone_change_replacement
from quorune.semantic_runtime import typed_entry_life_payment_amount
from quorune.targets import TargetGroup


def _record(text: str, *, name: str = "Typed Entry Fixture") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000614012",
        name=name,
        mana_cost="",
        mana_value=0,
        type_line="Land",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=("G",),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class TypedLandEntryCompilerTests(unittest.TestCase):
    def test_reveal_entry_has_typed_private_hand_query_and_optional_replacement(self):
        from quorune.oracle_ir import compile_oracle_card
        text="As this land enters, you may reveal a Mountain or Forest card from your hand. If you don't, this land enters tapped."
        ir=compile_oracle_card(_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual('replacement_effect',node.kind)
        self.assertEqual(['forest','mountain'],node.handlers[0]['spec']['query']['subtypes_any'])
        self.assertIn('zone.entry.reveal_hand_or_tapped',node.capability_dependencies)

    def test_reveal_entry_contract_rejects_open_queries_and_untrusted_owner(self):
        from quorune.compiler.entry_reveal_templates import entry_reveal_spec
        from quorune.entry_reveal import EntryRevealSpec
        from quorune.semantic_runtime.entry_reveal import EntryRevealHandler
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from common import ROOT
        text="As this land enters, you may reveal a Mountain or Forest card from your hand. If you don't, this land enters tapped."
        spec=entry_reveal_spec(text,source_name='Typed Entry Fixture');self.assertIsNotNone(spec)
        descriptor={'handler_id':'replacement.zone.entry-reveal-hand.v1','schema_version':1,'event':'zone.change','spec':spec.to_dict()}
        self.assertEqual((),EntryRevealHandler().lower(descriptor,object()))
        for body in (text+' Draw a card.',text.replace('Mountain or Forest','blue creature'),text.replace('your hand','a graveyard')):
            self.assertIsNone(entry_reveal_spec(body,source_name='Typed Entry Fixture'))
        malformed=spec.to_dict();malformed['query']['numeric_characteristic']={'characteristic':'power','comparison':'at_least','value':1}
        with self.assertRaises(ValueError):EntryRevealSpec.from_dict(malformed)
        for value in ({**descriptor,'unknown':True},{**descriptor,'schema_version':True}):
            with self.assertRaises(ValueError):EntryRevealHandler().lower(value,object())
        registry_value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in registry_value['capabilities'] if row['id']=='zone.entry.reveal_hand_or_tapped').update(status='blocked',blockers=['missing private reveal entry'])
        registry=CapabilityRegistry(registry_value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(_record(text),capability_registry=registry).status)

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_closed_entry_state_family_lowers_to_capability_closed_programs(self):
        cases = (
            ("This land enters tapped.", "zone-entry-state-self-tapped-v1"),
            (
                "This land enters tapped unless you have two or more opponents.",
                "zone-entry-state-self-minimum-2-opponents-v1",
            ),
            (
                "This land enters tapped unless you control a Forest or an Island.",
                "zone-entry-state-self-controlled-basic-types-v1",
            ),
            (
                "As this land enters, you may pay 2 life. If you don't, it enters tapped.",
                "zone-entry-state-self-pay-2-life-v1",
            ),
            (
                "Lands you control enter untapped.",
                "zone-entry-state-controlled-lands-untapped-v1",
            ),
        )
        for index, (text, template_id) in enumerate(cases):
            with self.subTest(text=text):
                lowered = static_entry_state_handler(
                    text, source_name="Typed Entry Fixture"
                )
                self.assertIsNotNone(lowered)
                self.assertEqual(template_id, lowered[0])
                self.assertEqual("zone.entry.tapped_state", lowered[2])
                program = compile_card_program(
                    self.db,
                    replace(
                        _record(text),
                        oracle_id=(
                            f"00000000-0000-4000-8000-{614012 + index:012d}"
                        ),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual(
                    ("zone.entry.tapped_state",),
                    program.capability_dependencies,
                )
                self.assertTrue(program.trust_closure["trusted"])
                self.assertEqual([], program.to_dict()["residuals"])

    def test_entry_state_near_misses_remain_residual(self):
        variants = (
            "This land enters tapped unless you control a Gate.",
            "This land enters tapped unless an opponent has two creatures.",
            "As this land enters, you may pay X life. If you don't, it enters tapped.",
            "Lands your opponents control enter untapped.",
            "This land may enter tapped.",
        )
        for text in variants:
            with self.subTest(text=text):
                self.assertIsNone(
                    static_entry_state_handler(
                        text, source_name="Typed Entry Fixture"
                    )
                )

    def test_entry_state_probe_without_source_identity_fails_closed(self):
        self.assertIsNone(
            static_entry_state_handler(
                "If you would draw a card, draw two cards instead.",
                source_name="",
            )
        )
        self.assertIsNotNone(
            static_entry_state_handler(
                "This land enters tapped.",
                source_name="",
            )
        )

    def test_entry_state_compiler_mutation_is_killed(self):
        record = _record("This land enters tapped.")
        ordinary = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertTrue(ordinary.trust_closure["trusted"])
        with patch(
            "quorune.compiler.runtime_templates.static_entry_state_handler",
            return_value=None,
        ):
            mutant = compile_card_program(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                trust_level="provisional",
            )
        self.assertFalse(mutant.trust_closure["trusted"])
        self.assertTrue(mutant.to_dict()["residuals"])


class TypedLandEntryRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

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
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str = "hand",
        register_all: bool = False,
    ) -> CardInstance:
        record = self.db.lookup(name)
        self.assertIsNotNone(record, name)
        card = CardInstance(
            object_id=f"typed-entry:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        if register_all or any(
            static_entry_state_handler(line, source_name=record.name)
            is not None
            for line in record.oracle_text.splitlines()
        ):
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                if register_all or any(
                    handler.get("handler_id")
                    == "replacement.zone.entry-state.v1"
                    for handler in program.handlers
                ):
                    engine.semantics.put(program)
        return card

    @staticmethod
    def stage_main(engine, seat: str = "A") -> None:
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine.state.players[seat].land_plays_remaining = 2

    @staticmethod
    def issue_priority(engine, seat: str = "A") -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine._grant_priority(seat)
        engine._issue_priority(seat)

    def legal_actions(self, session, seat: str = "A") -> list[dict]:
        decision = session.packet(f"pilot:{seat}", full=True)["decision"]
        self.assertIsNotNone(decision)
        return list(decision["legal_actions"])

    def existing_creature(self, engine, seat: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and not card.is_commander
            and "creature"
            in str(
                engine._effective_card_data(card).get("type_line") or ""
            ).casefold()
        )

    def test_land_entry_families_use_typed_snapshot_and_canonical_owners(self):
        session = self.session(6140121)
        engine = session.engine
        shock = self.add_card(engine, seat="A", name="Steam Vents", ref="L1")
        bond = self.add_card(engine, seat="A", name="Sea of Clouds", ref="L2")
        check = self.add_card(engine, seat="A", name="Hinterland Harbor", ref="L3")
        forest = self.add_card(engine, seat="A", name="Forest", ref="L4")

        life_before = engine.state.players["A"].life
        engine.move_card(
            shock.object_id,
            "battlefield",
            controller="A",
            entry_pay_life=True,
            log=False,
        )
        self.assertFalse(shock.tapped)
        self.assertEqual(life_before - 2, engine.state.players["A"].life)
        engine.move_card(bond.object_id, "battlefield", controller="A", log=False)
        self.assertFalse(bond.tapped)
        engine.move_card(forest.object_id, "battlefield", controller="A", log=False)
        engine.move_card(check.object_id, "battlefield", controller="A", log=False)
        self.assertFalse(check.tapped)

    def test_bond_land_uses_current_four_player_opponent_count(self):
        four = self.session(6140122, players=4)
        bond = self.add_card(
            four.engine, seat="A", name="Sea of Clouds", ref="L5"
        )
        four.engine.move_card(
            bond.object_id, "battlefield", controller="A", log=False
        )
        self.assertFalse(bond.tapped)

        duel = self.session(6140123, players=2)
        duel_bond = self.add_card(
            duel.engine, seat="A", name="Sea of Clouds", ref="L6"
        )
        duel.engine.move_card(
            duel_bond.object_id, "battlefield", controller="A", log=False
        )
        self.assertTrue(duel_bond.tapped)

    def test_shock_land_payment_is_prepared_before_zone_mutation(self):
        session = self.session(6140124)
        engine = session.engine
        shock = self.add_card(engine, seat="A", name="Steam Vents", ref="L7")
        engine.state.players["A"].life = 1
        before = copy.deepcopy(engine.state)
        with self.assertRaisesRegex(GameRuleError, "Cannot pay more life"):
            engine.move_card(
                shock.object_id,
                "battlefield",
                controller="A",
                entry_pay_life=True,
                log=False,
            )
        self.assertEqual("hand", shock.zone)
        self.assertEqual(before.players["A"].life, engine.state.players["A"].life)
        self.assertIn(shock.object_id, engine.state.players["A"].zones["hand"])

    def test_ambient_untapped_competes_with_intrinsic_tapped_entry(self):
        session = self.session(6140125)
        engine = session.engine
        ambient = self.add_card(
            engine, seat="A", name="Spelunking", ref="E1", zone="battlefield"
        )
        ambient.controller = "A"
        land = self.add_card(
            engine, seat="A", name="Temple of Mystery", ref="L8"
        )
        with self.assertRaises(ReplacementChoiceRequired) as caught:
            engine.move_card(
                land.object_id, "battlefield", controller="A", log=False
            )
        self.assertEqual("hand", land.zone)
        legal = caught.exception.pending.choice.legal_selections
        intrinsic = next(value for value in legal if land.ref in value)
        engine.move_card(
            land.object_id,
            "battlefield",
            controller="A",
            replacement_selections=(intrinsic,),
            log=False,
        )
        self.assertFalse(land.tapped)

    def test_entry_preparation_rejects_stale_face_and_rolls_back(self):
        session = self.session(6140126)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Barkchannel Pathway // Tidechannel Pathway",
            ref="M1",
        )
        front, _ = capture_prospective_entry_characteristics(
            engine, card=card, enter_face="Barkchannel Pathway"
        )
        prepared = prepare_zone_change_replacement(
            engine,
            card,
            "battlefield",
            destination_controller="A",
            entry_characteristics=front,
            error_type=GameRuleError,
        )
        back, _ = capture_prospective_entry_characteristics(
            engine, card=card, enter_face="Tidechannel Pathway"
        )
        with self.assertRaisesRegex(GameRuleError, "does not match"):
            prepare_zone_change_replacement(
                engine,
                card,
                "battlefield",
                destination_controller="A",
                entry_characteristics=back,
                prepared=prepared,
                error_type=GameRuleError,
            )
        self.assertEqual("hand", card.zone)

    def test_selected_mdfc_face_isolates_entry_program(self):
        session = self.session(61401261)
        engine = session.engine
        record = self.db.lookup(
            "Agadeem's Awakening // Agadeem, the Undercrypt"
        )
        self.assertIsNotNone(record)
        card = CardInstance(
            object_id="typed-entry:mdfc-shock",
            ref="M2",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="hand",
            known_to=["A"],
            revealed_to=[],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["hand"].append(card.object_id)
        for program in generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        ):
            engine.semantics.put(program)
        self.assertEqual(
            0,
            typed_entry_life_payment_amount(
                engine,
                record,
                card=card,
                prospective_name="Agadeem's Awakening",
            ),
        )
        self.assertEqual(
            3,
            typed_entry_life_payment_amount(
                engine,
                record,
                card=card,
                prospective_name="Agadeem, the Undercrypt",
            ),
        )

    def test_glasswing_faces_compose_aura_attachment_with_tapped_entry(self):
        session = self.session(61401263)
        engine = session.engine
        land = self.add_card(
            engine,
            seat="A",
            name="Glasswing Grace // Age-Graced Chapel",
            ref="G1",
            register_all=True,
        )
        aura = self.add_card(
            engine,
            seat="A",
            name="Glasswing Grace // Age-Graced Chapel",
            ref="G2",
            register_all=True,
        )
        target_ref = engine.create_token(
            "A",
            name="Assurance Creature",
            characteristics={
                "type_line": "Token Creature — Test",
                "colors": ["W"],
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        target = engine._resolve_object(
            "A", target_ref, zones={"battlefield"}
        )
        engine.state.players["A"].mana_pool["W"] = 5
        self.stage_main(engine)
        self.issue_priority(engine)

        land_action = next(
            action
            for action in self.legal_actions(session)
            if action["id"] == f"play-land:{land.ref}"
        )
        played = session.act("pilot:A", {"action_id": land_action["id"]})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", land.zone)
        self.assertEqual("Age-Graced Chapel", land.active_face)
        self.assertTrue(land.tapped)

        cast_action = next(
            action
            for action in self.legal_actions(session)
            if action["id"] == f"cast:{aura.ref}"
        )
        self.assertIn(target.ref, cast_action["target_schema"]["legal_refs"])
        cast = session.act(
            "pilot:A",
            {
                "action_id": cast_action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"W": 5},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual("battlefield", aura.zone)
        self.assertEqual("Glasswing Grace", aura.active_face)
        self.assertEqual(target.object_id, aura.attached_to)
        characteristics = engine._effective_card_data(target)
        self.assertEqual("4", characteristics["power"])
        self.assertEqual("4", characteristics["toughness"])
        self.assertGreaterEqual(
            {keyword.casefold() for keyword in characteristics["keywords"]},
            {"flying", "lifelink"},
        )

    def test_lotus_field_composes_tapped_entry_with_controller_relative_hexproof(
        self,
    ):
        session = self.session(61401264)
        engine = session.engine
        field = self.add_card(
            engine,
            seat="B",
            name="Lotus Field",
            ref="LF1",
            register_all=True,
        )
        engine.move_card(
            field.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        self.assertTrue(field.tapped)
        field_keywords = {
            keyword.casefold()
            for keyword in engine._effective_card_data(field)["keywords"]
        }
        self.assertIn(
            "hexproof",
            field_keywords,
        )

        group = TargetGroup.from_mapping(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
            }
        )
        source_a = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
        )
        row_a = next(
            row
            for row in engine._target_candidate_rows("A", group)
            if row["ref"] == field.ref
        )
        self.assertFalse(
            engine._target_row_matches(
                "A", group, row_a, source_ref=source_a.ref, as_target=True
            )
        )
        self.assertTrue(
            engine._target_row_matches(
                "A", group, row_a, source_ref=source_a.ref, as_target=False
            )
        )
        source_b = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card is not field
        )
        row_b = next(
            row
            for row in engine._target_candidate_rows("B", group)
            if row["ref"] == field.ref
        )
        self.assertTrue(
            engine._target_row_matches(
                "B", group, row_b, source_ref=source_b.ref, as_target=True
            )
        )

    def test_ruins_of_oran_rief_composes_tapped_entry_with_characteristic_target(
        self,
    ):
        session = self.session(61401266)
        engine = session.engine
        ruins = self.add_card(
            engine,
            seat="A",
            name="Ruins of Oran-Rief",
            ref="ROR1",
            register_all=True,
        )
        target_ref = engine.create_token(
            "A",
            name="Colorless Entrant",
            characteristics={
                "type_line": "Token Creature — Construct",
                "colors": [],
                "power": "1",
                "toughness": "1",
                "keywords": [],
            },
        )[0]
        target = engine._resolve_object(
            "A", target_ref, zones={"battlefield"}
        )
        self.assertEqual(
            engine.state.turn_sequence,
            target.entered_battlefield_turn_sequence,
        )
        engine.move_card(
            ruins.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertTrue(ruins.tapped)

        self.stage_main(engine)
        self.issue_priority(engine)
        action_id = f"activate:{ruins.ref}:ab3"
        self.assertNotIn(
            action_id,
            {action["id"] for action in self.legal_actions(session)},
        )

        ruins.tapped = False
        self.issue_priority(engine)
        action = next(
            action
            for action in self.legal_actions(session)
            if action["id"] == action_id
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        activated = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": [target.ref]},
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertTrue(ruins.tapped)
        for seat in engine.seats:
            passed = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(1, target.counters.get("+1/+1", 0))

    def test_fell_faces_compose_target_revalidation_with_tapped_entry(self):
        session = self.session(61401265)
        engine = session.engine
        land = self.add_card(
            engine,
            seat="A",
            name="Fell the Profane // Fell Mire",
            ref="F1",
            register_all=True,
        )
        spell = self.add_card(
            engine,
            seat="A",
            name="Fell the Profane // Fell Mire",
            ref="F2",
            register_all=True,
        )
        target = self.existing_creature(engine, "B")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.players["A"].mana_pool["B"] = 4
        self.stage_main(engine)
        self.issue_priority(engine)

        played = session.act(
            "pilot:A", {"action_id": f"play-land:{land.ref}"}
        )
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("Fell Mire", land.active_face)
        self.assertTrue(land.tapped)

        cast_action = next(
            action
            for action in self.legal_actions(session)
            if action["id"] == f"cast:{spell.ref}"
        )
        self.assertIn("target_schema", cast_action, cast_action)
        self.assertIn(target.ref, cast_action["target_schema"]["legal_refs"])
        cast = session.act(
            "pilot:A",
            {
                "action_id": cast_action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 4},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("stack", spell.zone)
        engine.move_card(target.object_id, "hand", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertFalse(engine.state.stack)
        self.assertEqual("graveyard", spell.zone)
        self.assertEqual("hand", target.zone)

    def test_agadeem_residual_spell_stays_fail_closed_while_land_entry_executes(
        self,
    ):
        session = self.session(61401266)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Agadeem's Awakening // Agadeem, the Undercrypt",
            ref="A1",
            register_all=True,
        )
        engine.state.config.semantic_policy = "trusted_only"
        engine.state.players["A"].mana_pool["B"] = 3
        self.stage_main(engine)
        self.issue_priority(engine)
        actions = self.legal_actions(session)
        self.assertIn(
            f"play-land:{card.ref}", {action["id"] for action in actions}
        )
        self.assertNotIn(
            f"cast:{card.ref}", {action["id"] for action in actions}
        )

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A", {"action_id": f"cast:{card.ref}"}
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        played = session.act(
            "pilot:A", {"action_id": f"play-land:{card.ref}"}
        )
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("Agadeem, the Undercrypt", card.active_face)
        self.assertTrue(card.tapped)

    def test_only_historical_v3_registry_can_use_entry_adapter(self):
        session = self.session(61401262)
        engine = session.engine
        current = self.add_card(
            engine, seat="A", name="Temple of Mystery", ref="H1"
        )
        historical = self.add_card(
            engine, seat="A", name="Temple of Mystery", ref="H2"
        )
        for program in tuple(
            engine.semantics.programs_for_oracle(
                current.oracle_id,
                active_zone="all",
                event="zone.change",
            )
        ):
            if any(
                descriptor.get("handler_id")
                == "replacement.zone.entry-state.v1"
                for descriptor in program.handlers
            ):
                engine.semantics.remove(program.key)
        engine.semantics._runtime_handler_compatibility.pop(
            (current.oracle_id, "all", "zone.change"), None
        )

        engine.move_card(
            current.object_id, "battlefield", controller="A", log=False
        )
        self.assertFalse(current.tapped)

        # Missing compatibility metadata is the historical Game Record v3
        # signal established by SemanticRegistry.load().
        engine.semantics._runtime_handler_compatibility_enabled = True
        engine.move_card(
            historical.object_id, "battlefield", controller="A", log=False
        )
        self.assertTrue(historical.tapped)

    def test_competing_land_entry_choice_is_private_and_replayable(self):
        session = self.session(6140127)
        engine = session.engine
        ambient = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Spelunking"
        )
        land = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Steam Vents"
        )
        engine.move_card(
            ambient.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        if land.zone != "hand":
            engine.move_card(land.object_id, "hand", log=False)
        self.stage_main(engine)
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action_id": f"play-land:{land.ref}"})
        self.assertTrue(result.ok, result.summary)
        land = engine.state.cards[land.object_id]
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNotNone(projected_a)
        self.assertIsNone(projector._decision("pilot:B"))
        self.assertNotIn("replacement_batch", json.dumps(projected_a))
        self.assertNotIn("priority_response", json.dumps(projected_a))
        self.assertEqual("hand", land.zone)
        selected = projected_a["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "replacement": selected},
        )
        self.assertTrue(result.ok, result.summary)
        land = engine.state.cards[land.object_id]
        self.assertEqual("battlefield", land.zone)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-land-entry-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])


if __name__ == "__main__":
    unittest.main()


class RevealEntryRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.deck import DeckDefinition,DeckEntry
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'reveal-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/reveal-entry-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Reveal entry',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Witnesses
    session=_Witnesses.session
    add=_Witnesses.add
    checkpoint=_Witnesses.checkpoint
    replay=_Witnesses.replay

    def ready(self,s,source):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.started=True
        e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main';e.state.priority_passes=[]
        e._grant_priority('A');e.pump()
        return next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==source.ref)

    def test_original_game_trail_private_choice_selected_reveal_untapped_entry_and_pending_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s=self.session(360001);e=s.engine;source=self.add(e,'Game Trail',zone='hand')
        forest=self.add(e,'Forest',zone='hand');island=self.add(e,'Island',zone='hand')
        foreign=self.add(e,'Mountain',seat='B',zone='hand')
        p=compile_best_available_card_program(self.db,self.db.lookup('Game Trail'),semantic_registry=e.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        action=self.ready(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind);self.assertEqual('hand',e.state.cards[source.object_id].zone)
        choices=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(forest.ref in row['id'] for row in choices));self.assertFalse(any(island.ref in row['id'] or foreign.ref in row['id'] for row in choices))
        projection=json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True)
        for card in (forest,island,foreign):self.assertNotIn(card.ref,projection)
        self.assertNotIn('Reveal Forest',json.dumps(s.packet('pilot:D',full=True),sort_keys=True))
        resumed=self.replay(s,load=True);choices=resumed.packet('pilot:A',full=True)['decision']['ctx']['options']
        chosen=next(row['id'] for row in choices if forest.ref in row['id']);before=authoritative_state_hash(resumed.state)
        for principal,value in [('pilot:C',chosen),('pilot:A','unknown-reveal')]:
            denied=resumed.act(principal,{'action_id':'choose','replacement':value});self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':chosen});self.assertTrue(accepted.ok,accepted.summary)
        land=resumed.state.cards[source.object_id];self.assertEqual('battlefield',land.zone);self.assertFalse(land.tapped)
        revealed=resumed.state.cards[forest.object_id];self.assertEqual('hand',revealed.zone)
        self.assertEqual(set('ABCD'),set(revealed.known_to));self.assertEqual(['A'],revealed.revealed_to)
        projection=json.dumps(resumed.projector._snapshot('pilot:D'),sort_keys=True);self.assertIn(forest.ref,projection)
        self.assertNotIn(island.ref,projection);self.assertNotIn(foreign.ref,projection);self.replay(resumed,load=True)

    def test_original_game_trail_decline_and_no_match_enter_tapped_without_reveal(self):
        for matching in (False,True):
            s=self.session(360002+int(matching));e=s.engine;source=self.add(e,'Game Trail',zone='hand')
            card=self.add(e,'Forest' if matching else 'Island',zone='hand')
            action=self.ready(s,source);self.checkpoint(s);accepted=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(accepted.ok,accepted.summary)
            if matching:
                options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                choice=next(row['id'] for row in options if row['label']=='Enter tapped without revealing a card')
                accepted=s.act('pilot:A',{'action_id':'choose','replacement':choice});self.assertTrue(accepted.ok,accepted.summary)
            current=e.state.cards[source.object_id];self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
            self.assertEqual(['A'],e.state.cards[card.object_id].known_to)
            self.assertNotIn(card.ref,json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True));self.replay(s,load=True)

    def test_original_secluded_glen_matches_kindred_subtype_without_creature_requirement(self):
        s=self.session(360004);e=s.engine;source=self.add(e,'Secluded Glen',zone='hand')
        card=self.add(e,'Bitterblossom',zone='hand');action=self.ready(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(accepted.ok,accepted.summary)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];choice=next(row['id'] for row in options if card.ref in row['id'])
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':choice});self.assertTrue(accepted.ok,accepted.summary)
        self.assertFalse(e.state.cards[source.object_id].tapped);self.replay(s,load=True)

    def test_original_copy_land_applies_copied_reveal_choice_to_copier_controller_and_replays(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        s=self.session(360005);e=s.engine;source=self.add(e,'Copy Land',zone='hand')
        target=self.add(e,'Game Trail',seat='B');reveal=self.add(e,'Forest',zone='hand')
        e.state.players['A'].mana_pool.update({'U':1,'C':2});self.ready(s,source);self.checkpoint(s)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==source.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        BoundEffectProgramRuntimeTests.resolve(self,s);self.assertEqual('replacement.order',s.state.pending_decision.kind)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];copy=next(row['id'] for row in options if target.ref in row['id'])
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':copy});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind);self.replay(s,load=True)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];choice=next(row['id'] for row in options if reveal.ref in row['id'])
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':choice});self.assertTrue(accepted.ok,accepted.summary)
        BoundEffectProgramRuntimeTests.resolve(self,s)
        current=e.state.cards[source.object_id];self.assertEqual('battlefield',current.zone);self.assertFalse(current.tapped)
        self.assertEqual('A',current.controller);self.assertEqual(set('ABCD'),set(e.state.cards[reveal.object_id].known_to))
        self.assertIn('enchantment',e._effective_card_data(current)['type_line'].casefold());self.replay(s,load=True)

    def test_reveal_does_not_override_independent_tapped_entry_and_reuses_same_hand_card(self):
        from quorune.semantic_runtime import prepare_zone_change_replacement_batch
        from quorune.replacement_effects import ReplacementChoiceRequired
        from quorune.entry_reveal import ENTRY_REVEAL_FIELD
        s=self.session(360006);e=s.engine;one=self.add(e,'Game Trail',zone='hand',ref='trail-one')
        two=self.add(e,'Game Trail',zone='hand',ref='trail-two');reveal=self.add(e,'Forest',zone='hand')
        changes=((one.object_id,'battlefield'),(two.object_id,'battlefield'));selections=[]
        for _ in range(6):
            try:
                prepared=prepare_zone_change_replacement_batch(e,changes,destination_controllers={one.object_id:'A',two.object_id:'A'},
                    requested_tapped={one.object_id:True,two.object_id:False},selections=selections)
                break
            except ReplacementChoiceRequired as choice:
                selections.append(next(value for value in choice.pending.choice.legal_selections if reveal.ref in value))
        else:self.fail('Simultaneous reveal choices did not complete')
        self.assertTrue(prepared[one.object_id].entry_tapped);self.assertFalse(prepared[two.object_id].entry_tapped)
        for result in prepared.values():self.assertEqual(reveal.ref,result.event.payload[ENTRY_REVEAL_FIELD]['ref'])
        self.assertEqual(['A'],reveal.known_to)
        e._move_cards_simultaneously(changes,
            replacement_selections=tuple(selections),reason='simultaneous reveal entry witness')
        self.assertEqual('battlefield',e.state.cards[one.object_id].zone)
        self.assertEqual('battlefield',e.state.cards[two.object_id].zone)
        self.assertEqual('hand',e.state.cards[reveal.object_id].zone)
        self.assertEqual(set('ABCD'),set(e.state.cards[reveal.object_id].known_to))

    def test_entering_land_cannot_reveal_itself_and_stale_private_identity_fails_atomically(self):
        from quorune.effect_runtime.hand_inspection import validate_entry_hand_reveal
        from quorune.errors import GameRuleError
        s=self.session(360007);e=s.engine;source=self.add(e,'Murmuring Bosk',zone='hand')
        action=self.ready(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('battlefield',e.state.cards[source.object_id].zone);self.assertTrue(e.state.cards[source.object_id].tapped)
        self.replay(s,load=True)
        card=self.add(e,'Forest',zone='hand');selection={'object_id':card.object_id,'logical_object_id':card.logical_object_id,
            'ref':card.ref,'player':'A','oracle_id':card.oracle_id,'name':card.printed_name}
        self.assertIs(card,validate_entry_hand_reveal(e,selection));before=authoritative_state_hash(e.state)
        for wrong in ({**selection,'logical_object_id':'stale'},{**selection,'player':'B'},{**selection,'unknown':True}):
            with self.assertRaises(GameRuleError):validate_entry_hand_reveal(e,wrong)
            self.assertEqual(before,authoritative_state_hash(e.state))

    def test_reveal_entry_producer_omission_mutant_is_killed(self):
        with patch('quorune.semantic_runtime.zone_replacements.entry_reveal_replacement_effects',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_original_game_trail_private_choice_selected_reveal_untapped_entry_and_pending_replay()

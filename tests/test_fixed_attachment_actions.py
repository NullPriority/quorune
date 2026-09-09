from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.abilities import ActivationLimit, parse_activated_abilities
from quorune.attachments import attach_objects
from quorune.carddb import CardRecord
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "fixed-attachment-actions-cards.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-attachment-actions.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "fixed-token-production-cards.json",
            FIXTURE,
        ],
        database,
    )
    return CardDatabase(database)


EXPECTED_ATTACHMENT_BEHAVIOR = (
    {
        "authority": ("301.5c", "701.3", "603.3d"),
        "oracle": (
            "When this Equipment enters, attach it to target creature you "
            "control."
        ),
        "legal_options": "one current controlled creature",
        "committed_result": "the same Equipment incarnation becomes attached",
        "counterexample": "an illegal target leaves the Equipment unattached",
    },
    {
        "authority": ("701.3", "702.6a"),
        "oracle": "Equip Human {1}",
        "legal_options": "one current controlled Human creature",
        "committed_result": "the resolving Equipment becomes attached",
        "counterexample": "a controlled non-Human creature is not offered",
    },
    {
        "authority": ("701.3", "702.92a"),
        "oracle": "Living weapon",
        "legal_options": "no target; create one fixed Phyrexian Germ",
        "committed_result": "attach the Equipment to one created Germ",
        "counterexample": "a departed or new Equipment incarnation is not attached",
    },
    {
        "authority": ("701.3", "702.163a"),
        "oracle": "For Mirrodin!",
        "legal_options": "no target; create one fixed red Rebel",
        "committed_result": "attach the Equipment to one created Rebel",
        "counterexample": "an independently added replacement token remains unattached",
    },
)


def record(
    name: str,
    oracle_text: str,
    *,
    type_line: str = "Artifact — Equipment",
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedAttachmentActionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, fixture: CardRecord):
        return compile_oracle_card(
            fixture,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_expectations_are_independent_and_distinguish_wrong_results(self):
        self.assertEqual(4, len(EXPECTED_ATTACHMENT_BEHAVIOR))
        for row in EXPECTED_ATTACHMENT_BEHAVIOR:
            with self.subTest(oracle=row["oracle"]):
                self.assertTrue(row["authority"])
                self.assertTrue(row["legal_options"])
                self.assertTrue(row["committed_result"])
                self.assertTrue(row["counterexample"])

    def test_source_equipment_entry_attach_is_typed_and_targeted(self):
        compiled = self.compile(
            record(
                "Entry Harness",
                "When this Equipment enters, attach it to target creature you control.",
            )
        )
        node = compiled.faces[0].nodes[0]

        self.assertTrue(node.exact)
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual("permanent.enter.self", node.event)
        self.assertEqual("fixed-source-equipment-attach-trigger-v1", node.template_id)
        self.assertEqual(
            (
                {
                    "op": "attach",
                    "attachment_kind": "equipment",
                    "source": "$source.zone_object",
                    "target": "$target.0",
                },
            ),
            node.effects,
        )
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": "you",
                "creature": True,
                "count": 1,
            },
            node.target_schema,
        )
        self.assertIn(
            "attachment.action.fixed_source",
            node.capability_dependencies,
        )

    def test_source_aura_attachment_separates_target_and_enchant_legality(self):
        for source, predicate in (
            (
                "Attach this Aura to target creature.",
                None,
            ),
            (
                "Attach this Aura to target creature other than enchanted creature.",
                "not_source_attachment",
            ),
        ):
            with self.subTest(source=source):
                compiled = self.compile(
                    record(
                        "Moving Aura",
                        f"Enchant creature you control\n{{1}}{{U}}: {source}",
                        type_line="Enchantment — Aura",
                        keywords=("Enchant",),
                    )
                )
                node = next(
                    candidate
                    for candidate in compiled.faces[0].nodes
                    if any(
                        effect.get("op") == "attach"
                        for effect in candidate.effects
                    )
                )
                self.assertTrue(node.exact)
                self.assertEqual(predicate, node.target_schema.get("predicate"))
                self.assertIn(
                    "attachment.action.fixed_source",
                    node.capability_dependencies,
                )

    def test_restricted_equip_variants_share_one_activation_contract(self):
        cases = {
            "Equip creature token {1}": {"token": True},
            "Equip commander {2}": {"commander": True},
            "Equip legendary creature {3}": {
                "supertypes_any": ("legendary",),
            },
            "Equip Human {1}": {"subtypes_any": ("human",)},
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                ability = parse_activated_abilities(
                    card_name="Restricted Harness",
                    oracle_text=source,
                    keywords=("Equip",),
                )[0]
                self.assertTrue(ability.compiled_cost)
                self.assertTrue(ability.sorcery_speed)
                self.assertEqual("builtin:equip", ability.builtin_semantic_key)
                self.assertEqual("you", ability.target_schema["controller"])
                self.assertIs(True, ability.target_schema["creature"])
                for field, value in expected.items():
                    self.assertEqual(value, ability.target_schema[field])

                node = self.compile(
                    record(
                        "Restricted Harness",
                        source,
                        keywords=("Equip",),
                    )
                ).faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    ("attachment.equip.fixed_restricted",),
                    node.capability_dependencies,
                )

        limited = parse_activated_abilities(
            card_name="Limited Harness",
            oracle_text="Equip {0}. Activate only once each turn.",
            keywords=("Equip",),
        )[0]
        self.assertEqual(ActivationLimit.ONCE_PER_TURN, limited.activation_limit)
        limited_node = self.compile(
            record(
                "Limited Harness",
                "Equip {0}. Activate only once each turn.",
                keywords=("Equip",),
            )
        ).faces[0].nodes[0]
        self.assertTrue(limited_node.exact)
        self.assertEqual(
            ("attachment.equip.fixed_restricted",),
            limited_node.capability_dependencies,
        )

    def test_living_weapon_and_for_mirrodin_lower_distinct_fixed_tokens(self):
        cases = (
            (
                "Living weapon",
                ("Equip", "Living weapon"),
                "Phyrexian Germ",
                "Token Creature — Phyrexian Germ",
                "0",
                "0",
                ["B"],
                "trigger.keyword.living_weapon",
            ),
            (
                "For Mirrodin!",
                ("Equip", "For Mirrodin!"),
                "Rebel",
                "Token Creature — Rebel",
                "2",
                "2",
                ["R"],
                "trigger.keyword.for_mirrodin",
            ),
        )
        for source, keywords, token, type_line, power, toughness, colors, capability in cases:
            with self.subTest(source=source):
                node = self.compile(
                    record("Token Harness", source, keywords=keywords)
                ).faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual("permanent.enter.self", node.event)
                self.assertEqual("create-attached-equipment-token-v1", node.template_id)
                self.assertEqual("create_attached_token", node.effects[0]["op"])
                self.assertEqual(token, node.effects[0]["name"])
                self.assertEqual(type_line, node.effects[0]["characteristics"]["type_line"])
                self.assertEqual(power, node.effects[0]["characteristics"]["power"])
                self.assertEqual(toughness, node.effects[0]["characteristics"]["toughness"])
                self.assertEqual(colors, node.effects[0]["characteristics"]["colors"])
                self.assertIn(capability, node.capability_dependencies)

    def test_adjacent_attachment_grammar_remains_fail_closed(self):
        base = record("Boundary Harness", "Equip {2}", keywords=("Equip",))
        for oracle_text in (
            "Equip Human {W/U}",
            "Equip Human—Discard a card",
            "When this Equipment enters, attach it to target creature an opponent controls.",
            "Living Weapon — Create two Germ tokens.",
            "For Mirrodin — Create a Rebel token.",
        ):
            with self.subTest(oracle_text=oracle_text):
                compiled = self.compile(
                    replace(base, oracle_text=oracle_text)
                )
                self.assertNotEqual("exact", compiled.status)


class FixedAttachmentActionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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

    def session(self, seed: int, *, players: int = 2):
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
        engine.state.pending_trigger_batches.clear()
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
        zone: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        card = CardInstance(
            object_id=f"attachment-action:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(
                [seat] if zone in {"hand", "library"} else list(engine.seats)
            ),
            revealed_to=(
                [] if zone in {"hand", "library"} else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def pass_until_empty(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            seat = session.engine.state.priority_player
            if seat is None:
                session.engine._grant_priority(session.engine.state.active_player)
                seat = session.engine.state.priority_player
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Attachment stack did not resolve")

    def _battlefield_creature(self, engine, seat: str, name: str) -> CardInstance:
        card = self.deck_card(engine, seat, name)
        engine.move_card(card.object_id, "battlefield", controller=seat, log=False)
        return card

    def test_equipment_entry_trigger_attaches_and_revalidates_target(self):
        session = self.session(7013001)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment entry witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)

        stale = self.session(7013002)
        stale_engine = stale.engine
        stale_source = self.add_card(
            stale_engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="stale-entry-harness",
            zone="hand",
        )
        stale_target = self._battlefield_creature(
            stale_engine, "A", "Mishra, Eminent One"
        )
        stale_engine.move_card(
            stale_source.object_id,
            "battlefield",
            controller="A",
            reason="stale attachment entry witness",
            semantic_events=True,
        )
        stale_engine._stabilize()
        chosen = stale.act(
            "pilot:A",
            {"action_id": "choose", "targets": [stale_target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        stale_engine.move_card(stale_target.object_id, "graveyard", log=False)
        self.resolve_top(stale_engine)
        self.assertIsNone(stale_source.attached_to)

    def test_legacy_equipment_attach_event_shape_is_preserved(self):
        session = self.session(7013017)
        engine = session.engine
        equipment = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="legacy-event-harness",
            zone="battlefield",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )

        engine.apply_effect(
            {
                "op": "attach",
                "equipment": equipment.ref,
                "creature": target.ref,
                "reason": "legacy attachment witness",
            },
            actor="A",
        )

        event = next(
            value
            for value in reversed(engine.state.events)
            if value.code == "attachment.attach"
        )
        self.assertEqual(
            {
                "equipment": equipment.ref,
                "creature": target.ref,
                "reason": "legacy attachment witness",
            },
            event.details,
        )
        self.assertEqual(target.object_id, equipment.attached_to)

    def test_departed_or_reentered_source_does_not_attach(self):
        session = self.session(7013003)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="incarnation-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="source incarnation witness",
            semantic_events=True,
        )
        engine._stabilize()
        old_identity = source.logical_object_id
        chosen = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(old_identity, source.logical_object_id)

        self.resolve_top(engine)

        self.assertIsNone(source.attached_to)
        self.assertNotIn(source.object_id, target.attachments)

        for seed, source_name in (
            (7013013, "Living Harness Fixture"),
            (7013014, "Mirrodin Harness Fixture"),
        ):
            with self.subTest(source=source_name):
                token_session = self.session(seed)
                token_engine = token_session.engine
                token_source = self.add_card(
                    token_engine,
                    seat="A",
                    name=source_name,
                    ref=f"stale-token-source-{seed}",
                    zone="hand",
                )
                token_engine.move_card(
                    token_source.object_id,
                    "battlefield",
                    controller="A",
                    reason="stale token attachment source entered",
                    semantic_events=True,
                )
                token_engine._stabilize()
                old_token_identity = token_source.logical_object_id
                token_engine.move_card(
                    token_source.object_id, "graveyard", log=False
                )
                token_engine.move_card(
                    token_source.object_id,
                    "battlefield",
                    controller="A",
                    log=False,
                )
                self.assertNotEqual(
                    old_token_identity,
                    token_source.logical_object_id,
                )
                self.resolve_top(token_engine)
                self.assertIsNone(token_source.attached_to)

    def test_restricted_equip_offer_commit_and_current_target_revalidation(self):
        session = self.session(7013004)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Restricted Harness Fixture",
            ref="restricted-harness",
            zone="battlefield",
        )
        human_ref = engine.create_token(
            "A",
            name="Human witness",
            characteristics={
                "type_line": "Token Creature — Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        insect_ref = engine.create_token(
            "A",
            name="Insect witness",
            characteristics={
                "type_line": "Token Creature — Insect",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        human = engine._resolve_object("A", human_ref)
        insect = engine._resolve_object("A", insect_ref)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["C"] = 3

        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "A",
                {
                    "source": source.ref,
                    "ability": "ab2",
                    "targets": [insect.ref],
                },
            )
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": "ab2",
                "targets": [human.ref],
            },
        )
        human.annotations["token_characteristics"]["type_line"] = (
            "Token Creature — Insect"
        )
        human.annotations["copy_overrides"]["type_line"] = (
            "Token Creature — Insect"
        )
        self.resolve_top(engine)
        self.assertIsNone(source.attached_to)

    def test_once_per_turn_equip_limit_is_shared_by_offer_and_commit(self):
        session = self.session(7013015)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Limited Harness Fixture",
            ref="limited-harness",
            zone="battlefield",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        ability = next(
            ability
            for ability in engine._activated_abilities(source)
            if ability.ability_id == "ab2"
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("A", source, ability),
        )
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)
        self.assertEqual(
            ("unavailable", "already_activated_this_turn"),
            engine._ability_availability("A", source, ability),
        )

    def test_aura_reattachment_rechecks_live_enchant_legality(self):
        session = self.session(7013005)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Exclusive Moving Aura Fixture",
            ref="moving-aura",
            zone="battlefield",
        )
        first_ref = engine.create_token(
            "A",
            name="First creature",
            characteristics={
                "type_line": "Token Creature — Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        second_ref = engine.create_token(
            "B",
            name="Second creature",
            characteristics={
                "type_line": "Token Creature — Insect",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        first = engine._resolve_object("A", first_ref)
        second = engine._resolve_object("A", second_ref)
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 3

        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "A",
                {
                    "source": aura.ref,
                    "ability": "ab3",
                    "targets": [first.ref],
                },
            )
        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [second.ref],
            },
        )
        second.annotations["token_characteristics"]["type_line"] = (
            "Token Artifact"
        )
        second.annotations["copy_overrides"]["type_line"] = "Token Artifact"
        self.resolve_top(engine)
        self.assertEqual(first.object_id, aura.attached_to)

    def test_aura_reattachment_uses_current_source_controller(self):
        session = self.session(7013020)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Controlled Moving Aura Fixture",
            ref="controlled-moving-aura",
            zone="battlefield",
        )
        first = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        second = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="Second controlled creature",
                characteristics={
                    "type_line": "Token Creature — Human",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        opposing = engine._resolve_object(
            "B",
            engine.create_token(
                "B",
                name="Opposing creature",
                characteristics={
                    "type_line": "Token Creature — Insect",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 4

        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [opposing.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(first.object_id, aura.attached_to)

        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [second.ref],
            },
        )
        engine.change_control(first.object_id, "B", reason="Aura control witness")
        engine.change_control(second.object_id, "B", reason="Aura control witness")
        engine.change_control(aura.object_id, "B", reason="Aura control witness")

        self.resolve_top(engine)

        self.assertEqual(second.object_id, aura.attached_to)
        self.assertEqual("B", aura.controller)
        self.assertEqual("B", second.controller)

    def test_living_weapon_creates_germ_and_attaches_before_state_actions(self):
        session = self.session(7013006)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Living Harness Fixture",
            ref="living-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="living weapon witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        germs = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Phyrexian Germ"
        ]
        self.assertEqual(1, len(germs))
        self.assertEqual(germs[0].object_id, source.attached_to)
        self.assertEqual(1, engine._numeric_stat(germs[0].object_id, "toughness"))

    def test_for_mirrodin_creates_rebel_and_attaches_source(self):
        session = self.session(7013007)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Mirrodin Harness Fixture",
            ref="mirrodin-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="for mirrodin witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        rebels = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Rebel"
        ]
        self.assertEqual(1, len(rebels))
        self.assertEqual(rebels[0].object_id, source.attached_to)
        self.assertEqual(["R"], engine._effective_card_data(rebels[0])["colors"])

    def test_token_attachment_uses_trigger_controller_after_control_change(self):
        session = self.session(7013016)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Mirrodin Harness Fixture",
            ref="controlled-mirrodin-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment control-change witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual("A", engine.state.stack[-1].controller)
        engine.change_control(
            source.object_id,
            "B",
            reason="attachment control-change witness",
        )
        self.resolve_top(engine)
        rebel = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Rebel"
        )
        self.assertEqual("A", rebel.controller)
        self.assertEqual("B", source.controller)
        self.assertEqual(rebel.object_id, source.attached_to)

    def test_token_replacement_attaches_exactly_one_created_token(self):
        session = self.session(7013008)
        engine = session.engine
        replacement = self.add_card(
            engine,
            seat="A",
            name="Token Echo Fixture",
            ref="token-echo",
            zone="battlefield",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Living Harness Fixture",
            ref="replacement-living-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attached token replacement witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        created = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.printed_name in {"Food", "Phyrexian Germ"}
        ]
        self.assertEqual({"Food", "Phyrexian Germ"}, {c.printed_name for c in created})
        germ = next(c for c in created if c.printed_name == "Phyrexian Germ")
        food = next(c for c in created if c.printed_name == "Food")
        self.assertEqual(germ.object_id, source.attached_to)
        self.assertNotEqual(food.object_id, source.attached_to)

    def test_malformed_attached_token_effect_fails_before_mutation(self):
        session = self.session(7013019)
        engine = session.engine
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "closed fixed shape"):
            engine.apply_effect(
                {
                    "op": "create_attached_token",
                    "controller": "A",
                    "name": "Phyrexian Germ",
                    "quantity": 1,
                    "characteristics": {
                        "type_line": "Token Creature — Phyrexian Germ",
                        "power": "0",
                        "toughness": "0",
                    },
                },
                actor="A",
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(
            any(
                card.is_token and card.printed_name == "Phyrexian Germ"
                for card in engine.state.cards.values()
            )
        )

    def test_four_player_attachment_target_projection_and_replay(self):
        session = self.session(7013009, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Entry Harness Fixture",
            ref="replay-entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "C", "Mishra, Eminent One"
        )
        engine.state.active_player = "C"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="C",
            reason="four-player attachment witness",
            semantic_events=True,
        )
        engine._stabilize()
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:C"))
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(
            source.object_id,
            json.dumps(projector._decision("pilot:C"), sort_keys=True),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selected = session.act(
            "pilot:C",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.pass_until_empty(session)
        self.assertEqual(target.object_id, source.attached_to)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "attachment-replay"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_four_player_restricted_equip_projection_and_replay(self):
        session = self.session(7013010, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Restricted Harness Fixture",
            ref="replay-restricted-harness",
            zone="battlefield",
        )
        target_ref = engine.create_token(
            "C",
            name="Replay Human",
            characteristics={
                "type_line": "Token Creature — Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        target = engine._resolve_object("C", target_ref)
        engine.state.active_player = "C"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "C"
        engine.state.players["C"].mana_pool["C"] = 2
        engine._issue_priority("C")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:C",
            {
                "action_id": f"activate:{source.ref}:ab2",
                "targets": [target.ref],
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        self.pass_until_empty(session)
        self.assertEqual(target.object_id, source.attached_to)
        for seat in ("A", "B", "C", "D"):
            packet = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            self.assertNotIn(source.object_id, json.dumps(packet, sort_keys=True))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "restricted-equip-replay"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_four_player_token_attachment_triggers_replay(self):
        for seed, source_name, token_name in (
            (7013011, "Living Harness Fixture", "Phyrexian Germ"),
            (7013012, "Mirrodin Harness Fixture", "Rebel"),
        ):
            with self.subTest(source=source_name):
                session = self.session(seed, players=4)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="C",
                    name=source_name,
                    ref=f"replay-token-harness-{seed}",
                    zone="hand",
                )
                engine.state.active_player = "C"
                engine.move_card(
                    source.object_id,
                    "battlefield",
                    controller="C",
                    reason="four-player token attachment witness",
                    semantic_events=True,
                )
                engine._stabilize()
                self.assertTrue(engine.state.stack)
                engine._grant_priority("C")
                engine._issue_priority("C")
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()

                self.pass_until_empty(session)
                token = next(
                    card
                    for card in engine.state.cards.values()
                    if card.is_token and card.printed_name == token_name
                )
                self.assertEqual(token.object_id, source.attached_to)
                for seat in ("A", "B", "C", "D"):
                    packet = StateProjector(self.db, engine.state)._snapshot(
                        f"pilot:{seat}"
                    )
                    self.assertNotIn(
                        token.object_id,
                        json.dumps(packet, sort_keys=True),
                    )
                expected_hash = authoritative_state_hash(engine.state)
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / f"token-attachment-{seed}"
                    session.save(directory)
                    replay = replay_record(directory, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_attachment_action_compiler_and_runtime_mutants_are_killed(self):
        fixture = record(
            "Mutation Harness",
            "When this Equipment enters, attach it to target creature you control.",
        )

        def assert_compiles() -> None:
            compiled = compile_oracle_card(
                fixture,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertTrue(compiled.faces[0].nodes[0].exact)

        assert_compiles()
        with mock.patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_source_attachment_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiles()

        def assert_runtime_attaches(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Entry Harness Fixture",
                ref=f"mutation-source-{seed}",
                zone="battlefield",
            )
            target = self._battlefield_creature(
                engine, "A", "Mishra, Eminent One"
            )
            engine.apply_effect(
                {
                    "op": "attach",
                    "attachment_kind": "equipment",
                    "source": source.ref,
                    "target": target.ref,
                },
                actor="A",
            )
            self.assertEqual(target.object_id, source.attached_to)

        assert_runtime_attaches(7013017)
        with mock.patch(
            "quorune.effect_runtime.zones_and_attachments.attach_objects",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_runtime_attaches(7013018)


if __name__ == "__main__":
    unittest.main()

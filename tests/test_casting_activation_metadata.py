from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.casting_activation_metadata_templates import (
    static_loyalty_cost_modifier_handler,
    static_self_zone_cast_permission_handler,
)
from quorune.oracle_ir import register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.casting_activation_metadata import (
    LOYALTY_COST_MODIFIER_EVENT,
    LOYALTY_COST_MODIFIER_HANDLER_ID,
    SELF_ZONE_CAST_PERMISSION_EVENT,
    SELF_ZONE_CAST_PERMISSION_HANDLER_ID,
    default_loyalty_cost_modifier_registry,
    default_self_zone_cast_permission_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Creature — Zombie",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name="Casting Activation Metadata Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
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


class CastingActivationMetadataCompilerTests(unittest.TestCase):
    def test_self_graveyard_public_permission_schema_and_unknown_scope_fail_closed(self):
        from quorune.semantic_runtime.casting_activation_metadata import PublicStateSelfZoneCastPermissionHandler
        text='You may cast this card from your graveyard if you gained life this turn.'
        descriptor=static_self_zone_cast_permission_handler(text)[1]
        handler=PublicStateSelfZoneCastPermissionHandler()
        handler.validate(descriptor)
        for field,value in (('schema_version',True),('source_zone','exile'),('unknown',True)):
            with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,field:value})
        for altered in (
            text.replace('this card','creature cards'),
            text.replace('you gained life this turn','the top card of your library is blue'),
            text+' Draw a card.',
        ):
            self.assertIsNone(static_self_zone_cast_permission_handler(altered))
        original=static_self_zone_cast_permission_handler('You may cast this card from your graveyard as long as you control a Zombie.')
        self.assertEqual(1,original[1]['schema_version'])
        self.assertEqual(['zombie'],original[1]['controlled_subtypes_any'])
        with mock.patch('quorune.compiler.public_state_queries.fixed_public_state_condition',return_value=None):
            self.assertIsNone(static_self_zone_cast_permission_handler(text))
        import copy,json
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((Path(__file__).parents[1]/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('casting.zone.self_graveyard.controlled_subtype','continuous.characteristics.fixed_public_state'):
            changed=copy.deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',blockers=['self-zone permission dependency withdrawal'])
            self.assertNotEqual('exact',compile_oracle_card(_record(text,suffix=363100008),capability_registry=CapabilityRegistry(changed)).status)

    def test_self_graveyard_permission_preserves_general_public_condition(self):
        from quorune.card_programs import bind_card_program_runtime
        for index, text in enumerate((
            'You may cast this card from your graveyard as long as you control a black or green permanent.',
            'You may cast this card from your graveyard if you gained life this turn.',
            'Max speed — You may cast this card from your graveyard.',
        )):
            with self.subTest(text=text):
                program = self.compile(_record(text,suffix=363100001+index),trust_level='provisional')
                self.assertEqual((),program.residuals)
                descriptor = next(d for ability in program.abilities for d in ability.handlers
                    if d['event'] == SELF_ZONE_CAST_PERMISSION_EVENT)
                if index == 2:
                    self.assertEqual('permission.cast.self-public-zones.v1', descriptor['handler_id'])
                    self.assertEqual(['graveyard'], descriptor['source_zones'])
                    scope = next(d for ability in program.abilities for d in ability.handlers
                        if d['handler_id'] == 'ability.static.component-scope.v1')
                    self.assertEqual({'kind': 'controller_speed_equals', 'designation': 'controller_speed', 'minimum': 4},
                        scope['fragment']['value']['applicability'])
                else:
                    self.assertEqual('permission.cast.self-zone-public-state.v1', descriptor['handler_id'])
                    self.assertIn('source_condition',descriptor)
                self.assertEqual('all' if index == 2 else 'graveyard',
                    next(a.active_zone for a in program.abilities if descriptor in a.handlers))
                default_self_zone_cast_permission_registry().validate(descriptor)
                program = self.compile(_record(text,suffix=363100001+index),trust_level='trusted')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=self.capabilities,
                    profile='commander_review')['strict_capability_ready'])
    def setUp(self):
        self.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord, *, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_closed_casting_activation_metadata_compiles_source_spanned_programs(
        self,
    ):
        cases = (
            (
                "You may cast this card from your graveyard as long as you "
                "control a Zombie.",
                "Creature — Zombie",
                SELF_ZONE_CAST_PERMISSION_HANDLER_ID,
                SELF_ZONE_CAST_PERMISSION_EVENT,
                "graveyard",
                "casting.zone.self_graveyard.controlled_subtype",
            ),
            (
                "Loyalty abilities of planeswalkers your opponents control "
                "cost {1} more to activate.",
                "Enchantment Creature — Spirit",
                LOYALTY_COST_MODIFIER_HANDLER_ID,
                LOYALTY_COST_MODIFIER_EVENT,
                "battlefield",
                "activation.loyalty_cost.modifier_detection",
            ),
            (
                "Planeswalkers' loyalty abilities you activate cost an "
                "additional [+1] to activate.",
                "Legendary Creature — Human Warrior",
                LOYALTY_COST_MODIFIER_HANDLER_ID,
                LOYALTY_COST_MODIFIER_EVENT,
                "battlefield",
                "activation.loyalty_cost.modifier_detection",
            ),
        )
        for index, (
            text,
            type_line,
            handler_id,
            event,
            active_zone,
            capability,
        ) in enumerate(cases, 1):
            with self.subTest(text=text):
                card_program = self.compile(
                    _record(
                        text,
                        suffix=601_300_000 + index,
                        type_line=type_line,
                    )
                )
                self.assertEqual((), card_program.residuals)
                ability = next(
                    ability
                    for ability in card_program.abilities
                    if any(
                        descriptor.get("handler_id") == handler_id
                        for descriptor in ability.handlers
                    )
                )
                self.assertEqual(event, ability.event)
                self.assertEqual(active_zone, ability.active_zone)
                self.assertEqual("front", ability.provenance["face_id"])
                self.assertEqual(1, ability.provenance["source_span"]["line"])
                self.assertEqual(
                    len(text),
                    ability.provenance["source_span"]["end"]
                    - ability.provenance["source_span"]["start"],
                )
                self.assertIn(capability, ability.capability_dependencies)

        graveyard_descriptor = static_self_zone_cast_permission_handler(
            cases[0][0]
        )[1]
        self.assertEqual(["zombie"], graveyard_descriptor["controlled_subtypes_any"])
        opponent_descriptor = static_loyalty_cost_modifier_handler(cases[1][0])[1]
        self.assertEqual("opponent", opponent_descriptor["affected_controller"])
        self.assertEqual("generic_mana_increase", opponent_descriptor["adjustment_kind"])
        controller_descriptor = static_loyalty_cost_modifier_handler(cases[2][0])[1]
        self.assertEqual(
            "source_controller", controller_descriptor["affected_controller"]
        )
        self.assertEqual("loyalty_increase", controller_descriptor["adjustment_kind"])

    def test_near_miss_and_malformed_casting_activation_metadata_fails_closed(
        self,
    ):
        unsupported = (
            "You may cast this card from your graveyard as long as you control "
            "a black or green permanent with an unrepresented ability.",
            "You may cast creature cards from your graveyard as long as you "
            "control a Zombie.",
            "Loyalty abilities of planeswalkers your opponents control cost "
            "{X} more to activate.",
            "You may activate the loyalty abilities of this planeswalker twice "
            "each turn. Spells you cast cost {2} less to cast.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                card_program = self.compile(
                    _record(text, suffix=601_301_000 + index),
                    trust_level="provisional",
                )
                self.assertTrue(card_program.residuals)
                self.assertFalse(
                    any(
                        ability.event
                        in {
                            SELF_ZONE_CAST_PERMISSION_EVENT,
                            LOYALTY_COST_MODIFIER_EVENT,
                        }
                        for ability in card_program.abilities
                    )
                )

        zone_descriptor = static_self_zone_cast_permission_handler(
            "You may cast this card from your graveyard as long as you control "
            "a Zombie."
        )[1]
        loyalty_descriptor = static_loyalty_cost_modifier_handler(
            "Planeswalkers' loyalty abilities you activate cost an additional "
            "[+1] to activate."
        )[1]
        malformed = (
            (
                default_self_zone_cast_permission_registry(),
                {**zone_descriptor, "controlled_subtypes_any": ["Zombie"]},
            ),
            (
                default_self_zone_cast_permission_registry(),
                {**zone_descriptor, "source_zone": "exile"},
            ),
            (
                default_loyalty_cost_modifier_registry(),
                {**loyalty_descriptor, "amount": True},
            ),
            (
                default_loyalty_cost_modifier_registry(),
                {**loyalty_descriptor, "unknown": True},
            ),
        )
        for registry, descriptor in malformed:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(descriptor)

    def test_casting_activation_metadata_compiler_mutant_is_killed(self):
        graveyard = _record(
            "You may cast this card from your graveyard as long as you control "
            "a Zombie.",
            suffix=601_302_001,
        )
        loyalty = _record(
            "Planeswalkers' loyalty abilities you activate cost an additional "
            "[+1] to activate.",
            suffix=601_302_002,
        )

        def assert_event(record: CardRecord, event: str) -> None:
            self.assertTrue(
                any(
                    ability.event == event
                    for ability in self.compile(
                        record,
                        trust_level="provisional",
                    ).abilities
                )
            )

        assert_event(graveyard, SELF_ZONE_CAST_PERMISSION_EVENT)
        with mock.patch(
            "quorune.compiler.runtime_templates."
            "static_self_zone_cast_permission_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_event(graveyard, SELF_ZONE_CAST_PERMISSION_EVENT)

        assert_event(loyalty, LOYALTY_COST_MODIFIER_EVENT)
        with mock.patch(
            "quorune.compiler.runtime_templates.static_loyalty_cost_modifier_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_event(loyalty, LOYALTY_COST_MODIFIER_EVENT)


class SelfZoneCastPermissionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
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
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def prepare_main(session, seat: str) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority(seat)
        engine.pump()

    def test_raw_oracle_zone_permission_without_typed_metadata_fails_closed(self):
        session = self.session(601_303_001)
        engine = session.engine
        crawler = self.card(engine, "B", "Gravecrawler")
        zombie = self.card(engine, "B", "Wight of the Reliquary")
        engine.move_card(crawler.object_id, "graveyard", log=False)
        engine.move_card(zombie.object_id, "battlefield", controller="B", log=False)
        self.assertIn(
            "cast this card from your graveyard",
            self.db.lookup(crawler.printed_name).oracle_text.casefold(),
        )
        for program in tuple(
            engine.semantics.runtime_handler_programs_for_oracle(
                crawler.oracle_id,
                active_zone="graveyard",
                event=SELF_ZONE_CAST_PERMISSION_EVENT,
            )
        ):
            engine.semantics.remove(program.key)

        self.assertFalse(engine._compiled_zone_cast_permission("B", crawler))

    def test_self_graveyard_cast_permission_is_typed_principal_scoped_and_replays(
        self,
    ):
        session = self.session(601_303_002)
        engine = session.engine
        crawler = self.card(engine, "B", "Gravecrawler")
        zombie = self.card(engine, "B", "Wight of the Reliquary")
        engine.move_card(crawler.object_id, "graveyard", log=False)
        engine.state.players["B"].mana_pool["B"] = 1

        self.assertFalse(engine._compiled_zone_cast_permission("B", crawler))
        engine.move_card(zombie.object_id, "battlefield", controller="B", log=False)
        self.assertTrue(engine._compiled_zone_cast_permission("B", crawler))
        self.assertFalse(engine._compiled_zone_cast_permission("A", crawler))
        self.prepare_main(session, "B")
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        owner_decision = session.packet("pilot:B", full=True)["decision"]
        opposing_decision = session.packet("pilot:A", full=True)["decision"]
        owner_actions = owner_decision["ctx"]["legal"]["actions"]
        opposing_actions = (
            opposing_decision.get("ctx", {}).get("legal", {}).get("actions", [])
            if isinstance(opposing_decision, dict)
            else []
        )
        cast = next(
            action
            for action in owner_actions
            if action.get("kind") == "cast" and action.get("card") == crawler.ref
        )
        self.assertNotIn(
            crawler.ref,
            {action.get("card") for action in opposing_actions},
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": cast["id"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", crawler.zone)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-self-zone-cast-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


class LoyaltyCostModifierRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        return session

    def add_modifier(self, session, *, seat: str = "A"):
        engine = session.engine
        record = self.db.lookup("Carth the Lion")
        source = next(
            (
                card
                for card in engine.state.cards.values()
                if card.printed_name == record.name
            ),
            None,
        )
        if source is None:
            from quorune.model import CardInstance

            source = CardInstance(
                object_id="fixture:typed-loyalty-cost-modifier",
                ref=f"{seat}-typed-loyalty-cost-modifier",
                oracle_id=record.oracle_id,
                printed_name=record.name,
                owner=seat,
                controller=seat,
                zone="battlefield",
                known_to=list(engine.seats),
                revealed_to=list(engine.seats),
            )
            engine.state.cards[source.object_id] = source
            engine.state.players[seat].zones["battlefield"].append(
                source.object_id
            )
        else:
            engine.move_card(
                source.object_id,
                "battlefield",
                controller=seat,
                log=False,
            )
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        return source

    @staticmethod
    def prepare_main(engine) -> None:
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine.state.priority_passes = []

    @staticmethod
    def daretti(engine):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Daretti, Scrap Savant"
        )

    def test_typed_loyalty_cost_modifier_replays_exactly(self):
        session = self.session(606_006)
        engine = session.engine
        source = self.add_modifier(session)
        daretti = self.daretti(engine)
        engine.move_card(daretti.object_id, "battlefield", controller="A", log=False)
        self.prepare_main(engine)
        self.assertEqual(
            ("unresolved", "unresolved_loyalty_cost_modification"),
            engine._ability_availability(
                "A", daretti, engine._activated_abilities(daretti)[0]
            ),
        )
        source.phased_out = True
        self.assertEqual(
            ("payable", None),
            engine._ability_availability(
                "A", daretti, engine._activated_abilities(daretti)[0]
            ),
        )
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        before = daretti.counters["loyalty"]
        packet = session.packet("pilot:A", full=True)
        activation = next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action.get("kind") == "activate"
            and action.get("source") == daretti.ref
        )
        result = session.act(
            "pilot:A",
            {"action_id": activation["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before + 2, daretti.counters["loyalty"])

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-loyalty-cost-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()

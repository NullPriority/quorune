from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, load_assets, make_session, pass_current
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardRecord
from quorune.casting_payment_keywords import (
    AffinitySpec,
    CastingPaymentKeywordError,
    DelvePaymentPlan,
)
from quorune.compiler.casting_payment_keyword_nodes import (
    ordinary_delve_keyword_node,
)
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.commit import commit_cast
from quorune.rules.casting.model import CastProposalError, CastProposalRequest
from quorune.rules.casting.proposal import build_cast_offer, build_cast_proposal
from quorune.semantic_runtime import SemanticNodeError
from quorune.semantic_runtime.cast_costs import (
    affinity_handler_descriptor,
    default_cast_cost_component_registry,
)


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


class CastingPaymentKeywordCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    @staticmethod
    def record(
        text: str,
        *,
        keywords: tuple[str, ...],
        mana_cost: str = "{5}",
        type_line: str = "Creature — Fixture",
    ) -> CardRecord:
        return CardRecord(
            oracle_id="00000000-0000-4000-8000-000000000126",
            name="Casting Payment Fixture",
            mana_cost=mana_cost,
            mana_value=5.0,
            type_line=type_line,
            oracle_text=text,
            power="3" if "Creature" in type_line else None,
            toughness="3" if "Creature" in type_line else None,
            loyalty=None,
            defense=None,
            colors=(),
            color_identity=(),
            keywords=keywords,
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={
                "traditional": "legal",
                "commander_duel": "legal",
                "commander_review": "legal",
            },
            faces=(),
            raw={},
        )

    def compile(self, text: str, *, keywords: tuple[str, ...]):
        return compile_oracle_card(
            self.record(text, keywords=keywords),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_battlefield_payment_grants_bind_the_kind_predicate_and_origin(self):
        from quorune.semantic_runtime.spell_payment_grants import SPELL_PAYMENT_GRANT_CAPABILITY

        def witness():
            ir = self.compile("Noncreature spells you cast from exile have convoke.", keywords=())
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIn(SPELL_PAYMENT_GRANT_CAPABILITY, node.capability_dependencies)
            self.assertIn("casting.payment.convoke", node.capability_dependencies)
            descriptor, = node.handlers
            spec, = default_cast_cost_component_registry().lower(descriptor, None)
            self.assertEqual("exile", spec.origin)
            self.assertEqual(("creature",), spec.predicate.excluded_types)
            return descriptor

        descriptor = witness()
        malformed = {**descriptor, "grant": {**descriptor["grant"], "kind": "delve"}}
        with self.assertRaises(SemanticNodeError):
            default_cast_cost_component_registry().lower(malformed, None)
        for text in (
            "Noncreature spells you cast from a hidden zone have convoke.",
            "The first noncreature spell you cast each turn has convoke.",
            "Artifact spells your opponents cast have convoke.",
        ):
            self.assertNotEqual("exact", self.compile(text, keywords=()).status, text)
        with mock.patch("quorune.compiler.spell_payment_grant_templates.spell_payment_grant_handler", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        blocked = next(row for row in raw["capabilities"] if row["id"] == SPELL_PAYMENT_GRANT_CAPABILITY)
        blocked.update(status="blocked", blockers=["Withdraw spell payment grant authority"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.capabilities.evidence_fingerprint)
        held = compile_oracle_card(self.record("Artifact spells you cast have convoke.", keywords=()),
            capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", held.status)

    def test_next_spell_payment_shape_and_window_are_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.next_spell_payment import NEXT_SPELL_PAYMENT_CAPABILITY, NextSpellPaymentWindow

        text = "{T}: The next spell you cast this turn has convoke."

        def witness():
            ir = self.compile(text, keywords=())
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIn(NEXT_SPELL_PAYMENT_CAPABILITY, node.capability_dependencies)
            self.assertIn("casting.payment.convoke", node.capability_dependencies)
            self.assertEqual(({"op": "grant_next_spell_payment", "schema_version": 1,
                               "player": "$controller", "kind": "convoke"},), node.effects)

        witness()
        for bad in (text.replace("you cast", "target player casts"), text.replace("this turn", "next turn")):
            self.assertNotEqual("exact", self.compile(bad, keywords=()).status)
        valid = NextSpellPaymentWindow(19, ("convoke",)).to_dict()
        for malformed in ({"turn_sequence": True}, {"kinds": ["convoke", "convoke"]}, {"kinds": ["trample"]}):
            with self.assertRaises(ValueError):
                NextSpellPaymentWindow.from_dict({**valid, **malformed})
        with mock.patch("quorune.compiler.temporary_player_rule_templates.temporary_player_rule_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        owner = next(row for row in raw["capabilities"] if row["id"] == NEXT_SPELL_PAYMENT_CAPABILITY)
        owner.update(status="blocked", blockers=["Withdraw next spell payment authority"])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(self.capabilities.evidence_fingerprint)
        ir = compile_oracle_card(self.record(text, keywords=()), capability_registry=altered,
                                capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)

    def test_affinity_quality_compiles_closed_effective_queries(self):
        for text, query_count in (
            ("Affinity for creatures", 1),
            ("Affinity for snow lands", 1),
            ("Affinity for outlaws", 1),
            ("Affinity for historic permanents", 3),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, keywords=("Affinity",))
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual("typed-affinity-effective-query-v2", node.template_id)
                self.assertEqual(("casting.payment.affinity",), node.capability_dependencies)
                self.assertEqual("casting.payment.affinity.v2", node.handlers[0]["handler_id"])
                self.assertEqual(query_count, len(node.handlers[0]["payment"]["queries_any"]))

    def test_affinity_query_descriptor_and_unknown_quality_fail_closed(self):
        registry = default_cast_cost_component_registry()
        valid = affinity_handler_descriptor(
            AffinitySpec.for_quality("historic permanents")
        )
        self.assertEqual(
            (AffinitySpec.for_quality("historic permanents"),),
            registry.lower(valid, None),
        )
        malformed = json.loads(json.dumps(valid))
        malformed["payment"]["queries_any"][0]["types_all"] = ["creature"]
        with self.assertRaises(SemanticNodeError):
            registry.validate(malformed)
        ir = self.compile("Affinity for Phyrexians", keywords=("Affinity",))
        self.assertTrue(ir.material_residuals)
        self.assertFalse(any(node.exact for node in ir.faces[0].nodes))

    def test_improvise_descriptor_and_raw_keyword_fail_closed(self):
        ir = self.compile("Improvise", keywords=("Improvise",))
        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual("ordinary-improvise-payment-v1", node.template_id)
        self.assertEqual("casting.payment.improvise.v1", node.handlers[0]["handler_id"])
        registry = default_cast_cost_component_registry()
        malformed = dict(node.handlers[0])
        malformed["payment"] = {"schema_version": 1, "kind": "delve"}
        with self.assertRaises(SemanticNodeError):
            registry.validate(malformed)

    def test_delve_compiler_and_descriptor_are_source_pinned(self):
        ir = self.compile("Delve", keywords=("Delve",))
        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual("ordinary-delve-payment-v1", node.template_id)
        self.assertEqual("casting.payment.delve.v1", node.handlers[0]["handler_id"])
        combined = self.compile(
            "Convoke, delve", keywords=("Convoke", "Delve")
        )
        self.assertEqual("exact", combined.status)
        self.assertEqual(
            {"ordinary-convoke-payment-v1", "ordinary-delve-payment-v1"},
            {node.template_id for node in combined.faces[0].nodes},
        )
        with mock.patch(
            "quorune.compiler.keyword_nodes.ordinary_delve_keyword_node",
            wraps=ordinary_delve_keyword_node,
        ) as lower:
            self.compile("Delve", keywords=("Delve",))
        lower.assert_called_once()

    def test_payment_keyword_instance_multiplicity_matches_rules(self):
        affinity = self.compile(
            "Affinity for artifacts, affinity for artifacts",
            keywords=("Affinity",),
        )
        self.assertEqual(
            2,
            sum(
                node.template_id == "typed-affinity-effective-query-v2"
                for node in affinity.faces[0].nodes
            ),
        )
        for text, keyword, template_id in (
            ("Delve, delve", "Delve", "ordinary-delve-payment-v1"),
            ("Improvise, improvise", "Improvise", "ordinary-improvise-payment-v1"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, keywords=(keyword,))
                self.assertEqual(
                    1,
                    sum(node.template_id == template_id for node in ir.faces[0].nodes),
                )

    def test_fixed_and_hybrid_evoke_compile_to_typed_cost_options(self):
        for text, expected_variants in (
            ("Evoke {2}{U}", 1),
            ("Evoke {R/G}{R/G}", 3),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, keywords=("Evoke",))
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual("fixed-mana-evoke-v1", node.template_id)
                lowered = default_cast_cost_component_registry().lower(
                    node.handlers[0], None
                )[0]
                self.assertEqual(expected_variants, len(lowered.cast_cost_options()))

    def test_evoke_nonmana_descriptor_and_raw_cost_fail_closed(self):
        ir = self.compile(
            "Evoke—Exile a green card from your hand.", keywords=("Evoke",)
        )
        self.assertTrue(ir.material_residuals)
        self.assertFalse(any(node.exact for node in ir.faces[0].nodes))
        valid = self.compile("Evoke {G}", keywords=("Evoke",)).faces[0].nodes[0].handlers[0]
        malformed = json.loads(json.dumps(valid))
        malformed["evoke"]["mana_variants"][0]["G"] = 2
        with self.assertRaises(SemanticNodeError):
            default_cast_cost_component_registry().validate(malformed)


class CastingPaymentKeywordRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = "B"
        engine.permissions.invalidate_current()
        return session

    def add_real_card(self, session, name: str):
        engine = session.engine
        record = self.db.lookup(name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        card = CardInstance(
            object_id=f"fixture:{record.oracle_id}:{engine.state.event_sequence}",
            ref=f"B-{name.casefold().replace(' ', '-')}",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="hand",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=["B"],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["B"].zones["hand"].append(card.object_id)
        return card, record

    @staticmethod
    def artifacts(engine, count: int) -> tuple[str, ...]:
        return tuple(
            engine.create_token(
                "B",
                name=f"Improvise Artifact {index}",
                characteristics={"type_line": "Token Artifact"},
                reason="casting-payment fixture",
            )[0]
            for index in range(count)
        )

    @staticmethod
    def graveyard_cards(
        engine,
        count: int,
        *,
        exclude_refs: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        candidates = [
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.zone in {"hand", "library"}
            and card.ref not in exclude_refs
        ][:count]
        if len(candidates) != count:
            raise AssertionError("fixture lacks graveyard cards")
        for card in candidates:
            engine.move_card(card.object_id, "graveyard", log=False)
        return tuple(card.ref for card in candidates)

    @staticmethod
    def pending_or_stacked_trigger_items(engine):
        return (
            *engine.state.stack,
            *(
                item
                for batch in engine.state.pending_trigger_batches
                for item in batch.items
            ),
        )

    @staticmethod
    def trigger_context(item):
        return item.context if hasattr(item, "context") else item.event_facts

    @staticmethod
    def trigger_semantic_key(item):
        return (
            item.semantic_key
            if hasattr(item, "semantic_key")
            else item.source_ability_id
        )

    def test_affinity_counts_effective_union_and_excludes_opponents(self):
        session = self.session(7024105)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Banish to Another Universe")
        engine.create_token(
            "B",
            name="Legendary Artifact Fixture",
            characteristics={"type_line": "Legendary Artifact"},
            reason="historic Affinity fixture",
        )
        engine.create_token(
            "B",
            name="Saga Fixture",
            characteristics={"type_line": "Enchantment — Saga"},
            reason="historic Affinity fixture",
        )
        dynamic_ref = engine.create_token(
            "B",
            name="Dynamic Historic Fixture",
            characteristics={"type_line": "Creature — Fixture"},
            reason="historic Affinity fixture",
        )[0]
        phased_ref = engine.create_token(
            "B",
            name="Phased Historic Fixture",
            characteristics={"type_line": "Artifact"},
            reason="historic Affinity fixture",
        )[0]
        next(
            card for card in engine.state.cards.values() if card.ref == phased_ref
        ).phased_out = True
        engine.create_token(
            "A",
            name="Opposing Historic Fixture",
            characteristics={"type_line": "Legendary Artifact"},
            reason="historic Affinity fixture",
        )
        engine.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        original_effective = engine._effective_card_data

        def effective_characteristics(value, **kwargs):
            data = original_effective(value, **kwargs)
            card = value if isinstance(value, CardInstance) else engine.state.cards[value]
            if card.ref == dynamic_ref:
                data = dict(data)
                data["type_line"] = "Legendary Creature — Fixture"
            return data

        with mock.patch.object(
            engine,
            "_effective_card_data",
            side_effect=effective_characteristics,
        ):
            options = engine._cast_cost_options(
                "B",
                spell,
                engine.semantics.get(f"{spell.oracle_id}:spell:front"),
                response={},
                hint=False,
            )
        self.assertEqual(1, options[0]["requirements"]["GENERIC"])
        self.assertEqual(1, options[0]["requirements"]["W"])
        self.assertNotIn(
            spell.printed_name,
            json.dumps(session.packet("pilot:A", full=True)["state"], sort_keys=True),
        )

    def test_improvise_compiles_and_pays_only_generic_with_artifacts(self):
        session = self.session(70212601)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Arc Reactor")
        artifacts = self.artifacts(engine, 4)
        engine.state.players["B"].mana_pool["C"] = 1

        options = engine._cast_cost_options(
            "B",
            spell,
            engine.semantics.get(f"{spell.oracle_id}:spell:front"),
            response={"improvise_cards": list(artifacts)},
            hint=False,
        )
        self.assertEqual(1, options[0]["requirements"]["GENERIC"])
        self.assertEqual(
            0,
            sum(
                options[0]["requirements"][symbol]
                for symbol in "WUBRGC"
            ),
        )
        engine._cast("B", {"card": spell.ref, "improvise_cards": list(artifacts)})
        self.assertEqual("stack", spell.zone)
        self.assertTrue(
            all(
                next(card for card in engine.state.cards.values() if card.ref == ref).tapped
                for ref in artifacts
            )
        )

    def test_improvise_offer_is_controller_scoped_and_private(self):
        session = self.session(70212602)
        spell, _ = self.add_real_card(session, "Arc Reactor")
        self.artifacts(session.engine, 5)
        owner = build_cast_offer(session.engine, "B", spell)
        opponent = session.packet("pilot:A", full=True)["state"]
        self.assertEqual("payable", owner.status)
        self.assertNotIn(spell.printed_name, json.dumps(opponent, sort_keys=True))

    def test_granted_and_printed_improvise_share_one_payment_owner(self):
        session = self.session(70212603)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Arc Reactor")
        refs = self.artifacts(engine, 5)
        engine.state.players["B"].stats["next_spell_improvise"] = True
        option = engine._cast_cost_options(
            "B",
            spell,
            engine.semantics.get(f"{spell.oracle_id}:spell:front"),
            response={"improvise_cards": list(refs)},
            hint=False,
        )[0]
        self.assertEqual(1, list(option["choice_schema"]).count("improvise_cards"))
        self._record_replay(session, {"card": spell.ref, "improvise_cards": list(refs)}, "legacy-improvise-window")
        context = engine.state.stack[-1].context
        self.assertTrue(context["granted_improvise"])
        self.assertNotIn("next_spell_payment_grants", context)

    def test_raw_improvise_keyword_without_compiled_descriptor_fails_closed(self):
        session = self.session(70212604)
        spell, _ = self.add_real_card(session, "Arc Reactor")
        with mock.patch(
            "quorune.rules.casting.costs.compiled_improvise_specs",
            return_value=(),
        ):
            offer = build_cast_offer(session.engine, "B", spell)
        self.assertEqual("unpayable", offer.status)

    def test_delve_compiles_and_exiles_selected_graveyard_cards(self):
        session = self.session(7026601)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Gurmag Angler")
        graveyard = self.graveyard_cards(engine, 6, exclude_refs=(spell.ref,))
        engine.state.players["B"].mana_pool["B"] = 1
        engine._cast("B", {"card": spell.ref, "delve_cards": list(graveyard)})
        self.assertEqual("stack", spell.zone)
        self.assertTrue(
            all(
                next(card for card in engine.state.cards.values() if card.ref == ref).zone
                == "exile"
                for ref in graveyard
            )
        )

    def test_delve_descriptor_raw_keyword_and_stale_plan_fail_closed(self):
        session = self.session(7026602)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Gurmag Angler")
        graveyard = self.graveyard_cards(engine, 6, exclude_refs=(spell.ref,))
        engine.state.players["B"].mana_pool["B"] = 1
        response = {"card": spell.ref, "delve_cards": list(graveyard)}
        proposal = build_cast_proposal(
            engine, CastProposalRequest.from_submission("B", response)
        )
        selected = next(
            card for card in engine.state.cards.values() if card.ref == graveyard[0]
        )
        engine.move_card(selected.object_id, "exile", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(CastProposalError, "changed zone or identity"):
            commit_cast(engine, proposal, response)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        with mock.patch(
            "quorune.rules.casting.costs.compiled_delve_specs", return_value=()
        ):
            self.assertEqual("unpayable", build_cast_offer(engine, "B", spell).status)
        malformed = DelvePaymentPlan.from_dict(
            proposal.to_dict()["details"]["selected_cost_option"]["delve_payment"]
        ).to_dict()
        malformed["remaining_requirements"]["B"] = 0
        with self.assertRaises(CastingPaymentKeywordError):
            DelvePaymentPlan.from_dict(malformed)

    def test_convoke_and_delve_compose_in_one_cast_payment(self):
        session = self.session(7026603, players=2)
        engine = session.engine
        base = self.db.lookup("Gurmag Angler")
        record = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000000000166",
            name="Convoke Delve Fixture",
            mana_cost="{3}{G}",
            mana_value=4.0,
            oracle_text="Convoke, delve",
            keywords=("Convoke", "Delve"),
            faces=(),
        )
        register_generated_programs(
            _NoRulingsDatabase(),
            engine.semantics,
            (record,),
            trust_level="trusted",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
        )
        spell = CardInstance(
            object_id="fixture:convoke-delve",
            ref="B-convoke-delve",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="hand",
            known_to=["B"],
        )
        engine.state.cards[spell.object_id] = spell
        engine.state.players["B"].zones["hand"].append(spell.object_id)
        original_card_record = engine.card_record
        original_trust = engine.semantic_program_is_current_trusted

        def card_record(value):
            card = (
                engine.state.cards.get(value)
                if isinstance(value, str)
                else value
            )
            if getattr(card, "oracle_id", None) == record.oracle_id:
                return record
            return original_card_record(value)

        def current_trusted(program):
            if getattr(program, "oracle_id", None) == record.oracle_id:
                return program.trust_level == "trusted" and not program.requires_arbiter
            return original_trust(program)

        with (
            mock.patch.object(engine, "card_record", side_effect=card_record),
            mock.patch.object(
                engine,
                "semantic_program_is_current_trusted",
                side_effect=current_trusted,
            ),
        ):
            graveyard = self.graveyard_cards(
                engine, 2, exclude_refs=(spell.ref,)
            )
            creature_ref = engine.create_token(
                "B",
                name="Green Convoke Creature",
                characteristics={
                    "type_line": "Token Creature",
                    "colors": ["G"],
                    "power": "1",
                    "toughness": "1",
                },
                reason="combined payment fixture",
            )[0]
            engine.state.players["B"].mana_pool["C"] = 1
            response = {
                "card": spell.ref,
                "delve_cards": list(graveyard),
                "convoke_cards": [creature_ref],
            }
            engine._cast("B", response)
        self.assertEqual("stack", spell.zone)
        self.assertTrue(
            next(card for card in engine.state.cards.values() if card.ref == creature_ref).tapped
        )
        self.assertTrue(
            all(
                next(card for card in engine.state.cards.values() if card.ref == ref).zone
                == "exile"
                for ref in graveyard
            )
        )

    def test_delve_offer_is_owner_scoped_and_private(self):
        session = self.session(7026604)
        spell, _ = self.add_real_card(session, "Gurmag Angler")
        graveyard = self.graveyard_cards(
            session.engine, 6, exclude_refs=(spell.ref,)
        )
        session.engine.state.players["B"].mana_pool["B"] = 1
        offer = build_cast_offer(session.engine, "B", spell)
        option = offer.cost_options[0].to_dict()
        self.assertEqual(set(graveyard), set(option["choice_schema"]["delve_cards"]["legal_refs"]))
        self.assertNotIn(
            spell.printed_name,
            json.dumps(session.packet("pilot:A", full=True)["state"], sort_keys=True),
        )

    def _record_replay(self, session, response: dict[str, object], directory: str):
        engine = session.engine
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("B")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"a": "cast", **response})
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / directory
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_improvise_cast_replays_exactly(self):
        session = self.session(70212605, players=2)
        spell, _ = self.add_real_card(session, "Arc Reactor")
        artifacts = self.artifacts(session.engine, 4)
        session.engine.state.players["B"].mana_pool["C"] = 1
        self._record_replay(
            session,
            {"card": spell.ref, "improvise_cards": list(artifacts)},
            "improvise-replay",
        )

    def test_delve_cast_replays_exactly(self):
        session = self.session(7026605, players=2)
        spell, _ = self.add_real_card(session, "Gurmag Angler")
        graveyard = self.graveyard_cards(
            session.engine, 6, exclude_refs=(spell.ref,)
        )
        session.engine.state.players["B"].mana_pool["B"] = 1
        self._record_replay(
            session,
            {"card": spell.ref, "delve_cards": list(graveyard)},
            "delve-replay",
        )

    def test_evoke_cost_marks_and_sacrifices_on_entry(self):
        session = self.session(7027401, players=2)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Walker of the Grove")
        engine.state.players["B"].mana_pool.update({"C": 4, "G": 1})
        engine._cast("B", {"card": spell.ref, "cost_option": "evoke"})
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        evoke_items = [
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:sacrifice-source"
            and item.context.get("evoke") is True
        ]
        self.assertEqual(1, len(evoke_items))
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", spell.zone)

    def test_evoke_uses_shared_trigger_owner_with_reviewed_override(self):
        session = self.session(7027402, players=2)
        engine = session.engine
        endurance = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Endurance"
        )
        green = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Birds of Paradise"
        )
        engine.move_card(endurance.object_id, "hand", log=False)
        engine.move_card(green.object_id, "hand", log=False)
        engine._cast(
            "B",
            {
                "card": endurance.ref,
                "cost_option": "evoke",
                "exile_card": green.ref,
                "pay": "manual",
                "payment": {},
            },
        )
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        evoke_items = [
            item
            for item in self.pending_or_stacked_trigger_items(engine)
            if self.trigger_context(item).get("evoke") is True
        ]
        self.assertEqual(1, len(evoke_items))
        self.assertEqual(
            "builtin:sacrifice-source",
            self.trigger_semantic_key(evoke_items[0]),
        )

    def test_evoke_trigger_uses_ordinary_apnap_batching(self):
        session = self.session(7027403, players=4)
        engine = session.engine
        spell, _ = self.add_real_card(session, "Walker of the Grove")
        engine.state.players["B"].mana_pool.update({"C": 4, "G": 1})
        engine._cast("B", {"card": spell.ref, "cost_option": "evoke"})
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        item = next(
            item
            for item in self.pending_or_stacked_trigger_items(engine)
            if self.trigger_context(item).get("evoke") is True
        )
        self.assertEqual("B", item.controller)
        self.assertEqual(list(engine.seats), item.visibility)

    def test_evoke_offer_keeps_hand_identity_private(self):
        session = self.session(7027404)
        spell, _ = self.add_real_card(session, "Walker of the Grove")
        session.engine.state.players["B"].mana_pool.update({"C": 4, "G": 1})
        offer = build_cast_offer(session.engine, "B", spell)
        self.assertTrue(any(option.option_id == "evoke" for option in offer.cost_options))
        self.assertNotIn(
            spell.printed_name,
            json.dumps(session.packet("pilot:A", full=True)["state"], sort_keys=True),
        )

    def test_evoke_cast_and_trigger_replay_exactly(self):
        session = self.session(7027405, players=2)
        spell, _ = self.add_real_card(session, "Walker of the Grove")
        session.engine.state.players["B"].mana_pool.update({"C": 4, "G": 1})
        engine = session.engine
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("B")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"a": "cast", "card": spell.ref, "cost_option": "evoke"},
        )
        self.assertTrue(result.ok, result.summary)
        for _ in range(8):
            if spell.zone == "graveyard":
                break
            pass_current(session)
        self.assertEqual("graveyard", spell.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "evoke-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


class BattlefieldSpellPaymentGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "spell-payment-grants.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/scryfall-exact-lists.json",
            ROOT / "tests/fixtures/battlefield-spell-payment-grant-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Spell payment grants", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 20),
        ], ["Generic Bound Commander"])

    def test_actual_chief_engineer_grants_convoke_only_to_its_controllers_artifact_spells(self):
        session = self.session(70251021)
        engine = session.engine
        provider = self.add(engine, "Chief Engineer", seat="B")
        engine.change_control(provider.object_id, "A", reason="spell payment grant fixture")
        payer = self.add(engine, "Generic Bound Body", ref="own-convoke-creature")
        foreign = self.add(engine, "Generic Bound Body", seat="B", ref="other-convoke-creature")
        ring = self.add(engine, "Sol Ring", zone="hand")
        other = self.add(engine, "Lone Missionary", zone="hand")
        hidden = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="private-grant-control")
        for card in (provider, ring, other):
            self.assert_closed(session, card)
        self.prepare(session)
        self.checkpoint(session)
        offer = build_cast_offer(engine, "A", ring)
        self.assertEqual("payable", offer.status)
        refs = offer.cost_options[0].to_dict()["choice_schema"]["convoke_cards"]["legal_refs"]
        self.assertEqual({provider.ref, payer.ref}, set(refs))
        self.assertEqual("unpayable", build_cast_offer(engine, "A", other).status)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(f"cast:{ring.ref}", {row["id"] for row in actions})
        self.assertNotIn(f"cast:{other.ref}", {row["id"] for row in actions})
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
        before = authoritative_state_hash(session.state)
        for actor, refs in (("B", [payer.ref]), ("A", [foreign.ref])):
            result = session.act(f"pilot:{actor}", {"action_id": f"cast:{ring.ref}", "convoke_cards": refs, "pay": "auto"})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": f"cast:{ring.ref}", "convoke_cards": [payer.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[payer.object_id].tapped)
        self.assertEqual("stack", session.state.cards[ring.object_id].zone)
        self.replay(session, load=True)

    def test_actual_statuary_and_teval_use_their_canonical_payment_resources(self):
        for index, (name, kind) in enumerate((("Inspiring Statuary", "improvise"), ("Teval, Arbiter of Virtue", "delve"))):
            with self.subTest(provider=name):
                session = self.session(70251022 + index)
                engine = session.engine
                provider = self.add(engine, name)
                spell = self.add(engine, "Lone Missionary", zone="hand")
                control = self.add(engine, "Sol Ring", zone="hand")
                if kind == "improvise":
                    payer = provider
                    foreign = self.add(engine, "Generic Bound Mentor", seat="C", ref="other-improvise-artifact")
                else:
                    payer = self.add(engine, "Generic Bound Growth", zone="graveyard", ref="own-delve-card")
                    foreign = self.add(engine, "Generic Bound Growth", seat="C", zone="graveyard", ref="other-delve-card")
                self.assert_closed(session, provider)
                self.assert_closed(session, spell)
                self.prepare(session, mana={"W": 1})
                self.checkpoint(session)
                field = f"{kind}_cards"
                offer = build_cast_offer(engine, "A", spell)
                self.assertEqual("payable", offer.status)
                self.assertEqual([payer.ref], offer.cost_options[0].to_dict()["choice_schema"][field]["legal_refs"])
                if kind == "improvise":
                    other = build_cast_offer(engine, "A", control)
                    self.assertTrue(all(field not in option.to_dict().get("choice_schema", {}) for option in other.cost_options))
                before = authoritative_state_hash(session.state)
                failed = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", field: [foreign.ref], "pay": "auto"})
                self.assertFalse(failed.ok, failed.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                life = session.state.players["A"].life
                result = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", field: [payer.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                current = session.state.cards[payer.object_id]
                if kind == "improvise":
                    self.assertTrue(current.tapped)
                else:
                    self.assertEqual("exile", current.zone)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(life + (4 if kind == "improvise" else 2), session.state.players["A"].life)
                self.replay(session, load=True)

    def test_actual_fallaji_grant_rechecks_the_provider_after_mana_payment(self):
        session = self.session(70251027)
        engine = session.engine
        provider = self.add(engine, "Fallaji Wayfarer")
        spell = self.add(engine, "Baleful Strix", zone="hand")
        for card in (provider, spell):
            self.assert_closed(session, card)
        self.prepare(session, mana={"U": 1})
        self.checkpoint(session)
        self.assertEqual({"W", "U", "B", "R", "G"}, set(engine._effective_card_data(provider)["colors"]))
        self.assertEqual(("G",), engine.card_record(provider).color_identity)
        offer = build_cast_offer(engine, "A", spell)
        self.assertEqual("payable", offer.status)
        self.assertIn(provider.ref, offer.cost_options[0].to_dict()["choice_schema"]["convoke_cards"]["legal_refs"])
        command = {"action_id": f"cast:{spell.ref}", "convoke_cards": [provider.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        pay = engine._pay_for_cost

        def pay_then_remove_provider(*args, **kwargs):
            paid = pay(*args, **kwargs)
            engine.move_card(provider.object_id, "graveyard", log=False)
            return paid

        with mock.patch.object(engine, "_pay_for_cost", side_effect=pay_then_remove_provider) as payment:
            rejected = session.act("pilot:A", command)
            payment.assert_called_once()
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertIn("spell payment ability was lost", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[provider.object_id].tapped)
        self.assertEqual("stack", session.state.cards[spell.object_id].zone)
        self.replay(session, load=True)

    def test_actual_dazzling_theater_grant_requires_its_unlocked_door(self):
        from quorune.shared_type_doors import SharedTypeDoors

        faces = ("Dazzling Theater", "Prop Room")
        for index, face in enumerate(faces):
            with self.subTest(unlocked=face):
                session = self.session(70251032 + index)
                engine = session.engine
                room = self.add(engine, "Dazzling Theater // Prop Room")
                room.shared_type_doors = SharedTypeDoors(faces, (face,))
                payer = self.add(engine, "Generic Bound Body", ref="room-convoke-creature")
                spell = self.add(engine, "Lone Missionary", zone="hand")
                self.assert_closed(session, room)
                self.prepare(session, mana={"C": 1})
                self.checkpoint(session)
                offer = build_cast_offer(engine, "A", spell)
                if face == "Prop Room":
                    self.assertEqual("unpayable", offer.status)
                    continue
                self.assertEqual("payable", offer.status)
                command = {"action_id": f"cast:{spell.ref}", "convoke_cards": [payer.ref], "pay": "auto"}
                accepted = session.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertTrue(session.state.cards[payer.object_id].tapped)
                self.replay(session, load=True)

    def activate_next_spell_payment(self, session, source):
        self.priority(session, "A")
        ability, = (candidate for candidate in session.engine._activated_abilities(source) if not candidate.mana_ability)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {row["id"] for row in actions})
        activated = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertIsNone(self.resolve(session))

    def test_actual_wand_grant_is_redundant_and_consumed_with_or_without_convoke(self):
        from quorune.rules.next_spell_payment import NEXT_SPELL_PAYMENT_FIELD

        session = self.session(70251042)
        engine = session.engine
        first = self.add(engine, "Wand of the Worldsoul", ref="first-payment-wand")
        second = self.add(engine, "Wand of the Worldsoul", ref="second-payment-wand")
        payer = self.add(engine, "Generic Bound Body", ref="wand-convoke-creature")
        self.add(engine, "Generic Bound Body", ref="untapped-after-first-cast")
        foreign = self.add(engine, "Generic Bound Body", seat="B", ref="other-next-spell-creature")
        hidden = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="private-wand-control")
        spell = self.add(engine, "Sol Ring", zone="hand")
        following = self.add(engine, "Ichor Wellspring", zone="hand")
        for card in (first, second, spell, following):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        self.activate_next_spell_payment(session, first)
        self.activate_next_spell_payment(session, second)
        self.assertEqual(["convoke"], session.state.players["A"].stats[NEXT_SPELL_PAYMENT_FIELD]["kinds"])
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
        paid_with_mana = self.replay(session, load=True)
        for game, selected in ((session, [payer.ref]), (paid_with_mana, [])):
            with self.subTest(paid_by_convoke=bool(selected)):
                self.priority(game, "A")
                command = {"action_id": f"cast:{spell.ref}", "convoke_cards": selected, "pay": "auto"}
                before = authoritative_state_hash(game.state)
                rejected = game.act("pilot:A", {**command, "convoke_cards": [foreign.ref]})
                self.assertFalse(rejected.ok, rejected.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertIn(NEXT_SPELL_PAYMENT_FIELD, game.state.players["A"].stats)
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual(bool(selected), game.state.cards[payer.object_id].tapped)
                self.assertNotIn(NEXT_SPELL_PAYMENT_FIELD, game.state.players["A"].stats)
                self.assertEqual(["convoke"], game.state.stack[-1].context["next_spell_payment_grants"])
                self.assertIsNone(self.resolve(game))
                self.priority(game, "A")
                offer = build_cast_offer(game.engine, "A", game.state.cards[following.object_id])
                self.assertEqual("payable", offer.status)
                self.assertTrue(all("convoke_cards" not in option.to_dict().get("choice_schema", {})
                                    for option in offer.cost_options))
                self.replay(game, load=True)

    def test_actual_flockchaser_grant_survives_the_attacking_source_departure(self):
        session = self.session(70251045)
        engine = session.engine
        source = self.add(engine, "Flockchaser Phantom")
        source.acquired_control_turn_count = -1
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        self.add(engine, "Island", seat="B")
        payer = self.add(engine, "Generic Bound Body", ref="flock-convoke-creature")
        spell = self.add(engine, "Sol Ring", zone="hand")
        for card in (source, bounce, spell):
            self.assert_closed(session, card)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(source, "B")])
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast(session, "B", bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.phase == "postcombat_main")
        self.priority(session, "A")
        result = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "convoke_cards": [payer.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[payer.object_id].tapped)
        self.assertEqual(["convoke"], session.state.stack[-1].context["next_spell_payment_grants"])
        self.replay(session, load=True)

    def test_actual_archway_grant_pays_with_an_artifact_and_expires_at_cleanup(self):
        from quorune.rules.next_spell_payment import NEXT_SPELL_PAYMENT_FIELD

        session = self.session(70251046)
        engine = session.engine
        archway = self.add(engine, "Archway of Innovation", zone="hand")
        self.add(engine, "Island")
        artifact = self.add(engine, "Generic Bound Mentor", ref="archway-improvise-artifact")
        spell = self.add(engine, "Lone Missionary", zone="hand")
        self.assert_closed(session, archway)
        self.prepare(session, mana={"U": 1, "W": 1})
        self.checkpoint(session)
        played = session.act("pilot:A", {"action_id": f"play-land:{archway.ref}"})
        self.assertTrue(played.ok, played.summary)
        self.assertFalse(session.state.cards[archway.object_id].tapped)
        self.activate_next_spell_payment(session, archway)
        self.assertEqual(["improvise"], session.state.players["A"].stats[NEXT_SPELL_PAYMENT_FIELD]["kinds"])
        expired = self.replay(session, load=True)
        turn = expired.state.turn_sequence
        self.until(expired, lambda game: game.state.turn_sequence > turn
                   or (game.state.pending_decision is not None and game.state.pending_decision.kind == "cleanup.discard"))
        if expired.state.turn_sequence == turn:
            decision = expired.state.pending_decision
            self.assertEqual("cleanup.discard", decision.kind)
            choice = decision.payload_by_actor["A"]
            discarded = expired.act("pilot:A", {"a": "discard", "cards": [row["id"] for row in choice["hand"][:choice["count"]]]})
            self.assertTrue(discarded.ok, discarded.summary)
        self.until(expired, lambda game: game.state.turn_sequence > turn)
        self.assertNotIn(NEXT_SPELL_PAYMENT_FIELD, expired.state.players["A"].stats)
        self.replay(expired, load=True)
        self.priority(session, "A")
        result = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "improvise_cards": [artifact.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[artifact.object_id].tapped)
        self.assertEqual(["improvise"], session.state.stack[-1].context["next_spell_payment_grants"])
        self.assertNotIn(NEXT_SPELL_PAYMENT_FIELD, session.state.players["A"].stats)
        self.replay(session, load=True)

    def test_actual_wand_and_archway_compose_disjoint_tap_payment_resources(self):
        from quorune.rules.next_spell_payment import NEXT_SPELL_PAYMENT_FIELD

        for overlap in (False, True):
            with self.subTest(dual_eligible_permanent=overlap):
                session = self.session(70251050 + int(overlap))
                engine = session.engine
                wand = self.add(engine, "Wand of the Worldsoul")
                archway = self.add(engine, "Archway of Innovation")
                payer = self.add(engine, "Generic Bound Body", ref="mixed-white-creature")
                artifact = self.add(engine, "Ichor Wellspring")
                dual = self.add(engine, "Generic Bound Mentor", ref="dual-payment-object") if overlap else None
                spell = self.add(engine, "Lone Missionary", zone="hand")
                for card in (wand, archway, artifact, spell):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"U": 1})
                self.checkpoint(session)
                self.activate_next_spell_payment(session, wand)
                self.activate_next_spell_payment(session, archway)
                self.assertEqual(["convoke", "improvise"], session.state.players["A"].stats[NEXT_SPELL_PAYMENT_FIELD]["kinds"])
                self.priority(session, "A")
                offer = build_cast_offer(engine, "A", spell)
                self.assertEqual("payable", offer.status)
                option = offer.cost_options[0].to_dict()
                if overlap:
                    for field in ("convoke_cards", "improvise_cards"):
                        self.assertIn(dual.ref, option["choice_schema"][field]["legal_refs"])
                    before = authoritative_state_hash(session.state)
                    rejected = session.act("pilot:A", {"action_id": f"cast:{spell.ref}",
                        "convoke_cards": [dual.ref], "improvise_cards": [dual.ref], "pay": "auto"})
                    self.assertFalse(rejected.ok, rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                else:
                    self.assertEqual({"convoke_cards": [payer.ref], "improvise_cards": [artifact.ref]},
                                     option["recommended_payment_refs"])
                result = session.act("pilot:A", {"action_id": f"cast:{spell.ref}",
                    "convoke_cards": [payer.ref], "improvise_cards": [artifact.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertTrue(session.state.cards[payer.object_id].tapped)
                self.assertTrue(session.state.cards[artifact.object_id].tapped)
                self.assertNotIn(NEXT_SPELL_PAYMENT_FIELD, session.state.players["A"].stats)
                self.assertEqual(["convoke", "improvise"], session.state.stack[-1].context["next_spell_payment_grants"])
                self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.abilities import parse_activated_abilities


class ActivatedAbilityAndCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @staticmethod
    def _owned_named(engine, seat: str, name: str):
        matches = [
            card for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name and card.zone != "outside"
        ]
        if len(matches) != 1:
            raise AssertionError(f"Expected one {seat} {name}, found {len(matches)}")
        return matches[0]

    @staticmethod
    def _priority_for(session, seat: str):
        session.engine.permissions.invalidate_current()
        session.engine.state.priority_player = None
        session.engine._grant_priority(seat)
        session.engine.pump()
        assert session.pending_principals() == [f"pilot:{seat}"]

    def test_channel_is_exposed_from_hand_and_pays_authoritative_discounted_cost(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=501)
        keep_all(session)
        engine = session.engine
        boseiju = self._owned_named(engine, "B", "Boseiju, Who Endures")
        if boseiju.zone != "hand":
            engine.move_card(boseiju.object_id, "hand", log=False)
        commander_id = engine.state.players["B"].zones["command"][0]
        engine.move_card(commander_id, "battlefield", controller="B", log=False)
        target = self._owned_named(engine, "A", "Sol Ring")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.state.players["B"].mana_pool["G"] = 1
        self._priority_for(session, "B")

        packet = session.packet("pilot:B")
        abilities = packet["decision"]["ctx"]["legal"]["abilities"]
        hint = next(item for item in abilities if item["s"] == boseiju.ref and item["a"] == "ab2")
        self.assertEqual("hand", hint["z"])
        self.assertEqual({"GENERIC": 1, "G": 1}, hint["m"])
        self.assertEqual(1, hint["legend_discount"])

        result = session.act(
            "pilot:B",
            {
                "a": "x",
                "source": boseiju.ref,
                "from": "hand",
                "ability": "ab2",
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"G": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", boseiju.zone)
        self.assertEqual(0, engine.state.players["B"].mana_pool["G"])
        self.assertEqual("activated_ability", engine.state.stack[-1].kind)
        self.assertEqual(boseiju.object_id, engine.state.stack[-1].source_object_id)

    def test_zimone_cost_selection_is_validated_and_paid_by_kernel(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=502)
        keep_all(session)
        engine = session.engine
        commander_id = engine.state.players["B"].zones["command"][0]
        commander = engine.move_card(commander_id, "battlefield", controller="B", log=False)
        commander.acquired_control_turn_count = engine.state.players["B"].turns_begun - 1
        token_ref = engine.create_token(
            "B",
            name="Test Creature",
            characteristics={"type_line": "Creature — Test", "power": "1", "toughness": "1"},
        )[0]
        token = next(card for card in engine.state.cards.values() if card.ref == token_ref)
        self._priority_for(session, "B")

        result = session.act(
            "pilot:B",
            {
                "a": "x",
                "source": commander.ref,
                "ability": "ab2",
                "cost_cards": [token.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(commander.tapped)
        self.assertEqual("outside", token.zone)  # token ceases to exist after leaving the battlefield
        self.assertEqual("activated_ability", engine.state.stack[-1].kind)

    def test_strategic_mana_ability_with_sacrifice_is_not_auto_hidden(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=503)
        keep_all(session)
        engine = session.engine
        tower = self._owned_named(engine, "B", "Phyrexian Tower")
        engine.move_card(tower.object_id, "battlefield", controller="B", log=False)
        token_ref = engine.create_token(
            "B",
            name="Tower Fodder",
            characteristics={"type_line": "Creature — Test", "power": "1", "toughness": "1"},
        )[0]
        token = next(card for card in engine.state.cards.values() if card.ref == token_ref)
        self._priority_for(session, "B")

        packet = session.packet("pilot:B")
        abilities = packet["decision"]["ctx"]["legal"]["abilities"]
        self.assertTrue(any(item["s"] == tower.ref and item["a"] == "ab2" for item in abilities))
        result = session.act(
            "pilot:B",
            {"a": "x", "source": tower.ref, "ability": "ab2", "cost_cards": [token.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(tower.tapped)
        self.assertEqual(2, engine.state.players["B"].mana_pool["B"])
        self.assertEqual("outside", token.zone)
        self.assertFalse(engine.state.stack)  # mana abilities do not use the stack


    def test_pilot_cannot_cast_from_graveyard_without_compiled_permission(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=505)
        keep_all(session)
        engine = session.engine
        signet = self._owned_named(engine, "A", "Arcane Signet")
        engine.move_card(signet.object_id, "graveyard", log=False)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.phase_index = 3
        self._priority_for(session, "A")
        result = session.act(
            "pilot:A",
            {"a": "c", "card": signet.ref, "from": "graveyard", "pay": "manual", "payment": {}},
        )
        self.assertFalse(result.ok)
        self.assertIn("not authorized by a compiled zone permission", result.summary)
        self.assertEqual("graveyard", signet.zone)

    def test_pilot_cannot_understate_ordinary_spell_cost(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=504)
        keep_all(session)
        engine = session.engine
        signet = self._owned_named(engine, "A", "Arcane Signet")
        if signet.zone != "hand":
            engine.move_card(signet.object_id, "hand", log=False)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.phase_index = 3
        self._priority_for(session, "A")

        result = session.act(
            "pilot:A",
            {"a": "c", "card": signet.ref, "declared_cost": {"GENERIC": 0}},
        )
        self.assertFalse(result.ok)
        self.assertIn("does not match authoritative cost", result.summary)
        self.assertEqual("hand", signet.zone)
        self.assertIsNotNone(engine.permissions.capability_for("pilot:A"))

    def test_boseiju_channel_is_not_advertised_with_one_green_source(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=506)
        keep_all(session)
        engine = session.engine
        boseiju = self._owned_named(engine, "B", "Boseiju, Who Endures")
        breeding_pool = self._owned_named(engine, "B", "Breeding Pool")
        engine.move_card(boseiju.object_id, "hand", log=False)
        engine.move_card(
            breeding_pool.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        hints = engine._priority_action_hints("B")

        self.assertFalse(
            any(
                item["s"] == boseiju.ref and item["a"] == "ab2"
                for item in hints["abilities"]
            )
        )
        self.assertTrue(
            any(
                item.get("s") == boseiju.ref
                and item.get("a") == "ab2"
                and item.get("reason") == "insufficient_mana"
                for item in hints["diagnostic"]["unpayable"]
            )
        )

    def test_boseiju_channel_is_advertised_with_two_sufficient_sources(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=507)
        keep_all(session)
        engine = session.engine
        boseiju = self._owned_named(engine, "B", "Boseiju, Who Endures")
        breeding_pool = self._owned_named(engine, "B", "Breeding Pool")
        island = self._owned_named(engine, "B", "Island")
        target = self._owned_named(engine, "A", "Sol Ring")
        engine.move_card(boseiju.object_id, "hand", log=False)
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        for card in (breeding_pool, island):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="B",
                tapped=False,
                log=False,
            )
        breeding_pool.tapped = False
        hints = engine._priority_action_hints("B")

        self.assertTrue(
            any(
                item["s"] == boseiju.ref and item["a"] == "ab2"
                for item in hints["abilities"]
            )
        )

    def test_divining_top_payability_and_tap_availability(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=508)
        keep_all(session)
        engine = session.engine
        top = self._owned_named(engine, "A", "Sensei's Divining Top")
        island = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Island"
        )
        for card in (top, island):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                tapped=False,
                log=False,
            )

        hints = engine._priority_action_hints("A")
        payable = {
            (item["s"], item["a"]) for item in hints["abilities"]
        }
        self.assertIn((top.ref, "ab1"), payable)
        self.assertIn((top.ref, "ab2"), payable)

        top.tapped = True
        tapped_hints = engine._priority_action_hints("A")
        tapped_payable = {
            (item["s"], item["a"])
            for item in tapped_hints["abilities"]
        }
        self.assertIn((top.ref, "ab1"), tapped_payable)
        self.assertNotIn((top.ref, "ab2"), tapped_payable)

    def test_mox_opal_requires_public_metalcraft(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=509)
        keep_all(session)
        engine = session.engine
        opal = self._owned_named(engine, "A", "Mox Opal")
        engine.move_card(
            opal.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )

        hints = engine._priority_action_hints("A")
        self.assertFalse(
            any(
                item["s"] == opal.ref and item["a"] == "ab1"
                for item in hints["mana_abilities"]
            )
        )
        self.assertFalse(
            any(source.object_id == opal.object_id for source in engine.available_mana_sources("A"))
        )

        result = session.act(
            "pilot:A",
            {"a": "x", "source": opal.ref, "ability": "ab1", "mana_choice": "U"},
        )
        self.assertFalse(result.ok)
        self.assertIn("requires_3_artifacts", result.summary)

        for name in ("Sensei's Divining Top", "Arcane Signet"):
            artifact = self._owned_named(engine, "A", name)
            engine.move_card(
                artifact.object_id,
                "battlefield",
                controller="A",
                tapped=False,
                log=False,
            )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        payable = engine._priority_action_hints("A")
        self.assertTrue(
            any(
                item["s"] == opal.ref and item["a"] == "ab1"
                for item in payable["mana_abilities"]
            )
        )
        self.assertTrue(
            any(source.object_id == opal.object_id for source in engine.available_mana_sources("A"))
        )

    def test_parenthesized_basic_land_reminder_uses_intrinsic_abilities(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=510)
        keep_all(session)
        engine = session.engine
        pool = self._owned_named(engine, "B", "Breeding Pool")
        engine.move_card(
            pool.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        pool.tapped = False

        hints = engine._priority_action_hints("B")
        pool_abilities = {
            item["a"]
            for item in hints["mana_abilities"]
            if item["s"] == pool.ref and not item.get("needs_rules")
        }
        self.assertEqual(
            {"intrinsic_island", "intrinsic_forest"},
            pool_abilities,
        )
        self.assertNotIn("ab1", pool_abilities)
        self.assertFalse(
            any(
                item.get("s") == pool.ref
                for item in hints["diagnostic"]["unresolved_cost_semantics"]
            )
        )

    def test_token_reminder_text_does_not_create_source_mana_ability(self):
        abilities = parse_activated_abilities(
            card_name="An Offer You Can't Refuse",
            oracle_text=(
                "Counter target noncreature spell. Its controller creates "
                "two Treasure tokens. (They're artifacts with "
                '"{T}, Sacrifice this token: Add one mana of any color.")'
            ),
            keywords=("Treasure",),
        )

        self.assertEqual((), abilities)

    def test_legacy_crew_parser_remains_card_agnostic_for_v3_compatibility(self):
        abilities = parse_activated_abilities(
            card_name="Example Vehicle",
            oracle_text="Crew 3",
            keywords=("Crew",),
        )

        self.assertEqual(1, len(abilities))
        self.assertEqual("crew", abilities[0].ability_id)
        self.assertEqual(3, abilities[0].crew_threshold)
        self.assertTrue(abilities[0].compiled_cost)

    def test_craft_reminder_compiles_both_generic_cost_choices(self):
        abilities = parse_activated_abilities(
            card_name="Example Relic",
            oracle_text=(
                "Craft with creature {4}{B} ({4}{B}, Exile this artifact, "
                "Exile a creature you control or a creature card from your "
                "graveyard: Return this card transformed under its owner's "
                "control. Craft only as a sorcery.)"
            ),
            keywords=("Craft",),
        )

        self.assertEqual(
            {"craft_battlefield", "craft_graveyard"},
            {ability.ability_id for ability in abilities},
        )
        self.assertEqual(
            {"battlefield", "graveyard"},
            {ability.choices[0].zone for ability in abilities},
        )
        self.assertTrue(all(ability.compiled_cost for ability in abilities))
        self.assertTrue(all(ability.sorcery_speed for ability in abilities))

    def test_quoted_granted_ability_is_not_source_activated_ability(self):
        abilities = parse_activated_abilities(
            card_name="Insidious Roots",
            oracle_text=(
                'Creature tokens you control have "{T}: Add one mana of '
                'any color."'
            ),
        )

        self.assertEqual((), abilities)

    def test_graveyard_target_does_not_move_source_ability_zone(self):
        engineer = self.db.lookup("Goblin Engineer")
        ability = parse_activated_abilities(
            card_name=engineer.name,
            oracle_text=engineer.oracle_text,
            keywords=engineer.keywords,
        )[0]

        self.assertEqual(("battlefield",), ability.zones)

    def test_cycling_keyword_compiles_hand_discard_activation(self):
        triome = self.db.lookup("Zagoth Triome")
        ability = next(
            ability
            for ability in parse_activated_abilities(
                card_name=triome.name,
                oracle_text=triome.oracle_text,
                keywords=triome.keywords,
            )
            if ability.ability_id == "ab3"
        )

        self.assertEqual(("hand",), ability.zones)
        self.assertTrue(ability.discard_source)
        self.assertEqual(3, ability.mana["GENERIC"])
        self.assertEqual("Draw a card.", ability.effect_text)

    def test_elvish_reclaimer_land_sacrifice_cost_is_compiled(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=511)
        keep_all(session)
        engine = session.engine
        reclaimer = self._owned_named(engine, "B", "Elvish Reclaimer")
        lands = [
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and (record := engine.card_record(card))
            and record.is_land
        ][:3]
        engine.move_card(
            reclaimer.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        reclaimer.acquired_control_turn_count = (
            engine.state.players["B"].turns_begun - 1
        )
        for land in lands:
            engine.move_card(
                land.object_id,
                "battlefield",
                controller="B",
                tapped=False,
                log=False,
            )

        hints = engine._priority_action_hints("B")
        self.assertFalse(
            any(
                item.get("s") == reclaimer.ref
                for item in hints["diagnostic"]["unresolved_cost_semantics"]
            )
        )
        self.assertTrue(
            any(
                item["s"] == reclaimer.ref
                and item["a"] == "ab2"
                and item["choose_cost"][0]["q"]["types_any"] == ["land"]
                for item in hints["abilities"]
            )
        )


if __name__ == "__main__":
    unittest.main()

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_keyword_removal import CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
BOUND = "resolution.effect_program.bound_references"
DAMAGE = "damage.result.multitype_permanent"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = {
    "Vertigo": ("spell_ability", 2, "Flying"),
    "Burning Palm Efreet": ("activated_ability", 2, "Flying"),
    "Soul Sear": ("spell_ability", 5, "Indestructible"),
    "Rebel Salvo": ("spell_ability", 5, "Indestructible"),
}


class DamageThenKeywordRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "announced-x-public-damage-set-cards.json": {"Air Elemental", "Mending Hands"},
            "attached-destruction-set-cards.json": {"Island", "Grizzly Bears", "Unsummon"},
            "temporary-keyword-removal-cards.json": {"Canopy Claws"},
            "targeted-controller-untap-cards.json": {"Ajani Vengeant"},
            "ability-kind-cost-modifier-cards.json": {"Bonesplitter"},
            "damage-death-exile-cards.json": {"Darksteel Myr", "Smite the Deathless"},
        }
        fixtures = [FIXTURES / "damage-then-keyword-removal-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            oracle_ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(piece)
        database = folder / "damage-and-removal.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered damage and keyword removal", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_each_complete_original_keeps_exact_damage_then_removal(self):
        for name, (kind, amount, keyword) in ORIGINALS.items():
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = [n for n in ir.faces[0].nodes if n.kind == kind]
                self.assertTrue({DAMAGE, CAPABILITY, BOUND, "damage.source.stack_current_or_lki",
                    "target.revalidate_resolution"} <= set(node.capability_dependencies))
                first, removal = node.effects
                self.assertEqual({"op": "damage", "source": "$source", "target": "$target.0", "amount": amount}, first)
                self.assertEqual(("$target.0", 11, [keyword], [], 0, 0),
                    (removal["card"], removal["schema_version"], removal["remove_keywords"], removal["keywords"],
                     removal["power"], removal["toughness"]))
                schema = node.target_schema
                self.assertEqual((["battlefield"], ["permanent"], 1),
                    (schema["zones"], schema["categories"], schema["count"]))
                if keyword == "Flying":
                    self.assertEqual((["creature"], ["flying"]), (schema["types_all"], schema["keywords_all"]))
                else:
                    self.assertEqual(["creature", "planeswalker"], schema["types_any"])
                    self.assertNotIn("keywords_all", schema)
                if name == "Rebel Salvo":
                    self.assertTrue(any("casting.payment.affinity" in other.capability_dependencies
                        for other in ir.faces[0].nodes))
                binding, _ = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_wrong_order_slot_amount_domain_or_rider_cannot_bind_the_pair(self):
        card = self.db.lookup("Vertigo", fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        damage, removal = thaw_value(program.effects)
        schema = thaw_value(program.target_schema)
        for changes in (
            {"effects": [removal, damage]},
            {"effects": [{**damage, "amount": True}, removal]},
            {"effects": [{**damage, "amount": 0}, removal]},
            {"effects": [damage, {**removal, "card": "$target.1"}]},
            {"effects": [damage, {**removal, "power": 1}]},
            {"effects": [damage, {**removal, "remove_keywords": ["Flying", "First Strike"]}]},
            {"target_schema": {key: value for key, value in schema.items() if key != "keywords_all"}},
            {"target_schema": {**schema, "count": 2}},
        ):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def resolve_damage_witness(self, game, target):
        """Read the target at the real damage occurrence before later effects."""
        observed = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **kwargs):
            if event == "damage.dealt" and context.get("target_object_id") == target.object_id:
                current = game.state.cards[target.object_id]
                observed.append((context, frozenset(game.engine._combat_keywords(current))))
            return dispatch(event, context, **kwargs)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        return observed

    def test_actual_vertigo_prevention_leaves_flying_loss_until_cleanup(self):
        game = self.session(76155210)
        spell = self.add(game.engine, "Vertigo", zone="hand")
        flyer = self.add(game.engine, "Air Elemental", seat="B")
        ground = self.add(game.engine, "Grizzly Bears", seat="C")
        prevent = self.add(game.engine, "Mending Hands", seat="B", zone="hand")
        private = self.add(game.engine, "Canopy Claws", seat="D", zone="hand", ref="vertigo-private")
        for card in (spell, prevent):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        command = {"action_id": action["id"], "pay": "auto", "targets": [flyer.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [ground.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(game, "B", prevent, [flyer.ref])
        self.resolve_top(game)
        self.assertEqual([], self.resolve_damage_witness(game, flyer))
        current = game.state.cards[flyer.object_id]
        self.assertEqual(0, current.marked_damage)
        self.assertNotIn("flying", game.engine._combat_keywords(current))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)
        self.until(game, lambda s: "flying" in s.engine._combat_keywords(s.state.cards[flyer.object_id]))
        self.assertEqual(0, game.state.cards[flyer.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_vertigo_fizzles_when_the_announced_creature_loses_flying(self):
        game = self.session(76155211)
        spell = self.add(game.engine, "Vertigo", zone="hand")
        flyer = self.add(game.engine, "Air Elemental", seat="B")
        remove = self.add(game.engine, "Canopy Claws", seat="B", zone="hand")
        for card in (spell, remove):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        game.state.players["B"].mana_pool["G"] = 1
        self.checkpoint(game)
        self.cast(game, "A", spell, [flyer.ref])
        self.cast(game, "B", remove, [flyer.ref])
        self.resolve_top(game)
        current = game.state.cards[flyer.object_id]
        self.assertNotIn("flying", game.engine._combat_keywords(current))
        resolving = [effect.to_dict() for effect in game.state.continuous_effects]
        self.assertEqual([], self.resolve_damage_witness(game, flyer))
        self.assertEqual(0, game.state.cards[flyer.object_id].marked_damage)
        self.assertEqual(resolving, [effect.to_dict() for effect in game.state.continuous_effects])
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_efreet_deals_damage_with_last_known_source_before_removing_flying(self):
        game = self.session(76155212)
        source = self.add(game.engine, "Burning Palm Efreet")
        flyer = self.add(game.engine, "Air Elemental", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        original = source.logical_object_id
        self.prepare(game, mana={"C": 1, "R": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        offered = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": offered["id"], "pay": "auto", "targets": [flyer.ref]}
        activated = game.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        event, = self.resolve_damage_witness(game, flyer)
        fact, keywords = event
        self.assertEqual((2, original, "A"), (fact["amount"], fact["source_logical_object_id"], fact["source_controller"]))
        self.assertIn("flying", keywords)
        current = game.state.cards[flyer.object_id]
        self.assertEqual(2, current.marked_damage)
        self.assertNotIn("flying", game.engine._combat_keywords(current))
        self.replay(game, load=True)

    def test_actual_soul_sear_removes_indestructible_after_damage_before_lethal_check(self):
        game = self.session(76155213)
        spell = self.add(game.engine, "Soul Sear", zone="hand")
        body = self.add(game.engine, "Darksteel Myr", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.assertIn("indestructible", game.engine._combat_keywords(body))
        self.cast(game, "A", spell, [body.ref])
        event, = self.resolve_damage_witness(game, body)
        fact, keywords = event
        self.assertEqual(5, fact["amount"])
        self.assertIn("indestructible", keywords)
        self.assertEqual("graveyard", game.state.cards[body.object_id].zone)
        self.replay(game, load=True)

    def test_actual_rebel_salvo_pays_equipment_affinity_and_targets_a_planeswalker(self):
        game = self.session(76155214)
        spell = self.add(game.engine, "Rebel Salvo", zone="hand")
        walker = self.add(game.engine, "Ajani Vengeant", seat="B")
        self.add(game.engine, "Bonesplitter", ref="first-affinity-equipment")
        self.add(game.engine, "Bonesplitter", ref="second-affinity-equipment")
        walker.counters.update({"loyalty": 8, "indestructible": 1})
        for card in (spell, walker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.assertIn("indestructible", game.engine._combat_keywords(walker))
        self.cast(game, "A", spell, [walker.ref])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        event, = self.resolve_damage_witness(game, walker)
        fact, keywords = event
        self.assertEqual(5, fact["amount"])
        self.assertIn("indestructible", keywords)
        current = game.state.cards[walker.object_id]
        self.assertEqual(("battlefield", 3), (current.zone, current.counters["loyalty"]))
        self.assertNotIn("indestructible", game.engine._combat_keywords(current))
        self.replay(game, load=True)

    def test_omitting_the_ordered_owner_blocks_plain_pair_and_keeps_death_exile(self):
        owner = "quorune.rules.temporary_keyword_removal.damage_then_fixed_keyword_removal_capabilities"
        with patch(owner, return_value=()):
            binding, _ = register_card(self.db, self.registry, "Rebel Salvo")
            existing, _ = register_card(self.db, self.registry, "Smite the Deathless")
        self.assertFalse(binding["strict_capability_ready"], binding)
        self.assertTrue(existing["strict_capability_ready"], existing["blockers"])

    def test_verified_withdrawals_require_damage_and_temporary_removal_for_every_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (DAMAGE, CAPABILITY):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Ordered damage and removal requirement withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    binding, _ = register_card(self.db, registry, name)
                    self.assertFalse(binding["strict_capability_ready"], (name, binding["blockers"]))
                control, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])

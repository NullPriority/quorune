"""Default continuous-component registry assembly; handlers keep their own owners."""
from functools import lru_cache
from ..rules.capabilities import load_default_capability_registry
from ..counter_snapshot import COUNTER_TOTAL_CAPABILITY
from .continuous_components import (ContinuousEffectComponentRegistry, FixedPowerToughnessAnthemHandler,
    FixedQueryPowerToughnessAnthemHandler, AddBasicLandTypeHandler, FixedQueryAbilityGrantHandler,
    FixedQueryKeywordGrantHandler, FixedQueryCharacteristicGrantHandler, FixedQueryProtectionGrantHandler,
    FixedQueryCharacteristicProtectionGrantHandler, LevelerBandsHandler)

@lru_cache(maxsize=1)
def default_continuous_effect_component_registry(
) -> ContinuousEffectComponentRegistry:
    from .station_symbols import StationSymbolsHandler
    from .commander_keyword_grants import CommanderKeywordGrantHandler
    from .cipher import CipherEncodingGrantHandler
    from .conditional_flash import ConditionalFlashKeywordHandler
    from .negative_keyword_grants import DisjointNegativeKeywordGrantHandler
    from .enchanted_player_characteristics import EnchantedPlayerCharacteristicsHandler
    from .defender_declarations import DefenderAttachmentPermissionHandler, DefenderDeclarationPermissionHandler
    from .conditional_color_grants import ConditionalColorGrantHandler
    from .keyword_prohibitions import OpponentKeywordProhibitionHandler
    from .basic_type_addition import ScopedBasicTypeAdditionHandler
    from .cross_zone_type_additions import (
        ControlledChosenCreatureSubtypeHandler, CrossZoneChosenCreatureTypeHandler,
        CrossZoneTypeAdditionHandler, SelfChosenCreatureSubtypeHandler,
    )
    from .basic_type_setting import FixedBasicTypeSettingHandler
    from .chosen_characteristics import ChosenCharacteristicsHandler, LinkedColorProtectionHandler, LinkedPlayerProtectionHandler
    from .conditional_chosen_characteristics import ConditionalChosenCharacteristicsHandler
    from .attached_continuous import AttachedFixedCharacteristicsHandler
    from .qualified_attached_settings import QualifiedAttachedCharacteristicsHandler
    from .chosen_aura_protection import SourceChosenAuraProtectionHandler
    from .attached_commander_protection import AttachedCommanderProtectionHandler
    from .player_outcomes import MatchOutcomeRestrictionHandler
    from .soulbond import SoulbondPairedGrantHandler
    from .linked_exile_characteristics import LinkedExileCharacteristicsHandler
    from .linked_exile_activations import LinkedExileActivatedGrantHandler
    from .fixed_characteristic_settings import FixedCharacteristicSettingsHandler, ConditionalCharacteristicSettingsHandler, ConditionalCreatureTypeRemovalHandler
    from .fixed_characteristic_settings import (
        AuraCountCharacteristicSettingsHandler, ControlledAuraRecipientSettingsHandler,
        FixedOpponentBaseToughnessHandler, NonownerCreatureModifierHandler,
    )
    from .attached_control import AttachedControlHandler
    from .attached_combat_strike import EquippedCombatStrikeHandler
    from .equipped_planeswalker import EquippedPlaneswalkerHandler
    from .combat_metadata import CombatCounterpartLifelinkHandler
    from .battlefield_cascade_grants import ConditionalStackCascadeHandler
    from .counter_placement_keyword_grants import CounterPlacementKeywordGrantHandler
    from .quantified_anthems import QuantifiedAnthemHandler
    from .recipient_anthems import RecipientAnthemHandler
    from .shared_type_anthems import SharedNameAnthemHandler, SharedTypeAnthemHandler
    from .graveyard_grants import GraveyardQueryAbilityGrantHandler, GraveyardConditionalAbilityGrantHandler
    from .graveyard_unearth_grants import GraveyardUnearthGrantHandler
    from .hand_ninjutsu_grants import HandNinjutsuGrantHandler
    from .composite_grants import CompositeAbilityGrantHandler, FixedTypeReplacementGrantHandler
    from .subtype_composite_grants import FixedSubtypeCompositeGrantHandler
    from .conditional_continuous import (
        FixedPublicStateCharacteristicsHandler,
        FixedPublicStateGrantedAbilityHandler,
        ConditionalDefenderPermissionHandler,
        ConditionalAsUnblockedAssignmentHandler,
        ConditionalBlockCapacityHandler,
        ConditionalDeclarationGrantHandler,
        GraveyardStaticCharacteristicsHandler,
        ConditionalTypedKeywordHandler,
    )

    registry = ContinuousEffectComponentRegistry(
        (
            OpponentKeywordProhibitionHandler(),
            ChosenCharacteristicsHandler(),
            LinkedColorProtectionHandler(),
            LinkedExileCharacteristicsHandler(),
            LinkedExileActivatedGrantHandler(),
            LinkedPlayerProtectionHandler(),
            ConditionalChosenCharacteristicsHandler(),
            FixedPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id="continuous.anthem.source-counter-query.v3", schema_version=3,
            ),
            AddBasicLandTypeHandler(),
            ScopedBasicTypeAdditionHandler(),
            CrossZoneTypeAdditionHandler(),
            CrossZoneChosenCreatureTypeHandler(),
            ControlledChosenCreatureSubtypeHandler(),
            SelfChosenCreatureSubtypeHandler(),
            FixedBasicTypeSettingHandler(),
            FixedQueryAbilityGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            CombatCounterpartLifelinkHandler(),
            EquippedCombatStrikeHandler(),
            EquippedPlaneswalkerHandler(),
            CounterPlacementKeywordGrantHandler(),
            CommanderKeywordGrantHandler(),
            FixedQueryCharacteristicGrantHandler(),
            FixedQueryProtectionGrantHandler(),
            FixedQueryCharacteristicProtectionGrantHandler(),
            LevelerBandsHandler(),
            StationSymbolsHandler(),
            CipherEncodingGrantHandler(),
            FixedPublicStateCharacteristicsHandler(),
            FixedPublicStateGrantedAbilityHandler(),
            ConditionalColorGrantHandler(),
            ConditionalFlashKeywordHandler(),
            ConditionalStackCascadeHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id='continuous.anthem.source-counter-total-query.v4', schema_version=4,
                capability_dependencies=('continuous.power_toughness.fixed_anthem', COUNTER_TOTAL_CAPABILITY)),
            ConditionalDefenderPermissionHandler(),
            DefenderAttachmentPermissionHandler(),
            DefenderDeclarationPermissionHandler(),
            ConditionalAsUnblockedAssignmentHandler(),
            ConditionalBlockCapacityHandler(),
            ConditionalDeclarationGrantHandler(),
            GraveyardStaticCharacteristicsHandler(),
            GraveyardQueryAbilityGrantHandler(),
            GraveyardUnearthGrantHandler(),
            HandNinjutsuGrantHandler(),
            CompositeAbilityGrantHandler(),
            FixedTypeReplacementGrantHandler(),
            FixedSubtypeCompositeGrantHandler(),
            GraveyardConditionalAbilityGrantHandler(),
            ConditionalTypedKeywordHandler(),
            AttachedFixedCharacteristicsHandler(),
            QualifiedAttachedCharacteristicsHandler(),
            SourceChosenAuraProtectionHandler(),
            AttachedCommanderProtectionHandler(),
            MatchOutcomeRestrictionHandler(),
            SoulbondPairedGrantHandler(),
            FixedCharacteristicSettingsHandler(),
            FixedOpponentBaseToughnessHandler(),
            NonownerCreatureModifierHandler(),
            EnchantedPlayerCharacteristicsHandler(),
            DisjointNegativeKeywordGrantHandler(),
            ConditionalCharacteristicSettingsHandler(),
            AuraCountCharacteristicSettingsHandler(),
            ControlledAuraRecipientSettingsHandler(),
            ConditionalCreatureTypeRemovalHandler(),
            AttachedControlHandler(),
            QuantifiedAnthemHandler(),
            RecipientAnthemHandler(),
            SharedTypeAnthemHandler(),
            SharedNameAnthemHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def off_battlefield_activation_grant_effects(host, card, *, maximum_layer=None):
    """Collect registered battlefield activation grants for a card's current zone."""
    from ..characteristic_read_frames import memoized_characteristic_read
    from ..continuous_effect_model import Layer
    from .graveyard_unearth_grants import GRAVEYARD_UNEARTH_GRANT_HANDLER
    from .hand_ninjutsu_grants import HAND_NINJUTSU_GRANT_HANDLER

    if card.is_token or (maximum_layer is not None and maximum_layer < Layer.ABILITY):
        return ()
    handler_id = {
        "graveyard": GRAVEYARD_UNEARTH_GRANT_HANDLER,
        "hand": HAND_NINJUTSU_GRANT_HANDLER,
    }.get(card.zone)
    if handler_id is None:
        return ()

    def collect():
        if not any(descriptor.get("handler_id") == handler_id
                   for program in host.semantics.programs_for_event("characteristics.evaluate")
                   for descriptor in program.handlers):
            return ()
        from ..card_programs.runtime import collect_card_program_continuous_effects

        keys = host._effective_static_component_key_map()
        return collect_card_program_continuous_effects(
            host.state, host.semantics, host.semantic_program_is_current_trusted,
            maximum_layer=Layer.ABILITY, static_component_resolver=lambda source: keys.get(source.object_id, ()),
            early_static_component_resolver=host._static_component_keys_at_layer,
            copied_static_source_resolver=host._copied_static_source_fragments,
            ability_source_participation=host._static_component_presence_at_ability_start,
            handler_ids=frozenset({handler_id}),
        )

    return tuple(effect for effect in memoized_characteristic_read(host, ("off-battlefield-grant", handler_id), collect)
                 if effect.applies.owner == card.owner)

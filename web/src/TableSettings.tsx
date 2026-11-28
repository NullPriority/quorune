import { useState } from "react";
import {
  BATTLEFIELD_LANES,
  CARD_SIZE_ZONES,
  DEFAULT_TABLE_COLORS,
  MAX_CARD_SIZE,
  MAX_HAND_PANEL_HEIGHT,
  MIN_CARD_SIZE,
  MIN_HAND_PANEL_HEIGHT,
  type CardSizeZone,
  type RightRailPanel,
  type TableColors,
  type TablePreferences,
} from "./tablePreferences";

const tableColorPresets = [
  ["Forest", DEFAULT_TABLE_COLORS],
  ["Midnight", { accent: "#a9c6ff", background: "#232b54" }],
  ["Ember", { accent: "#ffd08f", background: "#472626" }],
] as const;

export function CardSizeSlider({ label, value, onChange }: {
  label: string;
  value: number;
  onChange: (next: number) => void;
}) {
  return <label className="card-size-slider">
    <span>{label}</span><output>{Math.round(value * 100)}%</output>
    <input aria-label={label} aria-valuetext={`${Math.round(value * 100)} percent`} type="range"
      min={MIN_CARD_SIZE} max={MAX_CARD_SIZE} step={0.05} value={value}
      onChange={(event) => onChange(Number(event.target.value))} />
  </label>;
}

export function TableSettings({
  value,
  colors,
  onChange,
  onColors,
  onReset,
  opponentSeats = [],
}: {
  value: TablePreferences;
  colors: TableColors;
  onChange: <K extends keyof TablePreferences>(key: K, next: TablePreferences[K]) => void;
  onColors: (next: TableColors) => void;
  onReset: () => void;
  opponentSeats?: readonly Exclude<TablePreferences["opponentView"], "all">[];
}) {
  const firstPanel = value.rightRailOrder[0];
  const [sizeZone, setSizeZone] = useState<CardSizeZone>("battlefield");
  const zoneLabel = CARD_SIZE_ZONES.find(([zone]) => zone === sizeZone)?.[1];
  function setFirstPanel(panel: RightRailPanel) {
    onChange("rightRailOrder", [panel, ...value.rightRailOrder.filter((item) => item !== panel)]);
  }
  return (
    <details className="table-settings" data-testid="table-settings">
      <summary>Table settings</summary>
      <div className="table-settings-grid">
        <div className="compact-table-size">
          <CardSizeSlider label="Table card size" value={value.cardScale} onChange={(next) => onChange("cardScale", next)} />
        </div>
        <fieldset className="battlefield-size-settings">
          <legend>Battlefield lane sizes</legend>
          {BATTLEFIELD_LANES.map(([lane, label]) => <CardSizeSlider key={lane}
            label={label} value={value.battlefieldLaneScale[lane] ?? 1}
            onChange={(next) => onChange("battlefieldLaneScale", { ...value.battlefieldLaneScale, [lane]: next })} />)}
          <small>Larger lanes make space by compacting neighboring lanes.</small>
          <button type="button" className="link-button" onClick={() => onChange("battlefieldLaneScale", {})}>
            Reset lane sizes
          </button>
        </fieldset>
        <label>Hand height <output>{value.handPanelHeight} px</output><input aria-label="Hand panel height"
          aria-valuetext={`${value.handPanelHeight} pixels`} type="range"
          min={MIN_HAND_PANEL_HEIGHT} max={MAX_HAND_PANEL_HEIGHT} step={10} value={value.handPanelHeight}
          onChange={(event) => onChange("handPanelHeight", Number(event.target.value))} /></label>
        <label>Hand width <output>{value.handWidth}%</output><input aria-label="Hand width"
          type="range" min={35} max={76} value={value.handWidth}
          onChange={(event) => onChange("handWidth", Number(event.target.value))} /></label>
        <label className="check-setting"><input type="checkbox" checked={value.handArtFocused}
          onChange={(event) => onChange("handArtFocused", event.target.checked)} /> Art-focused hand</label>
        <label>Right rail width <input aria-label="Right rail width" type="range" min={260} max={520} step={10} value={value.rightRailWidth} onChange={(event) => onChange("rightRailWidth", Number(event.target.value))} /></label>
        <label>Right rail first <select value={firstPanel} onChange={(event) => setFirstPanel(event.target.value as RightRailPanel)}><option value="stack">Stack</option><option value="activity">Activity</option><option value="card">Card viewer</option></select></label>
        <label>Board density <select value={value.boardDensity} onChange={(event) => onChange("boardDensity", event.target.value as TablePreferences["boardDensity"])}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
        <label className="check-setting"><input type="checkbox" checked={value.handAutoCollapse} onChange={(event) => onChange("handAutoCollapse", event.target.checked)} /> Auto-collapse empty hand</label>
        <label className="check-setting"><input type="checkbox" checked={value.inspectorCollapsed} onChange={(event) => onChange("inspectorCollapsed", event.target.checked)} /> Collapse card viewer</label>
        <label className="check-setting"><input type="checkbox" checked={value.compactPhaseRail} onChange={(event) => onChange("compactPhaseRail", event.target.checked)} /> Compact phase rail</label>
        <label className="check-setting"><input type="checkbox" checked={value.activityVisible} onChange={(event) => onChange("activityVisible", event.target.checked)} /> Show recent activity</label>
        <button type="button" className="secondary-button" data-testid="reset-table-settings" onClick={onReset}>Reset table layout</button>
      </div>
      {opponentSeats.length > 1 && <details className="zone-size-settings">
        <summary>Opponent board widths</summary>
        <div className="table-settings-grid">{opponentSeats.map((seat) => <label key={seat}>
          Seat {seat} width <output>{value.opponentWidths[seat] ?? 100}%</output>
          <input type="range" aria-label={`Seat ${seat} board width`} min={50} max={200} step={5}
            value={value.opponentWidths[seat] ?? 100}
            onChange={(event) => onChange("opponentWidths", { ...value.opponentWidths, [seat]: Number(event.target.value) })} />
        </label>)}</div>
      </details>}
      <details className="zone-size-settings">
        <summary>Table colors</summary>
        <div className="table-color-presets" role="group" aria-label="Table color presets">
          {tableColorPresets.map(([name, preset]) => <button key={name} type="button" className="secondary-button"
            aria-pressed={colors.accent === preset.accent && colors.background === preset.background}
            onClick={() => onColors({ ...preset })}>
            <span className="theme-swatch" aria-hidden="true" style={{ background: preset.accent }} />{name}
          </button>)}
        </div>
        <div className="table-settings-grid table-custom-colors">
          <label>Accent <input type="color" value={colors.accent}
            onChange={(event) => onColors({ ...colors, accent: event.target.value })} /></label>
          <label>Background tint <input type="color" value={colors.background}
            onChange={(event) => onColors({ ...colors, background: event.target.value })} /></label>
        </div>
      </details>
      <details className="zone-size-settings">
        <summary>Card sizes by zone</summary>
        <div className="table-settings-grid">
          <label>Zone to resize
            <select aria-label="Zone to resize" value={sizeZone} onChange={(event) => setSizeZone(event.target.value as CardSizeZone)}>
              {CARD_SIZE_ZONES.map(([zone, label]) => <option key={zone} value={zone}>{label}</option>)}
            </select>
          </label>
          <CardSizeSlider label={`${zoneLabel} card size`} value={value.zoneCardScale[sizeZone] ?? 1}
            onChange={(next) => onChange("zoneCardScale", { ...value.zoneCardScale, [sizeZone]: next })} />
          <button type="button" className="secondary-button" onClick={() => onChange("zoneCardScale", {})}>Reset zone sizes</button>
        </div>
        <small>Zone sizes are relative to the table card size.</small>
      </details>
    </details>
  );
}

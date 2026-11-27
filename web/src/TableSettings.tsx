import { useState } from "react";
import {
  CARD_SIZE_ZONES,
  MAX_CARD_SIZE,
  MIN_CARD_SIZE,
  type CardSizeZone,
  type RightRailPanel,
  type TablePreferences,
} from "./tablePreferences";

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
  onChange,
  onReset,
}: {
  value: TablePreferences;
  onChange: <K extends keyof TablePreferences>(key: K, next: TablePreferences[K]) => void;
  onReset: () => void;
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
        <label>Hand height <input aria-label="Hand panel height" type="range" min={190} max={650} step={10} value={value.handPanelHeight} onChange={(event) => onChange("handPanelHeight", Number(event.target.value))} /></label>
        <label>Right rail width <input aria-label="Right rail width" type="range" min={260} max={520} step={10} value={value.rightRailWidth} onChange={(event) => onChange("rightRailWidth", Number(event.target.value))} /></label>
        <label>Right rail first <select value={firstPanel} onChange={(event) => setFirstPanel(event.target.value as RightRailPanel)}><option value="stack">Stack</option><option value="activity">Activity</option><option value="card">Card viewer</option></select></label>
        <label>Board density <select value={value.boardDensity} onChange={(event) => onChange("boardDensity", event.target.value as TablePreferences["boardDensity"])}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
        <label className="check-setting"><input type="checkbox" checked={value.handAutoCollapse} onChange={(event) => onChange("handAutoCollapse", event.target.checked)} /> Auto-collapse empty hand</label>
        <label className="check-setting"><input type="checkbox" checked={value.inspectorCollapsed} onChange={(event) => onChange("inspectorCollapsed", event.target.checked)} /> Collapse card viewer</label>
        <label className="check-setting"><input type="checkbox" checked={value.compactPhaseRail} onChange={(event) => onChange("compactPhaseRail", event.target.checked)} /> Compact phase rail</label>
        <label className="check-setting"><input type="checkbox" checked={value.holdNewStack} onChange={(event) => onChange("holdNewStack", event.target.checked)} /> Hold Pass turn for new spells and abilities</label>
        <label className="check-setting"><input type="checkbox" checked={value.holdGraveyard} onChange={(event) => onChange("holdGraveyard", event.target.checked)} /> Hold Pass turn for graveyard activity</label>
        <label className="check-setting"><input type="checkbox" checked={value.activityVisible} onChange={(event) => onChange("activityVisible", event.target.checked)} /> Show recent activity</label>
        <button type="button" className="secondary-button" data-testid="reset-table-settings" onClick={onReset}>Reset table layout</button>
      </div>
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

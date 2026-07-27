import { t } from "@/i18n";

export function DateRangeFilter({
  min,
  max,
  from,
  to,
  onChange,
}: {
  min: string;
  max: string;
  from: string;
  to: string;
  onChange: (range: { from: string; to: string }) => void;
}) {
  const clamp = (value: string) => (value < min ? min : value > max ? max : value);

  return (
    <div className="date-range-filter">
      <label>
        {t("filter.from")}
        <input
          type="date"
          min={min}
          max={to}
          value={from}
          onChange={(e) => onChange({ from: clamp(e.target.value), to })}
        />
      </label>
      <label>
        {t("filter.to")}
        <input
          type="date"
          min={from}
          max={max}
          value={to}
          onChange={(e) => onChange({ from, to: clamp(e.target.value) })}
        />
      </label>
      {(from !== min || to !== max) && (
        <button type="button" onClick={() => onChange({ from: min, to: max })}>
          {t("filter.reset")}
        </button>
      )}
    </div>
  );
}

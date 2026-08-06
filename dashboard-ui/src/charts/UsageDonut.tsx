import { useState } from "react";
import { PieChart } from "@/components/charts/pie-chart";
import { PieSlice } from "@/components/charts/pie-slice";
import { PieCenter } from "@/components/charts/pie-center";
import { TooltipContent } from "@/components/charts/tooltip/tooltip-content";
import {
  Legend,
  LegendItem,
  LegendLabel,
  LegendMarker,
  LegendValue,
} from "@/components/charts/legend";
import { CATEGORY_COLORS } from "./names";
import { t } from "@/i18n";

export type UsageDatum = { name: string; count: number };

// Matches OTHER_LABEL in src/tomax/render/_counters.py — a
// server-synthesized bucket, not a real skill/MCP name, so it's translated.
const OTHER_LABEL = "Other";

const SIZE = 220;

export function UsageDonut({ data, inView }: { data: UsageDatum[]; inView: boolean }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null);

  const active = data.filter((d) => d.count > 0);
  const total = active.reduce((sum, d) => sum + d.count, 0);
  if (total <= 0) return <div className="empty">{t("state.noData")}</div>;

  const slices = active.map((d, i) => ({
    label: d.name === OTHER_LABEL ? t("bucket.other") : d.name,
    value: d.count,
    maxValue: total,
    color: CATEGORY_COLORS[i % CATEGORY_COLORS.length],
  }));

  const hovered = hoveredIndex !== null ? slices[hoveredIndex] : null;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        gap: 32,
        alignItems: "center",
        justifyContent: "center",
        flexWrap: "wrap",
      }}
    >
      <div
        style={{ position: "relative" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setHoverPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
        }}
      >
        {inView ? (
          <PieChart
            data={slices}
            size={SIZE}
            innerRadius={64}
            padAngle={0.02}
            hoveredIndex={hoveredIndex}
            onHoverChange={setHoveredIndex}
          >
            {slices.map((_, i) => (
              <PieSlice key={i} index={i} />
            ))}
            <PieCenter defaultLabel={t("center.total")} />
          </PieChart>
        ) : (
          <div style={{ width: SIZE, height: SIZE }} />
        )}
        {hovered && hoverPos && (
          <div
            className="pointer-events-none absolute z-50"
            style={{
              left: hoverPos.x,
              top: hoverPos.y,
              transform: "translate(-50%, -100%)",
              marginTop: -12,
            }}
          >
            <div className="min-w-[140px] overflow-hidden rounded-lg bg-chart-tooltip-background shadow-lg">
              <TooltipContent
                rows={[
                  {
                    label: hovered.label,
                    color: hovered.color,
                    value: `${hovered.value} (${Math.round((hovered.value / total) * 100)}%)`,
                  },
                ]}
              />
            </div>
          </div>
        )}
      </div>
      <Legend
        items={slices}
        hoveredIndex={hoveredIndex}
        onHoverChange={setHoveredIndex}
        className="grid grid-cols-2 gap-x-4 gap-y-1"
      >
        <LegendItem className="flex items-center gap-2">
          <LegendMarker />
          <LegendLabel className="flex-1 truncate" />
          <LegendValue showPercentage />
        </LegendItem>
      </Legend>
    </div>
  );
}

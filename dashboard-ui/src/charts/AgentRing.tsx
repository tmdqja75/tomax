import { useState } from "react";
import { RingChart } from "@/components/charts/ring-chart";
import { Ring } from "@/components/charts/ring";
import { RingCenter } from "@/components/charts/ring-center";
import {
  Legend,
  LegendItem,
  LegendLabel,
  LegendMarker,
  LegendProgress,
  LegendValue,
} from "@/components/charts/legend";
import { agentColor, agentLabel, CATEGORY_COLORS } from "./names";
import { getLang, t } from "@/i18n";

export type AgentDatum = { agent: string; tokens: number };

const SIZE = 280;

export function AgentRing({ data, inView }: { data: AgentDatum[]; inView: boolean }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const active = data.filter((d) => d.tokens > 0);
  const total = active.reduce((sum, d) => sum + d.tokens, 0);
  if (total <= 0) return <div className="empty">{t("state.noAgentActivity")}</div>;

  const items = active.map((d, i) => ({
    label: agentLabel(d.agent),
    value: d.tokens,
    maxValue: total,
    color: agentColor(d.agent, CATEGORY_COLORS[i % CATEGORY_COLORS.length]),
  }));

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
      {inView ? (
        <RingChart
          data={items}
          size={SIZE}
          strokeWidth={20}
          hoveredIndex={hoveredIndex}
          onHoverChange={setHoveredIndex}
        >
          {items.map((_, i) => (
            <Ring key={i} index={i} />
          ))}
          <RingCenter
            defaultLabel={t("center.totalTokens")}
            formatOptions={{
              notation: "compact",
              maximumFractionDigits: getLang() === "ko" ? 0 : 1,
            }}
          />
        </RingChart>
      ) : (
        <div style={{ width: SIZE, height: SIZE }} />
      )}
      <Legend items={items} hoveredIndex={hoveredIndex} onHoverChange={setHoveredIndex}>
        <LegendItem>
          <LegendMarker />
          <LegendLabel />
          <LegendValue showPercentage />
          <LegendProgress />
        </LegendItem>
      </Legend>
    </div>
  );
}

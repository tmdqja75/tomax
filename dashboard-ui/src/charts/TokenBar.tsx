import { BarChart } from "@/components/charts/bar-chart";
import { Bar } from "@/components/charts/bar";
import { BarXAxis } from "@/components/charts/bar-x-axis";
import { Grid } from "@/components/charts/grid";
import { YAxis } from "@/components/charts/y-axis";
import { ChartTooltip } from "@/components/charts/tooltip";
import { SERIES_COLORS } from "./names";
import { t } from "@/i18n";
import type { TokenPoint } from "./TokenArea";

export function TokenBar({ data, inView }: { data: TokenPoint[]; inView: boolean }) {
  if (data.length === 0) return <div className="empty">{t("state.noTokenActivity")}</div>;

  return (
    <>
      <BarChart
        data={data}
        xDataKey="date"
        aspectRatio="3 / 1"
        margin={{ left: 54 }}
        stacked
        stackGap={3}
        status={inView ? "ready" : "loading"}
      >
        <Grid horizontal />
        <Bar dataKey="input" fill={SERIES_COLORS.input} lineCap="butt" />
        <Bar dataKey="output" fill={SERIES_COLORS.output} lineCap="butt" />
        <Bar dataKey="reasoning" fill={SERIES_COLORS.reasoning} lineCap="butt" />
        <Bar dataKey="cache" fill={SERIES_COLORS.cache} lineCap="butt" />
        <YAxis formatLargeNumbers />
        <BarXAxis />
        <ChartTooltip
          showDatePill
          rows={(p) => [
            { label: t("legend.input"), value: p.input as number, color: SERIES_COLORS.input },
            { label: t("legend.output"), value: p.output as number, color: SERIES_COLORS.output },
            {
              label: t("legend.reasoning"),
              value: p.reasoning as number,
              color: SERIES_COLORS.reasoning,
            },
            { label: t("legend.cache"), value: p.cache as number, color: SERIES_COLORS.cache },
          ]}
        />
      </BarChart>
      <div className="legend">
        {(["input", "output", "reasoning", "cache"] as const).map((k) => (
          <span className="item" key={k}>
            <span className="swatch" style={{ background: SERIES_COLORS[k] }} />
            {t(`legend.${k}`)}
          </span>
        ))}
      </div>
    </>
  );
}

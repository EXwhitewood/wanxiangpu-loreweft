import type { ComponentType } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import type { EChartsReactProps } from "echarts-for-react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, RadarChart } from "echarts/charts";
import { GridComponent, RadarComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  RadarChart,
  GridComponent,
  RadarComponent,
  TooltipComponent,
  CanvasRenderer,
]);

// `echarts-for-react/lib/core` is CommonJS and can surface as either the
// component itself or `{ default: Component }` in WebView2. Normalize that
// module boundary inside the lazy chart chunk so the route shell stays light.
const EChartsCoreComponent = (
  (ReactEChartsCore as unknown as { default?: ComponentType<EChartsReactProps> }).default
  ?? ReactEChartsCore
) as ComponentType<EChartsReactProps>;

export default function IntelligenceChart(props: Omit<EChartsReactProps, "echarts">) {
  return <EChartsCoreComponent echarts={echarts} {...props} />;
}

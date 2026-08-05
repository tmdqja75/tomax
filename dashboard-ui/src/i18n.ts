export type Lang = "en" | "ko";

declare global {
  interface Window {
    __LANG__?: string;
  }
}

export function getLang(): Lang {
  return typeof window !== "undefined" && window.__LANG__ === "ko" ? "ko" : "en";
}

export function getLocale(): string {
  return getLang() === "ko" ? "ko-KR" : "en-US";
}

const translations: Record<Lang, Record<string, string>> = {
  en: {
    "title.tokenUsage": "Total Token Usage",
    "title.usageByAgent": "Usage by Agent",
    "title.modelUsage": "Model Usage",
    "title.skillUsage": "Skill Usage",
    "title.mcpUsage": "MCP Usage",
    "title.activity": "Activity",
    "legend.input": "Input",
    "legend.output": "Output",
    "legend.reasoning": "Reasoning",
    "legend.cache": "Cache",
    "center.totalTokens": "Total tokens",
    "center.total": "Total",
    "heatmap.less": "Less",
    "heatmap.more": "More",
    "heatmap.tokens": "Tokens",
    "state.loading": "Loading…",
    "state.error": "Failed to load data:",
    "state.noData": "No data yet.",
    "state.noTokenActivity": "No token activity in this window.",
    "state.noAgentActivity": "No agent activity yet.",
    "state.noActivity": "No activity recorded yet.",
    "bucket.other": "Other",
    "filter.from": "From",
    "filter.to": "To",
    "filter.reset": "Reset",
  },
  ko: {
    "title.tokenUsage": "총 토큰 사용량",
    "title.usageByAgent": "에이전트별 사용량",
    "title.modelUsage": "모델 사용량",
    "title.skillUsage": "스킬 사용량",
    "title.mcpUsage": "MCP 사용량",
    "title.activity": "활동",
    "legend.input": "입력",
    "legend.output": "출력",
    "legend.reasoning": "추론",
    "legend.cache": "캐시",
    "center.totalTokens": "총 토큰",
    "center.total": "총계",
    "heatmap.less": "적음",
    "heatmap.more": "많음",
    "heatmap.tokens": "토큰",
    "state.loading": "불러오는 중…",
    "state.error": "데이터를 불러오지 못했습니다:",
    "state.noData": "아직 데이터가 없습니다.",
    "state.noTokenActivity": "이 기간에 토큰 활동이 없습니다.",
    "state.noAgentActivity": "아직 에이전트 활동이 없습니다.",
    "state.noActivity": "아직 기록된 활동이 없습니다.",
    "bucket.other": "기타",
    "filter.from": "시작일",
    "filter.to": "종료일",
    "filter.reset": "초기화",
  },
};

export function t(key: string): string {
  return translations[getLang()][key] ?? key;
}

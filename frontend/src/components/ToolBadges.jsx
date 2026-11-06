import React from 'react';
import {
  AnalyticsIcon,
  ChartIcon,
  FileIcon,
  GlobeIcon,
  LinkIcon,
  SearchIcon,
  StoryIcon,
  ThinkingIcon,
} from './AppIcons.jsx';
import { formatStreamingToolLabel } from '../utils/streaming.js';

function detailTitle(detail, tool) {
  const summary = detail && detail.result_summary ? detail.result_summary : {};
  const bits = [formatStreamingToolLabel(tool)];
  const resultStatus = String(summary.result_status || '').trim().toLowerCase();
  if (summary.total_count !== undefined && summary.total_count !== null && summary.total_count !== '' && summary.total_count !== 'N/A') {
    bits.push(`${summary.total_count} resultados`);
  }
  if (Number(summary.items_returned || 0) > 0) {
    bits.push(`${summary.items_returned} itens`);
  }
  if (resultStatus === 'blocked') {
    bits.push('bloqueada');
  } else if (['tool_error', 'exception'].includes(resultStatus) || summary.error_kind) {
    bits.push('erro');
  } else if (summary.has_error) {
    bits.push('com alertas');
  }
  return bits.join(' · ');
}

export default function ToolBadges({ tools, details }) {
  if (!tools || tools.length === 0) return null;
  const icons = {
    query_workitems: SearchIcon,
    search_workitems: ThinkingIcon,
    search_website: GlobeIcon,
    analyze_patterns: AnalyticsIcon,
    generate_user_stories: StoryIcon,
    query_hierarchy: LinkIcon,
    compute_kpi: AnalyticsIcon,
    generate_chart: ChartIcon,
    generate_file: FileIcon,
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
      {tools.map((tool, idx) => (
        <span
          key={`${tool}-${idx}`}
          className="tool-badge"
          title={details && details[idx] ? detailTitle(details[idx], tool) : formatStreamingToolLabel(tool)}
        >
          {React.createElement(icons[tool] || FileIcon, { size: 14 })}
          <span>{formatStreamingToolLabel(tool)}</span>
        </span>
      ))}
    </div>
  );
}

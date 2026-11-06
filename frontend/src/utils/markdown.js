import { escapeHtml, sanitizeHtmlOutput, sanitizeLinkUrl } from './sanitize.js';

// ---------------------------------------------------------------------------
// Inline markdown rendering
// ---------------------------------------------------------------------------

function renderInlineMarkdown(rawText) {
  let text = String(rawText || '');

  // 1. Extract markdown links [label](url) → placeholder tokens
  const linkPlaceholders = [];
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    const safeHref = sanitizeLinkUrl(url);
    if (!safeHref) return escapeHtml(label);
    const token = `@@LINK_${linkPlaceholders.length}@@`;
    linkPlaceholders.push(
      `<a href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
    );
    return token;
  });

  // 2. Extract inline code `code` → placeholder tokens (before escaping)
  const codePlaceholders = [];
  text = text.replace(/`([^`]+?)`/g, (_, code) => {
    const token = `@@CODE_${codePlaceholders.length}@@`;
    codePlaceholders.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  // 3. Escape HTML
  let html = escapeHtml(text);

  // 4. Bold **text** and __text__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // 5. Italic *text* and _text_ (but not inside words like file_name)
  html = html.replace(/(?<!\w)\*([^*]+?)\*(?!\w)/g, '<em>$1</em>');
  html = html.replace(/(?<!\w)_([^_]+?)_(?!\w)/g, '<em>$1</em>');

  // 6. Auto-link bare URLs
  html = html.replace(/(^|[\s(])((?:https?:\/\/|\/api\/)[^\s<)]+)/g, (_, prefix, url) => {
    const safeHref = sanitizeLinkUrl(url);
    if (!safeHref) return `${prefix}${url}`;
    return `${prefix}<a href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`;
  });

  // 7. Restore link placeholders
  for (let i = 0; i < linkPlaceholders.length; i++) {
    html = html.replaceAll(escapeHtml(`@@LINK_${i}@@`), linkPlaceholders[i]);
  }

  // 8. Restore code placeholders
  for (let i = 0; i < codePlaceholders.length; i++) {
    html = html.replaceAll(escapeHtml(`@@CODE_${i}@@`), codePlaceholders[i]);
  }

  return html;
}

// ---------------------------------------------------------------------------
// Table detection and rendering
// ---------------------------------------------------------------------------

function isTableSeparatorLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed.includes('-')) return false;
  const core = trimmed.replace(/^\|/, '').replace(/\|$/, '');
  return core
    .split('|')
    .map(cell => cell.trim())
    .every(cell => /^:?-{2,}:?$/.test(cell));
}

function isTableRowLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed || !trimmed.includes('|')) return false;
  return !isTableSeparatorLine(trimmed);
}

function parseTableRow(line) {
  const trimmed = String(line || '').trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map(cell => cell.trim());
}

function renderTableBlock(headerRow, dataRows) {
  const colCount = Math.max(1, headerRow.length);
  const normalizedHeader = [...headerRow];
  while (normalizedHeader.length < colCount) normalizedHeader.push('');

  const headerHtml = normalizedHeader
    .slice(0, colCount)
    .map(cell => `<th>${renderInlineMarkdown(cell)}</th>`)
    .join('');

  const bodyHtml = dataRows
    .map((row) => {
      const normalized = [...row];
      while (normalized.length < colCount) normalized.push('');
      return `<tr>${normalized.slice(0, colCount).map(cell => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`;
    })
    .join('');

  return `<div class="table-wrapper"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Block-level markdown rendering
// ---------------------------------------------------------------------------

function isHeading(line) {
  return /^#{1,6}\s+/.test(line);
}

function renderHeading(line) {
  const match = line.match(/^(#{1,6})\s+(.+)$/);
  if (!match) return null;
  // Map #..###### → h3..h6 to keep hierarchy without conflicting with app chrome.
  const level = match[1].length + 2;
  const tag = `h${Math.min(level, 6)}`;
  return `<${tag}>${renderInlineMarkdown(match[2])}</${tag}>`;
}

function isUnorderedListItem(line) {
  return /^[\s]*[-*]\s+/.test(line);
}

function isOrderedListItem(line) {
  return /^[\s]*\d+\.\s+/.test(line);
}

function getListItemContent(line) {
  return line.replace(/^[\s]*[-*]\s+/, '').replace(/^[\s]*\d+\.\s+/, '');
}

function isCodeFenceLine(line) {
  return /^```/.test(String(line || '').trim());
}

function isHorizontalRule(line) {
  const trimmed = String(line || '').trim();
  return /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);
}

function isBlockquote(line) {
  return /^>\s?/.test(String(line || '').trim());
}

export function renderMarkdown(text) {
  const source = String(text || '').replace(/\r\n?/g, '\n');
  const lines = source.split('\n');
  const segments = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = String(line || '').trim();
    const next = i + 1 < lines.length ? lines[i + 1] : '';

    // --- Code blocks (```) ---
    if (isCodeFenceLine(line)) {
      const lang = trimmed.slice(3).trim();
      const codeLines = [];
      i += 1;
      while (i < lines.length && !isCodeFenceLine(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1; // skip closing ```
      const codeContent = escapeHtml(codeLines.join('\n'));
      segments.push(`<pre><code${lang ? ` class="language-${escapeHtml(lang)}"` : ''}>${codeContent}</code></pre>`);
      continue;
    }

    // --- Tables ---
    if (isTableRowLine(line) && isTableSeparatorLine(next)) {
      const headerRow = parseTableRow(line);
      const dataRows = [];
      i += 2; // skip header + separator
      while (i < lines.length && isTableRowLine(lines[i])) {
        dataRows.push(parseTableRow(lines[i]));
        i += 1;
      }
      segments.push(renderTableBlock(headerRow, dataRows));
      continue;
    }

    // --- Horizontal rule ---
    if (isHorizontalRule(line)) {
      segments.push('<hr>');
      i += 1;
      continue;
    }

    // --- Headings ---
    if (isHeading(trimmed)) {
      const heading = renderHeading(trimmed);
      if (heading) {
        segments.push(heading);
        i += 1;
        continue;
      }
    }

    // --- Blockquotes ---
    if (isBlockquote(trimmed)) {
      const quoteLines = [];
      while (i < lines.length && isBlockquote(String(lines[i] || '').trim())) {
        quoteLines.push(String(lines[i] || '').trim().replace(/^>\s?/, ''));
        i += 1;
      }
      segments.push(`<blockquote>${quoteLines.map(l => renderInlineMarkdown(l)).join('<br>')}</blockquote>`);
      continue;
    }

    // --- Unordered lists ---
    if (isUnorderedListItem(trimmed)) {
      const items = [];
      while (i < lines.length && isUnorderedListItem(String(lines[i] || '').trim())) {
        items.push(getListItemContent(String(lines[i] || '').trim()));
        i += 1;
      }
      segments.push(`<ul>${items.map(item => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ul>`);
      continue;
    }

    // --- Ordered lists ---
    if (isOrderedListItem(trimmed)) {
      const items = [];
      while (i < lines.length && isOrderedListItem(String(lines[i] || '').trim())) {
        items.push(getListItemContent(String(lines[i] || '').trim()));
        i += 1;
      }
      segments.push(`<ol>${items.map(item => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ol>`);
      continue;
    }

    // --- Empty lines ---
    if (!trimmed) {
      i += 1;
      continue;
    }

    // --- Regular text (paragraph) ---
    const paraLines = [];
    while (
      i < lines.length &&
      String(lines[i] || '').trim() &&
      !isHeading(String(lines[i] || '').trim()) &&
      !isUnorderedListItem(String(lines[i] || '').trim()) &&
      !isOrderedListItem(String(lines[i] || '').trim()) &&
      !isCodeFenceLine(lines[i]) &&
      !isHorizontalRule(lines[i]) &&
      !isBlockquote(String(lines[i] || '').trim()) &&
      !(isTableRowLine(lines[i]) && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1]))
    ) {
      paraLines.push(lines[i]);
      i += 1;
    }
    if (paraLines.length > 0) {
      segments.push(paraLines.map(l => renderInlineMarkdown(l)).join('<br>'));
    }
  }

  return sanitizeHtmlOutput(segments.join('\n'));
}

export function renderInline(text) {
  return sanitizeHtmlOutput(renderInlineMarkdown(text));
}

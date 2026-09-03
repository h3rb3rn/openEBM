/**
 * EBM Text Highlighter
 * Rendert medizinischen Berichtstext mit farbigen GOP-Annotierungen.
 *
 * Unterstützt:
 *   - Überlappende Spans (werden mit Alpha-Blending gehandhabt)
 *   - Tooltip bei Hover (GOP-Code + Beschreibung)
 *   - Klick-Interaktion für Human-in-the-Loop Status-Toggle
 *   - Dynamische Legende
 */

/**
 * Rendert Text mit farbigen Highlights für GOP-Annotierungen.
 *
 * @param {string} text - Der originale Berichtstext
 * @param {Array} gopResults - Array von {gop_code, start_char, end_char, color_bg, color_border, description, status}
 * @returns {string} - HTML-String mit <mark>-Elementen
 */
function highlightText(text, gopResults) {
  if (!text) return '';

  // Nur GOPs mit validen Zeichenoffsets
  const validHighlights = gopResults.filter(g =>
    g.start_char != null &&
    g.end_char != null &&
    g.start_char >= 0 &&
    g.end_char <= text.length &&
    g.start_char < g.end_char
  );

  if (validHighlights.length === 0) {
    return escapeHtml(text);
  }

  // Alle Positionen sammeln, an denen sich Spans beginnen / enden
  // Für überlappende Spans: Priorität nach Reihenfolge im Array (LLM-Konfidenz)
  const events = [];
  validHighlights.forEach((gop, idx) => {
    events.push({ pos: gop.start_char, type: 'open',  gop, idx });
    events.push({ pos: gop.end_char,   type: 'close', gop, idx });
  });

  // Sortieren: nach Position, bei gleicher Position erst "open" vor "close"
  events.sort((a, b) => a.pos - b.pos || (a.type === 'open' ? -1 : 1));

  let result = '';
  let cursor = 0;
  const openStack = [];

  for (const event of events) {
    // Text bis zur aktuellen Position (außerhalb von Highlights)
    if (event.pos > cursor) {
      const plainText = text.slice(cursor, event.pos);
      if (openStack.length === 0) {
        result += escapeHtml(plainText);
      } else {
        // Text innerhalb eines Highlights
        result += escapeHtml(plainText);
      }
    }
    cursor = event.pos;

    if (event.type === 'open') {
      const gop = event.gop;
      const isRejected = gop.status === 'abgelehnt' ? ' rejected' : '';
      const tooltip = buildTooltip(gop);
      result += `<mark class="gop-highlight${isRejected}"
        style="background:${gop.color_bg}; border-bottom-color:${gop.color_border}; padding:2px 0;"
        data-gop-code="${gop.gop_code}"
        title="${tooltip}"
        onclick="gopHighlightClicked('${gop.gop_code}', this)"
      >`;
      openStack.push(gop);
    } else {
      // Close-Event: schließe den korrespondierenden Span
      if (openStack.length > 0) {
        result += '</mark>';
        openStack.pop();
      }
    }
  }

  // Restlicher Text nach dem letzten Highlight
  if (cursor < text.length) {
    result += escapeHtml(text.slice(cursor));
  }

  // Nicht geschlossene Spans schließen (defensive)
  while (openStack.length > 0) {
    result += '</mark>';
    openStack.pop();
  }

  return result;
}

/**
 * Baut Tooltip-Text für ein GOP-Highlight.
 */
function buildTooltip(gop) {
  const parts = [
    `GOP ${gop.gop_code}`,
    gop.description ? gop.description.substring(0, 80) : '',
    gop.confidence ? `Konfidenz: ${Math.round(gop.confidence * 100)}%` : '',
    gop.status ? `Status: ${statusLabel(gop.status)}` : '',
  ].filter(Boolean);
  return parts.join(' | ').replace(/"/g, '&quot;');
}

function statusLabel(status) {
  const labels = {
    vorgeschlagen: 'Vorgeschlagen',
    akzeptiert: 'Akzeptiert',
    abgelehnt: 'Abgelehnt',
  };
  return labels[status] || status;
}

/**
 * Klick-Handler für Highlight-Elemente.
 * Öffnet ein kleines Popup mit GOP-Details und Aktionsbuttons.
 */
function gopHighlightClicked(gopCode, element) {
  // Bestehende Popups entfernen
  document.querySelectorAll('.gop-popup').forEach(p => p.remove());

  if (!window.analysisResult) return;
  const gop = window.analysisResult.gop_results.find(g => g.gop_code === gopCode);
  if (!gop) return;

  const popup = document.createElement('div');
  popup.className = 'gop-popup fixed z-50 bg-white border border-gray-200 rounded-xl shadow-xl p-4 w-72 text-sm';

  const rect = element.getBoundingClientRect();
  popup.style.top = `${Math.min(rect.bottom + window.scrollY + 8, window.innerHeight - 200)}px`;
  popup.style.left = `${Math.max(0, Math.min(rect.left + window.scrollX, window.innerWidth - 300))}px`;

  popup.innerHTML = `
    <div class="flex items-start justify-between mb-2">
      <div>
        <div class="font-mono font-bold text-gray-900">GOP ${gop.gop_code}</div>
        <div class="text-xs text-gray-500 mt-0.5">${gop.description || '–'}</div>
      </div>
      <button onclick="this.closest('.gop-popup').remove()" class="text-gray-400 hover:text-gray-600 ml-2 flex-shrink-0">✕</button>
    </div>
    <div class="space-y-1 text-xs text-gray-600 mb-3">
      <div>Konfidenz: <strong>${Math.round(gop.confidence * 100)}%</strong></div>
      ${gop.reasoning ? `<div>Begründung: <em>${gop.reasoning.substring(0, 100)}</em></div>` : ''}
      <div>Status: <span class="${statusClass(gop.status)} px-1.5 py-0.5 rounded">${statusLabel(gop.status)}</span></div>
    </div>
    ${window.analysisResult.case_file_id ? `
      <div class="flex gap-2 pt-2 border-t border-gray-100">
        <button onclick="acceptGOP('${window.analysisResult.case_file_id}', '${gop.gop_code}')"
          class="flex-1 py-1.5 bg-emerald-600 text-white text-xs rounded-lg hover:bg-emerald-700 transition-colors">
          ✓ Akzeptieren
        </button>
        <button onclick="rejectGOP('${window.analysisResult.case_file_id}', '${gop.gop_code}')"
          class="flex-1 py-1.5 bg-red-600 text-white text-xs rounded-lg hover:bg-red-700 transition-colors">
          ✗ Ablehnen
        </button>
      </div>
    ` : '<div class="text-xs text-sky-600 pt-2 border-t border-gray-100">Instant-Mode: Kein Audit-Log</div>'}
  `;

  document.body.appendChild(popup);

  // Außerhalb klicken → schließen
  setTimeout(() => {
    document.addEventListener('click', function closePopup(e) {
      if (!popup.contains(e.target) && e.target !== element) {
        popup.remove();
        document.removeEventListener('click', closePopup);
      }
    });
  }, 100);
}

function statusClass(status) {
  return {
    vorgeschlagen: 'badge-vorgeschlagen',
    akzeptiert: 'badge-akzeptiert',
    abgelehnt: 'badge-abgelehnt',
  }[status] || '';
}

/**
 * Human-in-the-Loop: GOP akzeptieren.
 */
async function acceptGOP(caseFileId, gopCode) {
  await _updateGOPStatus(caseFileId, gopCode, 'accept');
}

async function rejectGOP(caseFileId, gopCode) {
  const reason = prompt('Ablehnungsgrund (optional):') || null;
  await _updateGOPStatus(caseFileId, gopCode, 'reject', reason);
}

async function _updateGOPStatus(caseFileId, gopCode, action, reason = null) {
  // GOP-ID aus Analyseergebnis heraussuchen
  const gops = window.analysisResult?.gop_results || [];
  const gop = gops.find(g => g.gop_code === gopCode);
  if (!gop || !gop.id) {
    // Ohne ID aus dem Result können wir den API-Aufruf nicht machen
    console.warn('Keine GOP-ID für', gopCode);
    document.querySelectorAll('.gop-popup').forEach(p => p.remove());
    return;
  }

  const url = `/cases/${caseFileId}/gops/${gop.id}/${action}`;
  const resp = await apiCall('POST', url, reason ? { reason } : null);
  if (resp && resp.ok) {
    // Lokales Update
    gop.status = action === 'accept' ? 'akzeptiert' : 'abgelehnt';
    // Highlight aktualisieren
    document.querySelectorAll(`[data-gop-code="${gopCode}"]`).forEach(el => {
      if (action === 'reject') {
        el.classList.add('rejected');
      } else {
        el.classList.remove('rejected');
      }
    });
    // Badge in Legende aktualisieren
    const badge = document.querySelector(`.gop-status-badge-${gopCode}`);
    if (badge) {
      badge.className = `gop-status-badge-${gopCode} text-xs px-1.5 py-0.5 rounded ${statusClass(gop.status)}`;
      badge.textContent = statusLabel(gop.status);
    }
  }
  document.querySelectorAll('.gop-popup').forEach(p => p.remove());
}

/**
 * HTML-Escape für sicheres Rendering.
 */
function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '<br>');
}

// Globales Result-Objekt für Popup-Zugriff
window.analysisResult = null;

// Override renderResults um window.analysisResult zu setzen
const _origRenderResults = window.renderResults;
if (typeof window.renderResults === 'function') {
  window.renderResults = function(result) {
    window.analysisResult = result;
    _origRenderResults(result);
  };
}

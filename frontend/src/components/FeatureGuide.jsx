import React, { useState } from 'react';

/**
 * FeatureGuide — S4-05: Optional feature guide overlay.
 *
 * Shows a dismissible panel with categorized feature descriptions and
 * example prompts. Users who already know the assistant skip this entirely;
 * new users click "Guia de funcionalidades" to discover capabilities.
 *
 * Props:
 *  - onTryPrompt (fn) — callback(text) to send a suggested prompt
 *  - onClose    (fn) — callback to close the guide
 */

const GUIDE_SECTIONS = [
  {
    title: 'User Stories & DevOps',
    icon: '📝',
    features: [
      { name: 'Gerar user stories', desc: 'Descreve o que precisas e o agente gera stories formatadas com ACs.', example: 'Gera 3 user stories sobre pagamento de serviços' },
      { name: 'Publicar no DevOps', desc: 'Após aprovação, publica directamente no Azure DevOps.', example: 'Cria uma US para exportar PDF e publica no DevOps' },
      { name: 'Consultar backlog', desc: 'Pesquisa work items, bugs, epics e features por linguagem natural.', example: 'Quais bugs estão activos no BackOffice?' },
      { name: 'KPIs e métricas', desc: 'Rankings, velocity, distribuições e tendências do sprint.', example: 'Mostra KPIs da equipa no último sprint' },
    ],
  },
  {
    title: 'Análise de dados',
    icon: '📊',
    features: [
      { name: 'Upload e análise', desc: 'Carrega CSV, Excel, PDF ou imagem e faz perguntas sobre o conteúdo.', example: 'Analisa o ficheiro que acabei de carregar' },
      { name: 'Código Python', desc: 'Executa pandas, DuckDB e matplotlib em sandbox segura.', example: 'Calcula a média de vendas por região' },
      { name: 'Gráficos', desc: 'Gera gráficos interactivos (bar, pie, line, scatter) a partir dos dados.', example: 'Gera um gráfico de barras com os totais por mês' },
      { name: 'Cross-file', desc: 'Com 2+ ficheiros, faz JOINs e comparações automáticas via DuckDB.', example: 'Compara os dados dos 2 ficheiros CSV' },
    ],
  },
  {
    title: 'Geração de ficheiros',
    icon: '📄',
    features: [
      { name: 'PowerPoint branded', desc: 'Gera apresentações com branding configurável.', example: 'Gera um PowerPoint com o resumo do sprint' },
      { name: 'Excel avançado', desc: 'Spreadsheets com formatting, freeze panes e filtros.', example: 'Gera um Excel com dashboard dos KPIs' },
      { name: 'Export multi-formato', desc: 'Exporta dados em CSV, XLSX, PDF ou HTML.', example: 'Exporta esta tabela em PDF' },
    ],
  },
  {
    title: 'Pesquisa e conhecimento',
    icon: '🔍',
    features: [
      { name: 'Base de conhecimento', desc: 'Pesquisa documentação interna, site do produto e processos internos.', example: 'Como fazer uma transferência SEPA no portal?' },
      { name: 'Pesquisa web', desc: 'Pesquisa na internet via Brave Search quando o contexto interno não basta.', example: 'Quais são as melhores práticas para acessibilidade WCAG?' },
      { name: 'Figma e Miro', desc: 'Pesquisa designs no Figma e boards no Miro.', example: 'Mostra os ecrãs do fluxo de pagamentos no Figma' },
    ],
  },
  {
    title: 'Produtividade',
    icon: '⚡',
    features: [
      { name: 'Voz', desc: 'Fala em português e o agente transcreve e responde.', example: null },
      { name: 'Email', desc: 'Classifica emails carregados por tema/prioridade e prepara rascunhos Outlook.', example: 'Classifica os emails do ficheiro por prioridade' },
      { name: 'Perfil de escrita', desc: 'O agente aprende o teu estilo e aplica nas próximas user stories.', example: 'Aprende o meu estilo de escrita' },
    ],
  },
];

export default function FeatureGuide({ onTryPrompt, onClose }) {
  const [expandedSection, setExpandedSection] = useState(null);

  return (
    <div className="feature-guide-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="feature-guide-modal">
        <div className="feature-guide-header">
          <h2>Guia de funcionalidades</h2>
          <p>Descobre tudo o que o assistente sabe fazer. Clica num exemplo para experimentar.</p>
          <button className="feature-guide-close" onClick={onClose} aria-label="Fechar guia">&times;</button>
        </div>

        <div className="feature-guide-sections">
          {GUIDE_SECTIONS.map((section, si) => (
            <div key={si} className="feature-guide-section">
              <button
                className={`feature-guide-section-header${expandedSection === si ? ' expanded' : ''}`}
                onClick={() => setExpandedSection(expandedSection === si ? null : si)}
              >
                <span className="feature-guide-section-icon">{section.icon}</span>
                <span className="feature-guide-section-title">{section.title}</span>
                <span className="feature-guide-chevron">{expandedSection === si ? '▾' : '▸'}</span>
              </button>

              {expandedSection === si && (
                <div className="feature-guide-features">
                  {section.features.map((feat, fi) => (
                    <div key={fi} className="feature-guide-feature">
                      <div className="feature-guide-feature-name">{feat.name}</div>
                      <div className="feature-guide-feature-desc">{feat.desc}</div>
                      {feat.example && (
                        <button
                          className="feature-guide-try-btn"
                          onClick={() => { onTryPrompt(feat.example); onClose(); }}
                        >
                          Experimentar: "{feat.example}"
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

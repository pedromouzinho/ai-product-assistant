/**
 * toolsCatalog.js — Single source of truth for all tool descriptions.
 *
 * Each section has:
 *   - id: unique key
 *   - title: display name
 *   - iconKey: maps to an icon in the rendering component
 *   - tools[]: array of { name, desc, prompts[] }
 *
 * Keep this file updated when tools are added/removed in the backend.
 * The ToolsManual component renders this data — no descriptions live in the component.
 */

const TOOLS_CATALOG = [
    {
        id: 'devops',
        title: 'User Stories & DevOps',
        iconKey: 'devops',
        tools: [
            {
                name: 'Gerar User Stories',
                desc: 'Cria user stories formatadas com critérios de aceitação, prioridade e estimativa. Suporta geração a partir de descrição livre, screenshots de UI ou contexto de backlog existente.',
                prompts: [
                    'Gera 3 user stories para um formulário de registo com validação de NIF',
                    'Cria uma epic de onboarding digital com 5 stories prioritizadas',
                ],
            },
            {
                name: 'Pesquisar Work Items',
                desc: 'Pesquisa full-text no Azure DevOps. Encontra bugs, stories, tasks e epics por palavras-chave, estado, sprint ou assignee.',
                prompts: [
                    'Mostra-me os bugs abertos do sprint atual',
                    'Quais são as stories assigned à Jane Doe?',
                ],
            },
            {
                name: 'Consultar Hierarquia',
                desc: 'Navega nas relações parent/child de work items. Útil para ver a estrutura de uma epic ou feature.',
                prompts: [
                    'Mostra a hierarquia completa da epic 12345',
                    'Quais são os filhos do PBI 67890?',
                ],
            },
            {
                name: 'KPIs e Métricas',
                desc: 'Calcula métricas agregadas do backlog: velocidade, burn-down, cycle time, distribuição por estado, sprint comparisons.',
                prompts: [
                    'Qual é a velocidade média dos últimos 3 sprints?',
                    'Mostra a distribuição de bugs por prioridade',
                ],
            },
            {
                name: 'Analisar Padrões',
                desc: 'Analisa padrões nos work items com IA: identifica tendências, bottlenecks, e áreas com maior concentração de bugs ou débito técnico.',
                prompts: [
                    'Analisa os padrões de bugs dos últimos 2 meses',
                    'Quais são as áreas com mais débito técnico?',
                ],
            },
            {
                name: 'Criar Work Item',
                desc: 'Publica stories, bugs ou tasks diretamente no Azure DevOps com campos preenchidos (título, descrição, ACs, prioridade, área).',
                prompts: [
                    'Publica esta story no DevOps no sprint atual',
                    'Cria um bug de prioridade alta para o leak de memória no login',
                ],
            },
            {
                name: 'Refinar Work Item',
                desc: 'Melhora a qualidade de um work item existente: reformula descrição, adiciona critérios de aceitação em falta, sugere estimativa.',
                prompts: [
                    'Refina o PBI 54321 com ACs mais detalhados',
                ],
            },
        ],
    },
    {
        id: 'analytics',
        title: 'Análise de Dados',
        iconKey: 'analytics',
        tools: [
            {
                name: 'Analisar Tabela',
                desc: 'Executa análise estatística sobre ficheiros CSV ou Excel enviados. Calcula resumos, distribuições, correlações e detecta outliers automaticamente.',
                prompts: [
                    'Faz um resumo estatístico deste ficheiro',
                    'Quais são as colunas com mais valores nulos?',
                    'Mostra a correlação entre as variáveis numéricas',
                ],
            },
            {
                name: 'Gerar Gráficos',
                desc: 'Cria gráficos interativos (barras, linhas, scatter, histogramas, pie charts) a partir de dados enviados ou resultados de análise.',
                prompts: [
                    'Cria um gráfico de barras com as vendas por região',
                    'Mostra a evolução mensal dos bugs num line chart',
                ],
            },
            {
                name: 'Executar Código Python',
                desc: 'Corre código Python com pandas, DuckDB, matplotlib e numpy. Útil para transformações complexas, joins, pivots ou cálculos personalizados.',
                prompts: [
                    'Faz um pivot table dos dados agrupados por mês e categoria',
                    'Calcula o rolling average de 7 dias para a coluna "revenue"',
                ],
            },
            {
                name: 'Dicionário de Dados',
                desc: 'Guarda e recupera mapeamentos de colunas (nomes amigáveis, tipos, descrições) para reutilizar em análises futuras.',
                prompts: [
                    'Guarda o dicionário de dados deste ficheiro',
                    'Qual é o dicionário de dados do último upload?',
                ],
            },
        ],
    },
    {
        id: 'export',
        title: 'Geração de Ficheiros',
        iconKey: 'export',
        tools: [
            {
                name: 'Exportar Apresentação (PPTX)',
                desc: 'Gera apresentações PowerPoint com branding configurável. Suporta slides com texto, tabelas, gráficos e imagens. Ideal para reports e proposals.',
                prompts: [
                    'Cria uma apresentação com o resumo do sprint',
                    'Gera um deck de 5 slides sobre o progresso do projeto',
                ],
            },
            {
                name: 'Exportar Spreadsheet (XLSX)',
                desc: 'Gera ficheiros Excel formatados com headers, estilos, fórmulas e múltiplas sheets. Útil para relatórios tabulares ou extrações de dados.',
                prompts: [
                    'Exporta estes dados para Excel com formatação',
                    'Cria um XLSX com uma sheet por categoria',
                ],
            },
            {
                name: 'Exportar CSV/PDF',
                desc: 'Exporta dados ou conteúdo para CSV ou PDF. O PDF suporta tabelas, headers e formatação básica.',
                prompts: [
                    'Exporta este resultado como CSV',
                    'Gera um PDF com o relatório de bugs',
                ],
            },
        ],
    },
    {
        id: 'upload',
        title: 'Upload & Documentos',
        iconKey: 'upload',
        tools: [
            {
                name: 'Pesquisar em Documentos Uploaded',
                desc: 'Faz pesquisa semântica (RAG) sobre ficheiros enviados: PDFs, Word, imagens com OCR, Excel e CSV. Encontra informação relevante sem ler o documento inteiro.',
                prompts: [
                    'O que diz o manual sobre autenticação?',
                    'Procura referências a "SLA" nos documentos enviados',
                ],
            },
            {
                name: 'Screenshot para User Stories',
                desc: 'Analisa screenshots de interfaces e extrai automaticamente user stories com base nos elementos visuais detectados (formulários, botões, fluxos).',
                prompts: [
                    'Gera user stories a partir deste screenshot',
                ],
            },
        ],
    },
    {
        id: 'email',
        title: 'Email',
        iconKey: 'email',
        tools: [
            {
                name: 'Classificar Emails',
                desc: 'Categoriza emails em massa a partir de um CSV/Excel enviado. Atribui labels (Urgente, Ação Necessária, Informativo, etc.), tipo de ação e nível de confiança.',
                prompts: [
                    'Classifica os emails deste ficheiro por prioridade',
                    'Quais emails precisam de resposta urgente?',
                ],
            },
            {
                name: 'Preparar Rascunho de Email',
                desc: 'Compõe rascunhos de email em formato Outlook. Suporta tom formal/informal, CC, assunto e corpo estruturado.',
                prompts: [
                    'Prepara um email para a equipa sobre o atraso no sprint',
                    'Escreve um email formal ao fornecedor sobre o SLA',
                ],
            },
        ],
    },
    {
        id: 'knowledge',
        title: 'Pesquisa e Conhecimento',
        iconKey: 'search',
        tools: [
            {
                name: 'Pesquisar Base de Conhecimento',
                desc: 'Pesquisa na base de conhecimento interna (portal de produto, documentação interna). Ideal para encontrar procedimentos, normas ou informação institucional.',
                prompts: [
                    'Pesquisa informação sobre o processo de onboarding',
                    'O que diz a documentação sobre limites de transferência?',
                ],
            },
            {
                name: 'Pesquisar Web',
                desc: 'Pesquisa na web externa via Brave Search. Útil para benchmarks, documentação técnica ou informação pública.',
                prompts: [
                    'Pesquisa best practices de CI/CD para Azure DevOps',
                    'Quais são as novidades do .NET 9?',
                ],
            },
        ],
    },
    {
        id: 'design',
        title: 'Design & Colaboração',
        iconKey: 'design',
        tools: [
            {
                name: 'Pesquisar Figma',
                desc: 'Pesquisa ficheiros e componentes no Figma por nome ou descrição. Útil para encontrar designs relevantes para uma feature.',
                prompts: [
                    'Procura os designs do fluxo de login no Figma',
                ],
            },
            {
                name: 'Analisar Fluxo Figma',
                desc: 'Traça e analisa fluxos de utilizador em ficheiros Figma. Identifica ecrãs, transições e interações.',
                prompts: [
                    'Analisa o fluxo de pagamento no ficheiro Figma do projeto',
                ],
            },
            {
                name: 'Pesquisar Miro',
                desc: 'Pesquisa boards no Miro por palavras-chave. Encontra workshops, retrospectivas e diagramas.',
                prompts: [
                    'Procura o board da retrospectiva do último sprint',
                ],
            },
        ],
    },
    {
        id: 'productivity',
        title: 'Produtividade & Aprendizagem',
        iconKey: 'star',
        tools: [
            {
                name: 'Perfil de Escrita',
                desc: 'O assistente aprende o teu estilo de escrita de user stories. Depois de gerar algumas, pode memorizar o tom, estrutura e nível de detalhe que preferes.',
                prompts: [
                    'Aprende o meu estilo de escrita de user stories',
                    'Mostra o meu perfil de escrita guardado',
                ],
            },
            {
                name: 'Digest Diário',
                desc: 'Resumo automático do estado do projeto: stories criadas ontem, bugs antigos, items sem assignee e items fechados esta semana.',
                prompts: [
                    'Mostra o resumo do dia',
                ],
            },
            {
                name: 'Voz (Speech-to-Text)',
                desc: 'Dita perguntas em português e o assistente transcreve e responde. Suporta reconhecimento contínuo.',
                prompts: [],
            },
        ],
    },
];

export default TOOLS_CATALOG;

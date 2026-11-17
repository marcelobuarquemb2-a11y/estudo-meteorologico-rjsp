# Recuperação histórica das previsões Meteoblue — investigação em 17/11/2026

Todos os horários exibidos neste relatório estão em America/Sao_Paulo (Brasília).

## Objetivo

Recuperar em D+1 a previsão originalmente disponível em D, para as referências 09h, 12h, 15h e 18h, preservando as previsões para D+1, D+2 e D+3. O objetivo desta investigação é recuperar versões de previsão. Não envolve comparar previsões com observações.

Caso de controle: captura de 16/11/2026, referência das 15h, iniciada às 15h25min33s:
https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/blob/main/data/2026-11/2026-11-16/152533.json

Os campos modelrun_utc e modelrun_updatetime_utc, convertidos para Brasília, apontavam 16/11 às 15h25 para Paraty, Angra dos Reis, Marambaia, Forte de Copacabana e Ilha Rasa, e 15h26 para Ilha Fiscal. Os metadados pertencem à resposta geral por pacote e estação, sem um par separado para cada dia de validade.

## Testes realizados

| Frente | Evidência | Resultado |
|---|---|---|
| modelrun_updatetime_utc | Seis estações, três horizontes; controle adicional sem filtro em Paraty | Respostas de 17/11 às 17h10. Nenhuma retornou a atualização solicitada de 16/11 às 15h25. |
| modelrun_utc e alternativas | 13 cenários por pacote, 26 consultas em Paraty; basic-1h e wind-3h | Todos os cenários retornaram o mesmo conjunto numérico dos controles sem filtro, com metadados de 17/11 às 17h32. |
| Endpoint de arquivo de rodadas | Controle com o domínio documentado IFSHRES_HOURLY72, por data e por data/hora | HTTP 403: acesso à Dataset API não disponível para a credencial atual. |
| Pacotes usados como domínio de arquivo | basic-1h e wind-3h enviados ao endpoint de arquivo | HTTP 400: Domain not supported. O nome de pacote não é um domínio aceito nesse endpoint. |
| Meteograma interativo | images/meteogram, highchartshtml, com modelrun_utc solicitado para ontem | HTML recebido. O objeto json_data contém dados numéricos atuais; os 12 valores relevantes de Paraty coincidem com o controle atual, não com o conjunto original completo. |
| Interface de verificação interativa | images/meteogram_verify, highchartshtml | HTTP 415: formato não disponível para esse diagrama. Nenhuma comparação com observações foi realizada. |
| Schemas oficiais de Forecast, Image, Maps e Time APIs | OpenAPI obtidos e inspecionados | Não foi encontrado seletor documentado de emissão passada para os nossos dois pacotes. Em Maps, lastUpdate é descrito como controle para limpeza de cache. |

A matriz de filtros incluiu: modelrun_utc no formato original, ISO e Unix; ambos os campos de metadados; combinação com history_days; referência nominal das 15h; aliases modelrun, run e asof; uma data impossível e controles sem filtro antes e depois.

Aceitação HTTP 200 não foi contada como recuperação histórica. Nenhum teste comprovou recuperação do conjunto original pela seleção da emissão.

## Resultado numérico da interface alternativa

Extraídos do objeto JSON do gráfico, sem estimativa por cores ou pixels. Os ventos foram convertidos para nós. Todos os horários de validade são 15h de Brasília.

| Validade | Vento médio (kt) | Rajada (kt) | Direção (graus) | Chuva (mm) |
|---|---:|---:|---:|---:|
| 17/11/2026 | 3,810 | 7,387 | 104 | 0,0 |
| 18/11/2026 | 4,665 | 7,484 | 105 | 1,2 |
| 19/11/2026 | 2,430 | 7,503 | 70 | 1,9 |

Esses números reproduzem o controle atual consultado às 17h32. A interface alternativa não forneceu um identificador verificável da emissão antiga.


## Novas alternativas examinadas até 18h46 de Brasília

| Frente | Teste efetivo | Resultado e limite |
|---|---|---|
| Página pública e seletores de data/rodada | Página de Paraty, controle, date e modelrun | HTTP 200 nos três casos; texto visível normalizado igual ao controle atual. A localização da cidade difere da estação e não foi usada como substituta. |
| Código público do site | HTML e dois scripts próprios referenciados pela página | Não foi localizado arquivo de edições anteriores. Referências encontradas apontam às interfaces já testadas. |
| Internet Archive | Consulta availability para a página de Paraty e referência de 16/11 às 15h de Brasília | archived_snapshots vazio. Uma consulta CDX mais ampla expirou por timeout; isso não demonstra inexistência de toda e qualquer captura no serviço. |
| Common Crawl | Catálogo de coleções públicas | A coleção mais recente encontrada era CC-MAIN-2026-43, de 5 a 18 de outubro. Não cobre 16/11. |
| Verificação em formato numérico | meteogram_verify com highcharts e json | highcharts: HTTP 415, formato indisponível para o diagrama; json: HTTP 422, formato inválido. Soma-se ao highchartshtml já testado. |
| Fonte da página pública de verificação | HTML de verificationshort e rota de imagem referenciada | A página aponta ao mesmo meteogram_verify. Não foi encontrada uma série numérica de previsões no HTML. |
| Metadados da imagem de verificação | PNG obtido com a nossa própria credencial | HTTP 200. Os dois blocos de texto contêm somente autoria e licença; nenhum conjunto numérico de previsões ou identificador de emissão. Não se estimaram valores por pixels. |
| Relatórios oficiais de verificação | Painel público e relatório de vento de outubro/2026 | Painel lista 30 relatórios mensais de janeiro a outubro, para três variáveis. O relatório de vento inspecionado contém imagens e tabelas agregadas, sem arquivos CSV ou consulta de registros horários. Não cobre a emissão de 16/11. |

A mera existência de gráficos de verificação não foi considerada prova de acesso ao arquivo das previsões com 24/48/72 horas de antecedência. Também não foram comparadas as previsões do estudo com observações.

### Teste de persistência do mesmo conjunto em capturas posteriores

Hipótese: uma resposta bruta obtida algumas horas depois poderia ainda conter exatamente os valores da captura anterior, nos mesmos horários futuros.

Foram selecionadas 20 capturas completas entre 11/11 às 12h e 17/11 às 15h, uma por referência, dentro da janela aceita. Cada conjunto contém 72 valores: seis estações, três horários futuros e quatro variáveis.

Antes da comparação, a extração dos dados brutos foi conferida contra os valores registrados no próprio arquivo original. A comparação usa identidade numérica, com tolerância de 0,002 apenas para arredondamento; não usa a tolerância meteorológica de acerto de ±2 nós.

| Intervalo entre referências | Pares de capturas | Conjuntos completos idênticos | Vento médio idêntico |
|---|---:|---:|---:|
| 3 horas | 13 | 0 de 13 | 39 de 234 = 16,7% |
| 24 horas | 15 | 0 de 15 | 2 de 270 = 0,7% |
| Todos os intervalos examinados | 61 | 0 de 61 | 44 de 1.098 = 4,0% |

Os demais intervalos nominais foram 6, 9, 15, 18, 21 e 27 horas; o intervalo efetivo entre capturas foi limitado a 27 horas. Os pares se sobrepõem e não constituem ensaios estatísticos independentes.

No caso de controle de 16/11, a captura de 18h26 ainda contém os horários futuros de 17, 18 e 19/11 às 15h, mas apenas 16 dos 72 valores permanecem iguais aos da captura das 15h25. Exemplo de vento médio em Paraty para 17/11 às 15h: 3,227 nós na captura original e 3,207 nós na posterior.

Conclusão limitada à amostra: presença do mesmo horário futuro na resposta posterior não preservou o conjunto originalmente emitido. Coincidência isolada de uma célula também não comprova sua versão de origem. Essa hipótese não foi habilitada como procedimento de recuperação.

Os arquivos anteriores ao período analisado que não possuem o formato atual foram excluídos; os avisos correspondentes estão preservados no JSON diagnóstico.

Os resultados estruturados estão em [meteoblue_additional_tests_2026-11-17.json](./meteoblue_additional_tests_2026-11-17.json).

## Limites da conclusão

O insucesso dos filtros testados não demonstra que seja impossível recuperar uma previsão antiga por outro serviço do fornecedor. O bloqueio 403 informa uma limitação da credencial atual. Habilitar a Dataset API, isoladamente, também não comprova disponibilidade dos snapshots dos pacotes basic-1h e wind-3h.

A documentação pública da Model-Run Archive API descreve rodadas históricas do IFS HRES. Isso não comprova arquivo das previsões processadas dos nossos pacotes. A necessidade específica é um arquivo de previsões originalmente emitidas, com seleção por data/hora de emissão e todos os horizontes de previsão preservados.

Os horários de metadados coincidiram repetidamente com o minuto da requisição. O significado desses campos para os pacotes processados e sua adequação como identificador de versão precisam ser esclarecidos pelo fornecedor.

## Situação atual e contato suspenso

Até os testes registrados acima, não foi encontrado um método público comprovado de seleção da emissão original dos pacotes basic-1h e wind-3h. Isso não demonstra inexistência de um arquivo interno ou de outra modalidade de acesso.

O produto público Model-Run Archive continua sendo uma opção para o modelo bruto documentado, mas não foi comprovado para os nossos pacotes processados, e a nossa credencial recebeu 403. Não se substituiu o Meteoblue processado por saída de outro modelo.

A orientação atual é buscar alternativas sem contatar o fornecedor por enquanto. Nenhuma mensagem foi enviada. A mensagem técnica abaixo permanece apenas arquivada para eventual uso futuro:

**To:** support@meteoblue.com
**Subject:** Historical snapshots of basic-1h and wind-3h forecasts

Hello,

We need to retrieve archived, as-issued snapshots of basic-1h and wind-3h, selected by their original issuance date and time, retaining forecasts for 24, 48 and 72 hours ahead.

For example: the snapshot delivered on 16 November 2026 at 15:25 America/Sao_Paulo, with forecasts valid on 17, 18 and 19 November at 15:00.

Our Forecast API requests return current data when modelrun_utc or modelrun_updatetime_utc are passed as query parameters. Our key receives HTTP 403 from the Model-Run Archive API.

Do you provide this forecast archive? If so, which endpoint and version identifier should we use, and is trial access available?

Thank you,
Marcelo Buarque

## Evidências de execução

- Consulta inicial por atualização: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44166388324
- Matriz de seletores: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44170329219
- Acesso ao arquivo e catálogo: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44170612516
- Interfaces alternativas e schemas: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44172033371

- Arquivos públicos e caches: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44180060350
- Formatos numéricos da verificação: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44180737177
- Persistência entre capturas: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44181783998
- Fonte e metadados da verificação: https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp/actions/runs/44182601725

## Fontes do fornecedor

- Forecast API: https://docs.meteoblue.com/en/weather-apis/forecast-api/overview
- Model-Run Archive API: https://docs.meteoblue.com/en/weather-apis/further-apis/model-run-archive-api
- Maps API: https://docs.meteoblue.com/en/weather-apis/maps-api/tile-api
- Dataset API: https://docs.meteoblue.com/en/weather-apis/dataset-api
- Trial: https://business.meteoblue.com/pricing

- Painel de relatórios: https://www.meteoblue.com/pub/verification/reports/reports_dashboard_external.html
- Relatório público de vento inspecionado: https://www.meteoblue.com/pub/verification/reports/wind_2026_10_external.html
- Explicação da equipe do Windy sobre atualizações e verificação com antecedências fixas: https://community.windy.com/topic/46198/metroblue-revising-past-data-to-game-comparisons/3
- Histórico versus previsões originalmente emitidas: https://community.meteoblue.com/topic/156/downloading-comparison-data

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

## Limites da conclusão

O insucesso dos filtros testados não demonstra que seja impossível recuperar uma previsão antiga por outro serviço do fornecedor. O bloqueio 403 informa uma limitação da credencial atual. Habilitar a Dataset API, isoladamente, também não comprova disponibilidade dos snapshots dos pacotes basic-1h e wind-3h.

A documentação pública da Model-Run Archive API descreve rodadas históricas do IFS HRES. Isso não comprova arquivo das previsões processadas dos nossos pacotes. A necessidade específica é um arquivo de previsões originalmente emitidas, com seleção por data/hora de emissão e todos os horizontes de previsão preservados.

Os horários de metadados coincidiram repetidamente com o minuto da requisição. O significado desses campos para os pacotes processados e sua adequação como identificador de versão precisam ser esclarecidos pelo fornecedor.

## Próxima dependência concreta

Confirmar se o Meteoblue oferece o arquivo dos snapshots dos dois pacotes e, se disponível, obter instruções de acesso e possibilidade de teste. Há oferta pública de trial mediante contato; escopo, duração e condições devem ser confirmados com o fornecedor.

Mensagem técnica preparada, ainda não enviada:

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

## Fontes do fornecedor

- Forecast API: https://docs.meteoblue.com/en/weather-apis/forecast-api/overview
- Model-Run Archive API: https://docs.meteoblue.com/en/weather-apis/further-apis/model-run-archive-api
- Maps API: https://docs.meteoblue.com/en/weather-apis/maps-api/tile-api
- Dataset API: https://docs.meteoblue.com/en/weather-apis/dataset-api
- Trial: https://business.meteoblue.com/pricing

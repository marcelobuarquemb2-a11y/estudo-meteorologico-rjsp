# Estudo Meteorológico RJ–SP

Coleta auditável de previsões meteorológicas para seis estações no litoral RJ–SP.

Os horários de referência são 09h, 12h, 15h e 18h de Brasília. Os horizontes nominais são 24, 48 e 72 horas.

A tolerância de ±30 minutos inclui os limites e se aplica à hora da consulta e à validade das previsões, bem como à hora da medição real. A publicação da medição pode ocorrer mais tarde. Entre rodadas de previsão ou medições reais da mesma janela, a comparação prioriza a mais completa e, em caso de empate, a mais próxima do horário de referência. Os horários reais ficam registrados.

As previsões e os actuals têm seis disparos por horário de referência: −20, −10, 0, +10, +20 e +30 minutos. As consultas de −20 e −10 são realizadas mesmo se houver sucesso antecipado. A primeira execução dentro da janela que começa no horário ou depois dele renova a consulta, mesmo que a coleta antecipada já esteja completa. Após essa renovação, novas consultas ocorrem somente enquanto não houver sucesso completo arquivado. Uma renovação parcial não apaga uma coleta completa anterior. Entre registros válidos, a comparação prioriza a maior completude e, em caso de empate, a menor distância ao horário nominal. Os disparos continuam sujeitos aos atrasos do agendador do GitHub Actions; uma execução que só começa após +30 minutos não consulta novas previsões nem realiza a tentativa principal de actuals.

Os actuals antecipados incluem somente medições realmente publicadas, com horário de medição até a hora da consulta e dentro da tolerância de ±30 minutos do horário de referência. Após a renovação nominal, são reconsultadas somente fontes com estações ainda incompletas; os resultados completos já arquivados são preservados com sua origem registrada. Para publicações posteriores à janela principal, permanecem as recuperações condicionais de actuals em +1h20 e +2h20 e às 08h40 do dia seguinte. A hora de publicação pode ser posterior; a hora da medição continua sujeita à mesma tolerância.

As previsões e as medições registram a direção de origem do vento em graus e o octante correspondente. Os setores têm 45° e são centrados em N (0°), NE (45°), E (90°), SE (135°), S (180°), SW (225°), W (270°) e NW (315°). No limite exato entre dois setores, vale o próximo no sentido horário.

A direção conta como acerto quando os octantes previsto e observado coincidem ou quando a menor diferença angular no círculo é estritamente inferior a 30°. Com diferença igual a 30°, o acerto depende de os octantes coincidirem. A comparação registra o desvio e o acerto quando ambas as direções estão disponíveis.

Fontes: Open-Meteo (ICON, GFS, ECMWF) e Meteoblue (basic-1h, wind-3h; identificação mLM ainda não comprovada).

A série oficial começa em 07/11/2026 às 15h de Brasília. A primeira comparação possível com os três horizontes é a da observação de 10/11/2026 às 15h, após a publicação das medições.

## Recuperação retrospectiva de previsões (sem reescrever coletas originais)

As rodadas de ICON, GFS e ECMWF recuperadas pela Open-Meteo Single Runs API
ficam em `data/recovered_forecasts/`, incluindo modelo, rodada UTC, estação, variável,
antecedência e horário-alvo. `recover_forecast_history.py` transforma os registros brutos
de `data/recovery_candidates/`; nenhuma coleta original em `data/YYYY-MM/DD/` é alterada.

**Uma rodada histórica não comprova, por si só, que já estava publicada no horário-alvo.**

- `snapshot_matched`: rodada confirmada por correspondência exata com valores coletados
  dentro da janela original. Pode complementar campos faltantes da mesma coleta.
- `vintage_inferred_publication_unverified`: rodada inferida por observações em outros dias;
  não há comprovação direta da sua disponibilidade no horário nominal perdido.

Em `data/comparisons/recovered_pairs_status.json`, cada campo é rotulado
`original_capture`, `recovered_snapshot_matched` ou `recovered_inferred`.
`available_pairs` inclui pares provisórios; `eligible_pairs` conta apenas os
pares com previsão original ou recuperação confirmada contra snapshot.
O relatório operacional `data/comparisons/status.json` permanece inalterado.

A rotina de observações atualiza os pares recuperados com novos actuals.
Para atualização manual: `python recover_forecast_history.py --pairs-only`.

## Conclusão metodológica adotada em 15/11/2026 — confiabilidade da recuperação histórica

O estudo passa a **aceitar as previsões retroativamente recuperadas** de ICON, GFS e ECMWF
como dados utilizáveis na avaliação de acerto, distinguindo seu método de obtenção
(`coleta_original` / `coleta_retroativa`) e preservando as identificações das rodadas.
A coleta retroativa no dia seguinte é o procedimento definido para esses três modelos;
a Meteoblue continua dependendo da coleta dentro da janela original.

## Coleta diária D+1 — ativação em 16/11/2026

O workflow `coleta_retroativa_diaria.yml` recupera as previsões de ICON, GFS e
ECMWF correspondentes às referências 09h, 12h, 15h e 18h do dia anterior, nas
seis estações, para 24h, 48h e 72h: **864 valores esperados por dia**.

São dez oportunidades programadas, de **09h00 a 12h00 BRT**, a cada 20 minutos.
Cada execução consulta somente os conjuntos ainda incompletos. Após 100% de
cobertura, as demais oportunidades encerram sem consultar os provedores.
O limite é dez tentativas por data de referência. O início efetivo ainda está
sujeito ao agendador GitHub Actions; a recuperação histórica não depende de
executar dentro da janela de consulta original.

`collect_previous_day.py` consulta rodadas individuais na Single Runs API.
Quando há captura original elegível, seleciona a rodada que melhor reproduz
suas previsões, sem usar actuals para essa escolha. Nas janelas sem captura,
aplica a regra de ciclos calibrada no estudo (`rjsp_calibrated_reference_cycles_v1`):

| Referência BRT | ICON UTC | GFS UTC | ECMWF UTC |
|---|---|---|---|
| 09h | 06Z | 06Z | 00Z |
| 12h | 06Z | 06Z | 06Z |
| 15h | 12Z | 12Z | 06Z |
| 18h | 12Z | 12Z | 12Z |

A rodada, coordenadas solicitadas e retornadas, unidades, horários-alvo,
correspondência com snapshots e erros são preservados. A escolha não presume
prova de publicação histórica no nominal; esse metadado continua distinto da
aceitação da coleta retroativa no estudo. Valores válidos já recuperados são
preservados nas novas tentativas, usando a mesma rodada ao completar um conjunto.

Resultados: `data/recovered_forecasts/AAAA-MM-DD/HHMM.json`.
Estado e tentativas: `data/daily_recovery/AAAA-MM-DD/status.json`.
Os pares são atualizados em `data/comparisons/recovered_pairs_status.json`.
As capturas originais são mantidas como evidência histórica. A partir da referência
25/11/2026 15h BRT, ICON, GFS e ECMWF passam a operar exclusivamente em D+1;
o coletor em tempo real consulta somente Meteoblue. As rotinas de Meteoblue e
actuals mantêm seus agendamentos. Os três modelos usam a mesma validação, extração,
controle de tentativas, armazenamento e deduplicação; somente os ciclos calibrados
por modelo diferem. Para auditorias históricas anteriores à transição, a comparação
usa igualdade numérica e normalização da precipitação para uma casa decimal,
sem exceção por modelo; os valores brutos e as divergências são preservados.
A aceitação anterior de arquivos já armazenados não é reescrita.

Uma alteração do código no main executa uma primeira coleta real. Também é
possível usar workflow_dispatch, informando opcionalmente a data de referência,
ou executar `python collect_previous_day.py --date AAAA-MM-DD`.

**Evidência disponível:** comparação amostral de 8 arquivos de coleta × 2 estações × 3
modelos × 3 horizontes × 4 variáveis: **570/576 valores idênticos (98,96%)**.
ICON: **192/192**; ECMWF: **192/192**; GFS: **186/192**.
Em 23 das 24 combinações coleta × modelo houve reprodução integral.
As únicas **6 divergências** ocorreram na variável rajadas (`wind_gusts_10m`) do GFS,
na coleta de 14/11/2026 às 15h BRT, em Forte de Copacabana e Ilha Rasa, nas
antecedências de 24h, 48h e 72h. A anomalia será tratada como localizada e monitorada;
não será generalizada a ICON ou ECMWF.

**Limitação científica mantida:** as rodadas históricas desse teste foram identificadas
pela melhor correspondência com capturas originais conhecidas. Portanto, 98,96%
mede **reprodutibilidade amostral**, não a capacidade já demonstrada de selecionar
autonomamente a rodada disponível no horário perdido. Nas janelas sem captura,
a disponibilidade exata da rodada inferida permanece sem comprovação independente.
Esta ressalva permanece no metadado de auditoria, sem excluir automaticamente
os dados retroativos da análise expandida aprovada.

Fonte auditável: `data/recovery_candidates/openmeteo_retrospective_test_20261115.json`.


## Resilience of the existing GitHub Meteoblue collection (16/11/2026)

The GitHub Actions schedule remains the active platform; no cloud migration is enabled.
Since the 25/11/2026 cutover, real-time acquisition queries **only Meteoblue**.
Meteoblue numerical coverage and on-window acquisition timestamps are evaluated
independently; Open-Meteo is acquired exclusively by the previous-day collector.

Each provider response is saved atomically to a *local incremental checkpoint*.
When the final original forecast snapshot is successfully written, this temporary
checkpoint is removed. If the Python collection exits unexpectedly but the workflow
reaches its always-run archival step, any remaining checkpoint is committed for
diagnosis/replay; a sudden runner loss before archival is still a GitHub limitation.
Scheduled attempts also create a compact diagnostic in
`data/collection_diagnostics/YYYY-MM-DD/` with nominal slot, actual start,
GitHub run ID, late-window/complete/partial status and Meteoblue coverage.
The workflow retries Git push conflicts up to five times.

No late (>30 min) provider response is accepted as an official Meteoblue capture.
This change does not extend the scientific window and does not modify the
D+1 retrospective collection of ICON/GFS/ECMWF.



## Coleta Meteoblue com início antecipado (17/11/2026)

A rotina `meteoblue_early.py` mantém a janela original de ±30 minutos e os alvos
nominais de 24/48/72 horas. Os novos disparos do GitHub são em H−73 minutos
(principal) e H−53 minutos (reserva), fora dos minutos cheios. Para 09h BRT:
07h47 e 08h07. Para 12h: 10h47 e 11h07. Para 15h: 13h47 e 14h07. Para 18h:
16h47 e 17h07. O processo iniciado cedo aguarda até H−30 sem consultar a API.
Se iniciar atrasado dentro da janela, consulta imediatamente. Se o pacote
permanecer parcial, publica os dados e aguarda cinco minutos antes da próxima
tentativa, limitada ao fechamento H+30. A conclusão exige um pacote validado
com 72 valores (seis estações × três alvos × quatro indicadores) e confirmação
do envio ao repositório. A janela de aquisição original nunca é ampliada.

### Duplicidade e rastreabilidade

- Principal e reserva usam o mesmo grupo de concorrência por hora nominal,
  sem cancelar uma execução ativa. A reserva faz checkout atualizado ao iniciar.
- Antes de consultar, a rotina atualiza o repositório e procura uma captura
  Meteoblue completa para a mesma data e referência, nas coordenadas vigentes.
  Se encontrar, encerra sem nova consulta. Uma captura parcial não bloqueia a reserva.
- O coletor repete a verificação imediatamente antes de consultar. Os snapshots
  antecipados usam nomes com microssegundos para evitar sobrescrita entre rotinas.
- A rotina anterior permanece como backup exclusivamente da Meteoblue, podendo produzir outra captura bruta.
  A seleção em `compare_forecasts.forecast_archives` escolhe somente um frame
  Meteoblue completo por referência, independentemente da cobertura Open-Meteo:
  menor distância ao horário nominal, depois horário de consulta e nome do arquivo.
  Essa escolha preserva um frame completo, sem combinar pacotes de tentativas diferentes.
  A chave estação × modelo × alvo × indicador aparece uma única vez na série.
- Cada linha selecionada guarda o arquivo e o horário de consulta correspondentes.
  Capturas brutas repetidas permanecem exclusivamente como trilha de auditoria.
- Cada tentativa publica os arquivos antes de declarar sucesso. Se o envio falhar
  após as repetições, o workflow tenta preservar os dados como artefato por 30 dias.
  Isso não é uma garantia contra falha abrupta do runner antes do upload.

O agendamento continua sujeito a atraso ou descarte no GitHub. A antecipação e
as tentativas locais reduzem essa exposição, sem oferecer garantia de pontualidade.
ICON/GFS/ECMWF mantêm a recuperação D+1. A rotina anterior de coleta e os actuals
permanecem ativos. Este workflow não envia notificações neste chat.

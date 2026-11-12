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



## Candidata de migração para Google Cloud

A infraestrutura paralela, condições de ativação, custos e plano de sete dias estão em [cloud/README.md](cloud/README.md). A configuração não ativa a migração automaticamente.

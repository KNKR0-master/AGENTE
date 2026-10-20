# Publisher: preparação local — gate final aprovado

Data: 20/10/2026. Base integrada: `069fc20c08c398ae846b13c0da470cf38a40dad2`.

Resultado: **READY_FOR_LIMITED_MODE_COMMIT**. A entrega passou na descoberta
completa do Publisher e foi preparada para publicação do código na `main`.
Publicar o repositório não implanta nem reinicia o Publisher operacional.

## Funcionalidade validada

Na Biblioteca, **Preparar vídeo localmente** produz MP4 para prévia e download,
individualmente ou em lote. Cada vídeo começa sem headline. Habilitá-la exige
seleção explícita; nesse caso, o resultado precisa ser um derivado validado.

Os jobs locais preservam identidade da mídia, snapshot, reservas, histórico,
idempotência e exclusão mútua. Não executam upload TikTok, Save Draft, publicação,
agendamento, promoção remota para PERSISTED, consumo ou exclusão no Drive.
Cancelamento, retry, refresh, recuperação de startup e download foram testados
com mídia sintética, Fake Drive e Chrome descartável limitado a loopback.

O renderer conserva libx264 medium, CRF 18, até quatro threads, timeout de encode
limitado a 600 segundos e validações de CFR/VFR, frames, áudio, timestamps,
identidade e cache. NVENC não foi habilitado.

## Resultado do gate

| Verificação | Resultado |
| --- | --- |
| Smoke local individual/bulk antes bloqueado | 1 aprovado |
| Cobertura afetada de fixtures e Chrome | 16 aprovados |
| Publisher completo | 884 executados: 882 aprovados, 2 skips conhecidos |
| Falhas / erros | 0 / 0 |
| Exceções inesperadas de fixture | 0 |
| Complementares Drive | 27 resultados anteriores reutilizados, hashes conferidos |
| Checks estáticos | 12 compiles Python, 3 checks JavaScript, diffs aprovados |

Os skips foram os testes operacionais opt-in:

- `test_publisher_queue_browser.QueueBrowserTests.test_operational_snapshot_read_only`.
- `test_publisher_thumbnails_operational.OperationalThumbnailTests.test_four_real_items_in_temporary_chrome`.

A suíte completa foi executada sem fail-fast para falhas comuns. Os 30 testes
novos de backend local e os dois cenários Chrome passaram dentro da descoberta.
Os guards P0 de histórico, persistência confiável, reupload, auto_cleanup e
consentimento destrutivo independente também passaram.

## Correção do harness Windows

O harness anterior criava um CWD de encode com 272 caracteres. O Windows
rejeitava iniciar o processo com WinError 267. O runner
`tests/publisher_windows_gate.py` usa TEMP curto e exclusivo por processo;
`tests/test_publisher_remote.py` respeita esse TEMP na criação de sua fixture.

O CWD final passou para 180 caracteres. A prova `python -B -c pass` retornou 0
nesse diretório e o encode real concluiu. O guard permite SQLite apenas em
memória ou na raiz canônica específica da execução; não permite QA inteiro,
TEMP geral, bancos ou perfis operacionais. Todas as raízes de execução foram
removidas corretamente. TEMP/TMP foram alterados somente no processo do teste.

A correção anterior de WinError 10053 permaneceu intacta, incluindo as asserções
negativas. Exceções de transporte não foram ignoradas genericamente.

## Reprodução

```powershell
python -B -m tests.publisher_windows_gate probe
python -B -m tests.publisher_windows_gate smoke
python -B -m tests.publisher_windows_gate affected
python -B -m tests.publisher_windows_gate full
```

O modo `full` usa descoberta `test_publisher*.py`. Os logs e manifestos detalhados
ficam na pasta local ignorada `data/publisher/qa/final-full-local-preparation/`;
bancos, vídeos, perfis, credenciais e artifacts de QA não acompanham o código.

## Limite operacional

**READY_FOR_OPERATIONAL_DEPLOYMENT=false**. O gate não comprova persistência no
TikTok real. Nenhuma prova Fake é autoridade de produção. A ausência de prova
real mantém estados UNCONFIRMED e os bloqueios de segurança correspondentes.

Implantar exige decisão separada, backup consistente e ensaio de startup em
cópia isolada. Este trabalho não alterou banco operacional, Drafts, MediaAssets,
runtime, Analytics ou contas reais, nem executou upload ou exclusão remota.

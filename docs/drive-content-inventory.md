# Inventario de conteudo no Google Drive

## Escopo

Este modulo faz leitura somente de metadados da arvore configurada em
`GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID`. Ele nao move, renomeia, compartilha,
publica nem apaga arquivos. Affiliate Orders, GMV, pedidos, comissoes e
liquidacao continuam sob responsabilidade dos importadores TikTok existentes.

Origem esperada:

- conta: `ceciliafigueiredo.shop@gmail.com`;
- raiz: `FLOW` (`1XA8zgZNAMjhRvonypu1hb7cm3b9iX4Gc`);
- escopo Google: `drive.metadata.readonly`.

## Fonte unica de disponibilidade

`VideoPublicationEligibilityService` classifica cada arquivo em:

- `AVAILABLE`: existe no Drive, esta em pasta mapeada, atende a regra de nome,
  possui Content reconciliado, validacao tecnica `PASS` e estado `READY`;
- `ALREADY_PUBLISHED`: Content em `PUBLISHED`;
- `ALREADY_QUEUED`: Content em `QUEUED` ou `SENT_TO_PHONE`;
- `INVALID`: validacao tecnica falhou ou Content falhou;
- `DUPLICATE`: duplicidade confirmada por ID, hash ou assinatura de conteudo;
- `NEEDS_REVIEW`: falta mapeamento, importacao ou validacao tecnica;
- `IGNORED`: nao e MP4, nome fora do padrao ou pasta ignorada/administrativa;
- `MISSING_FROM_DRIVE`: arquivo conhecido nao apareceu no ultimo scan
  bem-sucedido da pasta.

Somente `AVAILABLE` com `missing_since IS NULL` entra em "Videos disponiveis
para publicacao". Um registro local `READY` isolado nunca entra na contagem.

A regra operacional de nome permanece:

```text
(?i)(G\d+C\d+A\d+|FINAL)
```

A verificacao tecnica reaproveita a importacao local com `ffprobe`; arquivos
novos encontrados apenas por metadados ficam em `NEEDS_REVIEW` ate serem
importados e verificados. Nenhum limite tecnico hipotetico foi adicionado.

## Inventario e reconciliacao

O scanner persiste:

- identidade da pasta, pai, caminho relativo, profundidade e estado do scan;
- identidade do arquivo pelo Drive File ID, pasta atual, MD5, tamanho e datas;
- classificacao, motivo, Content reconciliado e arquivo canonico da duplicata;
- runs completos, parciais ou com erro;
- historico de mapeamentos manuais.

Prioridade de reconciliacao de arquivo:

1. Drive File ID;
2. hash, quando comparavel;
3. nome exato e produto, quando o resultado e unico;
4. nome, tamanho e produto como possivel duplicata.

Um arquivo movido troca de contexto sem gerar outro registro. Uma pasta que
falha nao invalida as pastas lidas; arquivos antigos dessa pasta nao sao
marcados como ausentes durante um scan parcial.

## Mapeamento pasta-produto

Estados: `MAPPED_TO_PRODUCT`, `CATEGORY_ONLY`, `ADMINISTRATIVE`, `IGNORED` e
`NEEDS_REVIEW`.

Prioridade deterministica:

1. confirmacao manual por Folder ID, confianca 1.0;
2. mapeamento herdado de pasta-pai confirmada;
3. alias ou nome normalizado exato;
4. segmento de caminho e sobreposicao de tokens;
5. sugestao para revisao abaixo do limiar ou em caso ambiguo.

Uma pasta pode apontar para no maximo um produto; um produto pode possuir
varias pastas. O painel permite confirmar, reclassificar ou criar o produto.
Toda confirmacao manual e auditada e reprocessa imediatamente a pasta e suas
descendentes.

## Cobertura, tendencia e prioridade

`ProductContentInventory` combina disponibilidade com GMV, pedidos, comissao e
Content IDs de venda ja existentes. A cobertura e:

```text
available_count / taxa_de_publicacao_diaria
```

A taxa usa publicacoes reais dos ultimos 30 dias; sem historico, usa o alvo
configuravel `CONTENT_PUBLICATION_TARGET_PER_DAY=2`.

Momentum compara GMV dos ultimos 7 dias com os 7 dias anteriores:

- `ACCELERATING`: inicio de vendas ou crescimento de pelo menos 25%;
- `STABLE`: entre 75% e 125% do periodo anterior;
- `DECLINING`: 75% ou menos;
- `INSUFFICIENT_DATA`: ambos os periodos sem GMV.

O score, limitado a 0..100, soma sinais explicaveis:

- GMV 7d: ate 30 pontos;
- pedidos 7d: ate 20;
- momentum: -5 a 20;
- videos disponiveis: ate 30;
- cobertura: ate 20;
- fila vazia: 5;
- falta de publicacao recente: 10.

Faixas: `HIGH >= 60`, `MEDIUM >= 35`, `LOW < 35`. Sem performance comercial,
sem pasta mapeada ou sem scan confiavel, `data_confidence` fica `LOW`; isso
evita interpretar falta de dados como oportunidade comercial forte.

## Alertas e operacao

O alerta usa `CONTENT_STOCK_WATCH_THRESHOLD=8`. Estados e eventos evitam
repeticao ate ocorrer mudanca de estado, variacao configurada da quantidade,
mudanca de momentum ou fim do cooldown. Recuperacao tambem gera evento.

Comandos:

```powershell
python -m pip install -r requirements.txt
python manage_drive.py scan
python manage_drive.py status
python manage_drive.py map FOLDER_ID PRODUCT_ID
```

O dashboard expoe a aba **Drive e estoque** e o botao **Atualizar Drive**.
Cache normal: 5 a 15 minutos, configurado por
`DRIVE_INVENTORY_CACHE_SECONDS`; o botao forca uma nova leitura.

## Autorizacao ativa

Em 2026-08-08, a aplicacao server-side foi autorizada no projeto Google
`banded-chimera-505001-g3` por uma conta de servico dedicada. A raiz `FLOW`
foi compartilhada com essa conta somente como **Leitor**, e o JSON privado
ficou fora do repositorio. O `.env` local aponta para essa credencial.

A primeira leitura real terminou com status `COMPLETE`:

- 95 pastas processadas de 95;
- 1.037 arquivos encontrados;
- 885 arquivos de video;
- 865 candidatos no padrao operacional aguardando mapping/importacao;
- 4 duplicatas por hash;
- 168 arquivos ignorados;
- 0 erros de pasta.

O total `AVAILABLE` permaneceu zero porque a base reiniciada ainda nao possui
`Product` e `Content` tecnicamente validados. Essa e uma protecao intencional:
arquivos novos nao sao promovidos a disponiveis apenas por existirem no Drive.
As pastas devem ser confirmadas na tela **Drive Product Mapping**, e cada MP4
deve passar pelo fluxo de importacao e `ffprobe` antes de entrar no estoque.

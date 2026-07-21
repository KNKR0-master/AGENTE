# Workflow atual do usuario - Google Flow + CapCut + TikTok Shop

Data de registro: 2026-07-16

Este e o fluxo real ja usado pelo usuario para dar continuidade em produtos validados. Ele deve ser tratado como base operacional antes de criarmos automacoes mais agressivas.

## Fluxo atual

1. Usar imagem de ambiente ja existente como referencia.
2. Manter a referencia principal e mudar apenas o ambiente.
3. Aplicar prompts padrao ja validados para gerar frames em cima dessas referencias.
4. Filtrar interferencias de imagem, inconsistencias de audio, deformacoes, simbolos e outros artefatos.
5. Baixar todos os outputs aprovados.
6. Subir os arquivos no CapCut.
7. Editar no CapCut:
   - remover ruidos sonoros;
   - cortar onde termina a cena/fala;
   - remover trechos ruins;
   - ajustar continuidade.
8. Salvar arquivo final.
9. Usar o Gera Frames para gerar aproximadamente 18 videos por rodada.
10. Salvar no Drive apenas o resultado exportado do Gera Frames, dentro da pasta do produto.
11. Normalmente gerar 2 ambientes diferentes por produto para otimizar e criar videos diferentes.
12. Fazer edicoes finais.
13. Publicar no TikTok Shop.

## Principio do fluxo

O ganho vem de padronizar o que ja funciona:

- mesma logica de criativo;
- variacao controlada de ambiente;
- prompts validados;
- filtro humano de qualidade;
- CapCut como acabamento final;
- Gera Frames como ferramenta separada de multiplicacao por frames;
- Drive apenas como armazenamento e rastreio dos arquivos exportados;
- 2 variacoes por produto para aumentar aprendizado sem dobrar todo o trabalho.

## Mapa de automacao

| Etapa | Pode automatizar agora? | Como |
|---|---|---|
| Selecionar produto validado | Sim | Usar score/decisao dos relatorios. |
| Escolher 2 ambientes | Parcial | Sugerir ambientes por categoria/produto; usuario aprova. |
| Preparar prompts padrao | Sim | Gerar prompts com variaveis de produto, ambiente e restricoes. |
| Criar checklist de qualidade | Sim | Aplicar lista fixa de interferencias e riscos. |
| Gerar frames no Flow | Parcial | Codex prepara prompt e pode disparar `low priority` sem credito quando assets/ambiente ja estiverem aprovados. |
| Gera Frames | Parcial | Registrar lote, prompt padrao, imagem base e quantidade gerada/aprovada. |
| Filtrar outputs ruins | Parcial | Checklist assistido; revisao visual humana ainda necessaria. |
| Baixar outputs | Semi-manual | Confirmar download/export; depois registrar arquivos. |
| Subir no CapCut | Manual no inicio | Pode virar assistido depois, mas envolve app/arquivos. |
| Remover ruidos/cortes | Parcial | Criar checklist de edicao e pontos de corte; edicao humana por enquanto. |
| Salvar final | Semi-manual | Padrao de nome e pasta pode ser automatizado. |
| Publicar no TikTok Shop | Manual | Publicacao e conta real continuam com aprovacao humana. |
| Registrar resultado | Sim | Registrar video_id, produto_id, ambiente, prompt e metricas. |

## Automacoes seguras para primeira fase

- Criar briefing por produto.
- Gerar 2 ambientes recomendados por produto.
- Transformar prompts padrao em prompts finais.
- Criar matriz `produto x ambiente x prompt`.
- Criar checklist de revisao visual/audio.
- Criar nomes de arquivos e pastas.
- Criar registro dos outputs gerados.
- Criar registro de lotes de aproximadamente 18 videos.
- Armazenar no Drive os resultados exportados pelo Gera Frames.
- Gerar pauta de edicao para CapCut.
- Registrar quais videos foram publicados e acompanhar metricas.

## Automacoes sensiveis

Exigem confirmacao do usuario:

- gerar no Flow em modo pago/rapido que consome creditos;
- fazer upload de imagem/video;
- baixar arquivos;
- abrir/manipular CapCut;
- publicar no TikTok Shop;
- alterar descricao, produto vinculado, preco, cupom ou configuracao da publicacao.

## Padrao de 2 ambientes

Para cada produto aprovado:

```text
Produto:
Ambiente A:
Ambiente B:
Prompt base:
Prompt padrao version:
Imagem base version:
Frame set:
Lote A:
Lote B:
Quantidade gerada:
Quantidade aprovada:
Quantidade descartada:
Prompt ambiente A:
Prompt ambiente B:
Output A:
Output B:
Arquivo CapCut A:
Arquivo CapCut B:
Arquivo final A:
Arquivo final B:
Status publicacao A:
Status publicacao B:
Metricas A:
Metricas B:
```

Arquivo detalhado:

- `MULTIPLICACAO_FRAMES.md`

## Checklist de filtro visual

Reprovar ou editar se houver:

- produto deformado;
- embalagem ilegivel;
- texto estranho na embalagem;
- simbolos indesejados;
- marca ou logo inventado;
- mao/dedo deformado;
- objeto flutuando;
- ambiente incoerente;
- transicao brusca;
- cena que nao combina com produto;
- promessa visual exagerada;
- antes/depois enganoso;
- qualquer claim sensivel em tela.

## Checklist de audio

Reprovar ou editar se houver:

- ruido alto;
- fala cortada;
- fala artificial demais;
- pronuncia errada do produto;
- silencio estranho;
- musica competindo com voz;
- trecho que termina antes da cena;
- cena que continua depois da fala acabar sem intencao.

## Mitigacao economica de erros

Para evitar que inconsistencias breves passem despercebidas sem gastar muitos tokens:

1. Rodar checagem tecnica local nos arquivos gerados.
2. Extrair poucos frames-chave por video.
3. Usar revisao visual curta apenas nos frames.
4. Descartar outputs ruins antes do CapCut.
5. Usar prompt corretivo apenas quando a falha for clara.

Arquivo detalhado:

- `REVISAO_ECONOMICA_AUDIO_VIDEO.md`

## Padrao de naming

```text
produto-{produto_id}_{slug}_amb-{ambiente}_v{numero}_{status}.mp4
```

Exemplo:

```text
produto-1732849645859341976_tetra-magnesio_amb-cozinha-v1_final.mp4
produto-1732849645859341976_tetra-magnesio_amb-escrivaninha-v1_final.mp4
```

## Decisao operacional

Neste momento, automatizar preparacao, variacoes, registro, checklist e geracao `low priority` sem credito quando o produto, ambiente e prompt ja estiverem aprovados.

Manter humano no loop para upload sensivel, modo pago/rapido, finalistas visuais, CapCut e publicacao ate termos pelo menos 5 produtos e 10 videos finalizados com esse processo.

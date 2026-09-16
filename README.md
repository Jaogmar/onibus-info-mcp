# MCP `onibus-info` — ônibus de Joinville/SC

Servidor [MCP](https://modelcontextprotocol.io) que dá ao Claude acesso aos dados de
transporte público de **Joinville / Santa Catarina**, consumindo a API pública de
[onibus.info](https://onibus.info) (backend OpenTripPlanner + GTFS).

Com ele dá para perguntar em linguagem natural:

> "Qual a melhor rota do Terminal Tupy até a Avenida Edgar Nelsom Meister, 474, saindo às 7h?"
> "Que horas passa a 0130 no Terminal Norte no sábado?"
> "Quais linhas param perto de -26.2920, -48.8142?"

## Instalação

Requer [uv](https://docs.astral.sh/uv/) e Python 3.11+.

```powershell
cd D:\git\faculdade\mcp-onibus
uv sync
```

## Registro no Claude Code (escopo de usuário)

```powershell
claude mcp add --scope user onibus-info -- uv run --directory D:\git\faculdade\mcp-onibus python -m onibus_mcp.server
```

O `--directory` é o que permite usar o servidor a partir de qualquer pasta.
Confira com `claude mcp list` (deve aparecer `onibus-info: connected`).

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `planejar_rota` | Itinerários completos entre dois pontos, com baldeações, linhas e horários |
| `buscar_local` | Busca paradas, terminais e endereços por nome parcial |
| `geocodificar_endereco` | Converte endereço em lat/lon (avisa se estiver fora de Joinville) |
| `listar_linhas` | Lista/filtra todas as linhas da cidade |
| `sentidos_da_linha` | Ida/volta de uma linha e o `shape_id` de cada sentido |
| `horarios_linha` | Quadro de horários fixo (dias úteis / sábado / domingo), já filtrado |
| `paradas_da_linha` | Sequência completa de paradas de um trajeto (`shape_id`) |
| `linhas_na_parada` | Quais linhas atendem uma parada |
| `proximas_saidas` | Próximas saídas previstas em uma parada |
| `paradas_proximas` | Paradas mais próximas de uma coordenada, com distância |
| `detalhe_parada` | Nome, coordenadas e terminal-pai de uma parada |

### Fluxo típico

1. `buscar_local` / `geocodificar_endereco` para confirmar origem e destino
2. `planejar_rota` para as opções
3. `horarios_linha`, `sentidos_da_linha` + `paradas_da_linha` para aprofundar

## API consumida

Nenhum endpoint exige autenticação — basta `User-Agent` de navegador e
`Referer: https://onibus.info/`.

| Endpoint | Método | Conteúdo |
|---|---|---|
| `/api/search/stops` | POST `{term, limit}` | paradas e endereços por texto |
| `/api/geocode/coords` | POST `{address}` | geocodificação (lat/lng + componentes) |
| `/api/directions` | GET | plano de viagem OTP (`plan.itineraries[]`) |
| `/api/routes` | GET | todas as linhas |
| `/api/routetrips/{linha}` | GET | sentidos e `shape_id` |
| `/api/timetable/{linha}` | GET | quadro de horários por sentido/parada/serviço |
| `/api/shapestops/{shape_id}` | GET | paradas do trajeto em ordem |
| `/api/stoproutes/{parada}` | GET | linhas que atendem a parada |
| `/api/stoptimes/{parada}` | GET | próximas saídas |
| `/api/stopsnear?lat=&lng=` | GET | paradas próximas com distância |
| `/api/stops/{parada}` | GET | detalhe da parada |

### Detalhes de implementação

- **Fuso horário**: tudo em `America/Sao_Paulo`. O parâmetro `time` de
  `/api/directions` é **UTC**, apesar da interface do site mostrar hora local —
  a conversão é feita em `client.para_utc_iso`.
- **Cache**: dados estáticos do GTFS (`/api/routes`, `/api/timetable`,
  `/api/shapestops`, `/api/routetrips`, `/api/stops`, `/api/stoproutes`) ficam
  1 hora em memória. Rotas, próximas saídas e buscas nunca são cacheadas.
- **Resiliência**: 3 tentativas com backoff para timeout e 5xx; erros viram
  mensagens em português, nunca traceback.
- **Economia de tokens**: geometrias (`legGeometry`), instruções de caminhada
  (`steps`) e `debugOutput` são descartadas; tudo é devolvido como Markdown curto.
- **Tipos de dia do GTFS local**: `DU` (dias úteis), `SAB`, `DOM`.

## Estrutura

```
src/onibus_mcp/
├── client.py      # HTTP, cache, retry e helpers de data/hora
├── formatters.py  # JSON do OTP/GTFS -> Markdown enxuto
└── server.py      # FastMCP + as 11 ferramentas
```

## Limitações

- Cobertura **apenas de Joinville/SC**.
- Dados não oficiais, obtidos de uma API pública não documentada — sujeita a mudança.
- `proximas_saidas` depende de previsão ativa no site; fora do horário de operação
  volta vazio (use `horarios_linha` para o quadro fixo).

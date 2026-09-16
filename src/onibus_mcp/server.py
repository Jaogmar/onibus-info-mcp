"""Servidor MCP `onibus-info` — transporte publico de Joinville/SC.

Expoe a API publica de https://onibus.info como ferramentas MCP, para
planejar rotas, consultar linhas, paradas e quadros de horario.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import client as api
from .formatters import formatar_plano, formatar_quadro_horarios, metros, tabela

INSTRUCOES = """\
Dados de transporte publico de **Joinville / Santa Catarina** (fonte: onibus.info).
Nao ha cobertura de outras cidades.

Fluxo recomendado:
1. Em caso de duvida sobre o endereco, confirme com `buscar_local` ou
   `geocodificar_endereco`.
2. Use `planejar_rota` para obter os itinerarios (ele aceita endereco livre
   ou "lat,lon" e ja compara as opcoes).
3. Para aprofundar, use `horarios_linha` (quadro fixo), `sentidos_da_linha`
   + `paradas_da_linha` (itinerario completo) e `linhas_na_parada`.

Horarios sao sempre no fuso America/Sao_Paulo.
"""

mcp = MCPServer("onibus-info", instructions=INSTRUCOES, version="0.1.0")

# Identificador do Google Analytics que o site envia; a API aceita qualquer valor.
_GID = "1369214577.1789527924"


def _num(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 1. Planejamento de rota
# --------------------------------------------------------------------------


@mcp.tool()
async def planejar_rota(
    origem: str,
    destino: str,
    horario: str = "agora",
    chegar_ate: bool = False,
    max_caminhada_m: int = 5000,
    modos: str = "TRANSIT,WALK",
    max_itinerarios: int = 3,
) -> str:
    """Planeja as melhores rotas de onibus entre dois pontos em Joinville/SC.

    Args:
        origem: endereco, nome de parada/terminal ou "lat,lon".
        destino: idem.
        horario: "agora", "08:30", "17/09/2026 08:30" ou ISO 8601 (hora local).
        chegar_ate: se True, `horario` e a hora de CHEGADA desejada.
        max_caminhada_m: distancia maxima a pe, em metros.
        modos: modos do OTP, ex. "TRANSIT,WALK" ou "WALK".
        max_itinerarios: quantas opcoes retornar (1 a 5).
    """
    quando = api.parse_horario(horario)
    params = {
        "gid": _GID,
        "userPlace": "null",
        "fromPlace": origem,
        "toPlace": destino,
        "time": api.para_utc_iso(quando),
        "arriveBy": "true" if chegar_ate else "false",
        "mode": modos,
        "maxWalkDistance": max_caminhada_m,
    }
    dados = await api.get_json("/api/directions", params)
    if isinstance(dados, dict) and dados.get("error"):
        erro = dados["error"]
        detalhe = erro.get("msg") if isinstance(erro, dict) else erro
        return (
            f"onibus.info nao conseguiu planejar a rota: {detalhe}. "
            "Confira origem e destino com `buscar_local`."
        )
    texto = formatar_plano(dados, origem, destino, max(1, min(max_itinerarios, 5)))
    referencia = "chegada ate" if chegar_ate else "partida a partir de"
    return f"_Consulta para {referencia} {quando:%d/%m/%Y %H:%M} (Joinville)._\n\n{texto}"


# --------------------------------------------------------------------------
# 2-3. Busca e geocodificacao
# --------------------------------------------------------------------------


@mcp.tool()
async def buscar_local(termo: str, limite: int = 6) -> str:
    """Busca paradas, terminais e enderecos de Joinville por nome parcial."""
    dados = await api.post_json(
        "/api/search/stops", {"term": termo, "limit": max(1, min(limite, 20))}
    )
    if not dados:
        return f"Nenhum local encontrado para {termo!r} em Joinville."
    linhas = [
        [
            item.get("id", "?"),
            item.get("name", "?"),
            "terminal/estacao" if str(item.get("location_type")) == "1" else "parada",
            f"{item.get('stop_lat')},{item.get('stop_lon')}",
        ]
        for item in dados
    ]
    return tabela(["id", "nome", "tipo", "lat,lon"], linhas)


@mcp.tool()
async def geocodificar_endereco(endereco: str) -> str:
    """Converte um endereco em coordenadas (lat/lon) usando o geocoder do site."""
    dados = await api.post_json("/api/geocode/coords", {"address": endereco})
    if not dados:
        return f"Endereco {endereco!r} nao encontrado."
    partes = []
    for item in dados[:5]:
        comp = item.get("components", {}) or {}
        cidade = comp.get("city") or comp.get("county") or "?"
        aviso = (
            "\n  ⚠ Fora de Joinville — a API de rotas nao cobre esta cidade."
            if cidade != "Joinville"
            else ""
        )
        extras = ""
        if comp.get("district"):
            extras += f" · {comp['district']}"
        if comp.get("postcode"):
            extras += f" · CEP {comp['postcode']}"
        partes.append(
            f"- **{item.get('address', '?')}**\n"
            f"  `{item.get('lat')},{item.get('lng')}` · {cidade}{extras}{aviso}"
        )
    return "\n".join(partes)


# --------------------------------------------------------------------------
# 4-7. Linhas
# --------------------------------------------------------------------------


@mcp.tool()
async def listar_linhas(filtro: str | None = None, limite: int = 60) -> str:
    """Lista as linhas de onibus de Joinville, opcionalmente filtrando por texto."""
    dados = await api.get_json("/api/routes")
    if filtro:
        alvo = filtro.strip().lower()
        dados = [
            r
            for r in dados
            if alvo in str(r.get("route_id", "")).lower()
            or alvo in str(r.get("route_long_name", "")).lower()
            or alvo in str(r.get("route_station") or "").lower()
        ]
    if not dados:
        return f"Nenhuma linha encontrada para o filtro {filtro!r}."
    total = len(dados)
    recorte = dados[:limite]
    corpo = tabela(
        ["linha", "nome", "terminal"],
        [
            [
                r.get("route_id", "?"),
                r.get("route_long_name", "?"),
                r.get("route_station") or "-",
            ]
            for r in recorte
        ],
    )
    rodape = (
        f"\n\n_Mostrando {len(recorte)} de {total} linhas. Use `filtro` para refinar._"
        if total > len(recorte)
        else f"\n\n_{total} linha(s)._"
    )
    return corpo + rodape


@mcp.tool()
async def sentidos_da_linha(linha: str) -> str:
    """Mostra os sentidos (ida/volta) de uma linha e o `shape_id` de cada um.

    O `shape_id` e a entrada para `paradas_da_linha`.
    """
    dados = await api.get_json(f"/api/routetrips/{linha.strip()}")
    if not dados:
        return f"Linha {linha!r} nao encontrada. Use `listar_linhas` para ver os codigos."
    nome = dados[0].get("route_long_name", "")
    corpo = tabela(
        ["sentido", "direction_id", "shape_id", "destino (headsign)"],
        [
            [
                "Ida" if str(d.get("direction_id")) == "0" else "Volta",
                d.get("direction_id"),
                d.get("shape_id", "?"),
                d.get("trip_headsign", "?"),
            ]
            for d in dados
        ],
    )
    return f"**Linha {linha} — {nome}**\n\n{corpo}"


@mcp.tool()
async def horarios_linha(
    linha: str,
    sentido: str | None = None,
    parada_id: str | None = None,
    tipo_dia: str | None = None,
    a_partir_de: str | None = None,
    limite: int = 40,
) -> str:
    """Quadro de horarios de uma linha (horarios fixos do GTFS).

    Args:
        linha: codigo da linha, ex. "0130".
        sentido: "ida"/"volta" ou "0"/"1". Sem valor, usa o primeiro sentido.
        parada_id: parada de referencia. Sem valor, usa a primeira do sentido.
        tipo_dia: "DU" (dias uteis), "SAB" ou "DOM". Sem valor, usa o dia de hoje.
        a_partir_de: corta horarios anteriores a "HH:MM" ("agora" e aceito).
        limite: maximo de horarios exibidos.
    """
    dados = await api.get_json(f"/api/timetable/{linha.strip()}")
    if not dados:
        return f"Sem quadro de horarios para a linha {linha!r}."

    if sentido is None:
        bloco = dados[0]
    else:
        alvo = sentido.strip().lower()
        direction_id = {"ida": "0", "volta": "1", "0": "0", "1": "1"}.get(alvo)
        bloco = next(
            (
                d
                for d in dados
                if str(d.get("direction_id")) == direction_id
                or str(d.get("direction", "")).lower() == alvo
            ),
            None,
        )
        if bloco is None:
            disponiveis = ", ".join(
                f"{d.get('direction')} (id {d.get('direction_id')})" for d in dados
            )
            return (
                f"Sentido {sentido!r} nao existe na linha {linha}. "
                f"Disponiveis: {disponiveis}."
            )

    paradas = bloco.get("stop_data") or []
    if not paradas:
        return (
            f"O sentido {bloco.get('direction')} da linha {linha} "
            "nao tem paradas com horario publicado."
        )
    if parada_id:
        parada = next(
            (p for p in paradas if str(p.get("stop_id")) == str(parada_id)), None
        )
        if parada is None:
            opcoes = ", ".join(
                f"{p.get('stop_id')} ({p.get('stop_name')})" for p in paradas
            )
            return (
                f"Parada {parada_id!r} nao esta no quadro dessa linha. "
                f"Disponiveis: {opcoes}."
            )
    else:
        parada = paradas[0]

    servico = api.normaliza_tipo_dia(tipo_dia)
    dados_servico = next(
        (s for s in parada.get("service_data", []) if s.get("service_id") == servico),
        None,
    )
    if dados_servico is None:
        existentes = ", ".join(
            s.get("service_id", "?") for s in parada.get("service_data", [])
        )
        return f"Sem horarios de {servico} nessa parada. Tipos disponiveis: {existentes}."

    horarios = sorted(
        t["departure_time"]
        for bloco_hora in dados_servico.get("time_data", [])
        for t in bloco_hora
        if t.get("departure_time")
    )
    if a_partir_de:
        corte = api.parse_horario(a_partir_de).strftime("%H:%M")
        horarios = [h for h in horarios if h >= corte]

    outras = [
        f"{p.get('stop_id')} ({p.get('stop_name')})" for p in paradas if p is not parada
    ]
    titulo = (
        f"**Linha {linha} · {bloco.get('direction')} · "
        f"{dados_servico.get('service_name', servico)}**\n"
        f"Saidas de: {parada.get('stop_name')} (id {parada.get('stop_id')}) · "
        f"{len(horarios)} horario(s)"
    )
    texto = formatar_quadro_horarios(horarios, titulo, limite)
    if outras:
        texto += (
            "\n\n_Outras paradas com quadro neste sentido: " + "; ".join(outras) + "._"
        )
    return texto


@mcp.tool()
async def paradas_da_linha(shape_id: str) -> str:
    """Lista, em ordem, todas as paradas do trajeto de um sentido da linha.

    O `shape_id` vem de `sentidos_da_linha` (ex.: "0130-0").
    """
    dados = await api.get_json(f"/api/shapestops/{shape_id.strip()}")
    if not dados:
        return (
            f"Nenhuma parada para o trajeto {shape_id!r}. "
            "Confira o shape_id com `sentidos_da_linha`."
        )
    linhas = [
        [
            p.get("stop_order", "?"),
            p.get("stop_id", "?"),
            p.get("stop_name", "?"),
            metros(_num(p.get("shape_dist_traveled")) or 0),
        ]
        for p in dados
    ]
    return (
        f"**Trajeto {shape_id} — {len(dados)} paradas**\n\n"
        + tabela(["#", "id", "parada", "dist. do inicio"], linhas)
    )


# --------------------------------------------------------------------------
# 8-11. Paradas
# --------------------------------------------------------------------------


@mcp.tool()
async def linhas_na_parada(parada_id: str) -> str:
    """Mostra quais linhas atendem uma parada."""
    dados = await api.get_json(f"/api/stoproutes/{parada_id.strip()}")
    if not dados:
        return f"Nenhuma linha registrada na parada {parada_id!r}."
    return tabela(
        ["linha", "shape_id", "destino (headsign)"],
        [
            [
                r.get("route_id", "?"),
                r.get("shape_id", "?"),
                r.get("trip_headsign", "?"),
            ]
            for r in dados
        ],
    )


@mcp.tool()
async def proximas_saidas(parada_id: str) -> str:
    """Proximas saidas previstas em uma parada (quando o site tem previsao ativa)."""
    dados = await api.get_json(f"/api/stoptimes/{parada_id.strip()}")
    if not dados:
        return (
            f"Sem saidas previstas agora para a parada {parada_id}. "
            "Isso e normal fora do horario de operacao. "
            "Use `horarios_linha` para consultar o quadro fixo."
        )
    linhas = []
    for item in dados:
        horario = (
            item.get("departure_time")
            or item.get("live_time")
            or item.get("start_time")
            or "?"
        )
        linhas.append(
            [
                item.get("route_id", "?"),
                item.get("trip_headsign") or item.get("headsign") or "-",
                horario,
                item.get("start_time_diff", "-"),
            ]
        )
    return tabela(["linha", "destino", "saida", "faltam"], linhas)


@mcp.tool()
async def paradas_proximas(lat: float, lng: float, limite: int = 10) -> str:
    """Lista as paradas mais proximas de uma coordenada, com distancia em metros."""
    dados = await api.get_json("/api/stopsnear", {"lat": lat, "lng": lng})
    if not dados:
        return f"Nenhuma parada perto de {lat},{lng} (a cobertura e so Joinville/SC)."
    dados = sorted(dados, key=lambda p: _num(p.get("distance")) or 1e9)[:limite]
    return tabela(
        ["id", "parada", "distancia", "tipo"],
        [
            [
                p.get("stop_id", "?"),
                p.get("stop_name", "?"),
                f"{round(_num(p.get('distance')) or 0)} m",
                "terminal/estacao" if str(p.get("location_type")) == "1" else "parada",
            ]
            for p in dados
        ],
    )


@mcp.tool()
async def detalhe_parada(parada_id: str) -> str:
    """Detalhes de uma parada: nome, coordenadas e terminal ao qual pertence."""
    dados = await api.get_json(f"/api/stops/{parada_id.strip()}")
    if not dados:
        return f"Parada {parada_id!r} nao encontrada."
    p = dados[0]
    tipo = "terminal/estacao" if str(p.get("location_type")) == "1" else "parada"
    texto = (
        f"**{p.get('stop_name', '?')}** (id {p.get('stop_id')})\n"
        f"- Coordenadas: `{p.get('stop_lat')},{p.get('stop_lon')}`\n"
        f"- Tipo: {tipo}"
    )
    if p.get("parent_station"):
        texto += f"\n- Pertence ao terminal de id {p['parent_station']}"
    return texto


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

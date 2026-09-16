"""Transforma o JSON cru do OTP/GTFS em texto Markdown curto.

Tudo aqui existe para economizar tokens: descartamos geometrias (`legGeometry`),
instrucoes passo a passo de caminhada (`steps`) e `debugOutput`.
"""

from __future__ import annotations

from typing import Any

from .client import hora_local, ms_para_local

_MODOS = {
    "WALK": "\U0001f6b6 A pe",
    "BUS": "\U0001f68c",
    "TRAM": "\U0001f68b",
    "RAIL": "\U0001f686",
    "SUBWAY": "\U0001f687",
    "FERRY": "\u26f4",
    "BICYCLE": "\U0001f6b2",
    "CAR": "\U0001f697",
}


def _min(segundos: float) -> str:
    total = round(segundos / 60)
    if total < 60:
        return f"{total} min"
    return f"{total // 60}h{total % 60:02d}"


def _metros(m: float) -> str:
    return f"{round(m)} m" if m < 1000 else f"{m / 1000:.1f} km".replace(".", ",")


_NOMES_OTP = {"Origin": "origem informada", "Destination": "destino informado"}


def _nome_ponto(ponto: dict[str, Any]) -> str:
    nome = ponto.get("name", "?")
    return _NOMES_OTP.get(nome, nome)


def _perna_a_pe(leg: dict[str, Any]) -> str:
    destino = _nome_ponto(leg.get("to", {}))
    return (
        f"  \U0001f6b6 A pe {_min(leg.get('duration', 0))} "
        f"({_metros(leg.get('distance', 0))}) ate **{destino}**"
    )


def _perna_transporte(leg: dict[str, Any]) -> str:
    icone = _MODOS.get(leg.get("mode", ""), "\U0001f68c")
    linha = leg.get("routeShortName") or leg.get("route") or "?"
    nome = leg.get("routeLongName") or ""
    headsign = leg.get("headsign") or ""
    origem = leg.get("from", {})
    destino = leg.get("to", {})
    paradas = (destino.get("stopSequence") or 0) - (origem.get("stopSequence") or 0)

    cabecalho = f"  {icone} **Linha {linha}**"
    if nome:
        cabecalho += f" \u2014 {nome}"
    if headsign:
        cabecalho += f" (sentido: {headsign})"

    detalhe = (
        f"     {hora_local(origem.get('departure', leg['startTime']))} "
        f"{origem.get('name', '?')} \u2192 "
        f"{hora_local(destino.get('arrival', leg['endTime']))} "
        f"{destino.get('name', '?')}"
    )
    extras = [_min(leg.get("duration", 0))]
    if paradas > 0:
        extras.append(f"{paradas} paradas")
    if leg.get("agencyName"):
        extras.append(leg["agencyName"])
    if leg.get("realTime"):
        atraso = round((leg.get("departureDelay") or 0) / 60)
        extras.append(
            "\u23f1 tempo real"
            + (f", {atraso:+d} min" if atraso else "")
        )
    detalhe += f" ({', '.join(extras)})"
    return f"{cabecalho}\n{detalhe}"


def formatar_itinerario(it: dict[str, Any], indice: int) -> str:
    inicio = ms_para_local(it["startTime"])
    fim = ms_para_local(it["endTime"])
    baldeacoes = it.get("transfers", 0)
    rotulo_bald = (
        "direto" if baldeacoes <= 0
        else f"{baldeacoes} baldeacao" if baldeacoes == 1
        else f"{baldeacoes} baldeacoes"
    )
    linhas_usadas = [
        (l.get("routeShortName") or l.get("route") or "?")
        for l in it.get("legs", [])
        if l.get("transitLeg")
    ]
    cabecalho = (
        f"### Opcao {indice}: {inicio:%H:%M} \u2192 {fim:%H:%M} "
        f"({_min(it.get('duration', 0))})\n"
        f"{inicio:%d/%m/%Y} \u00b7 {rotulo_bald} \u00b7 "
        f"linhas: {' + '.join(linhas_usadas) or 'nenhuma'} \u00b7 "
        f"{_metros(it.get('walkDistance', 0))} a pe \u00b7 "
        f"espera {_min(it.get('waitingTime', 0))}"
    )
    corpo = [
        _perna_a_pe(leg) if not leg.get("transitLeg") else _perna_transporte(leg)
        for leg in it.get("legs", [])
    ]
    return cabecalho + "\n" + "\n".join(corpo)


def formatar_plano(
    dados: dict[str, Any], origem: str, destino: str, limite: int
) -> str:
    plano = dados.get("plan") or {}
    itinerarios = plano.get("itineraries") or []
    if not itinerarios:
        return (
            f"Nenhum itinerario de onibus encontrado entre **{origem}** e "
            f"**{destino}** nesse horario.\n\n"
            "Possiveis causas: um dos enderecos esta fora de Joinville/SC, "
            "o horario nao tem operacao, ou o endereco nao foi reconhecido. "
            "Use `buscar_local` ou `geocodificar_endereco` para conferir os enderecos, "
            "ou aumente `max_caminhada_m`."
        )

    itinerarios = itinerarios[:limite]
    melhor_tempo = min(itinerarios, key=lambda i: i.get("duration", 1e9))
    menos_bald = min(
        itinerarios, key=lambda i: (i.get("transfers", 9), i.get("duration", 1e9))
    )

    partes = [f"## {origem} \u2192 {destino}", ""]
    for i, it in enumerate(itinerarios, start=1):
        partes.append(formatar_itinerario(it, i))
        partes.append("")

    cedo = min(itinerarios, key=lambda i: i.get("endTime", 9e18))
    idx_rapido = itinerarios.index(melhor_tempo) + 1
    idx_bald = itinerarios.index(menos_bald) + 1
    idx_cedo = itinerarios.index(cedo) + 1
    resumo = [
        f"**Menor tempo de viagem:** opcao {idx_rapido} "
        f"({_min(melhor_tempo['duration'])})."
    ]
    if idx_cedo != idx_rapido:
        resumo.append(
            f"**Chega antes:** opcao {idx_cedo} "
            f"({ms_para_local(cedo['endTime']):%H:%M})."
        )
    if idx_bald != idx_rapido:
        resumo.append(
            f"**Menos baldeacoes:** opcao {idx_bald} "
            f"({menos_bald.get('transfers', 0)}, {_min(menos_bald['duration'])})."
        )
    partes.append(" ".join(resumo))
    return "\n".join(partes)


def formatar_quadro_horarios(
    horarios: list[str], titulo: str, limite: int
) -> str:
    """Agrupa uma lista de 'HH:MM' por hora, respeitando um limite."""
    if not horarios:
        return f"{titulo}\n\n_Nenhum horario para os filtros informados._"
    truncado = len(horarios) > limite
    horarios = horarios[:limite]
    por_hora: dict[str, list[str]] = {}
    for h in horarios:
        por_hora.setdefault(h.split(":")[0], []).append(h)
    linhas = [f"`{hora}h` {'  '.join(hs)}" for hora, hs in por_hora.items()]
    rodape = (
        f"\n\n_Mostrando os {limite} primeiros horarios; aumente `limite` para ver mais._"
        if truncado
        else ""
    )
    return f"{titulo}\n\n" + "\n".join(linhas) + rodape


def metros(m: float) -> str:
    """Distancia legivel (usada tambem pelo server)."""
    return _metros(m)


def tabela(cabecalhos: list[str], linhas: list[list[str]]) -> str:
    sep = "|" + "|".join(["---"] * len(cabecalhos)) + "|"
    corpo = ["| " + " | ".join(cabecalhos) + " |", sep]
    corpo += ["| " + " | ".join(str(c) for c in linha) + " |" for linha in linhas]
    return "\n".join(corpo)

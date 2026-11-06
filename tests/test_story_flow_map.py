from story_flow_map import search_story_flow_map, serialize_story_flow_match


def test_story_flow_map_surfaces_pagamentos_transferencias_seed():
    result = search_story_flow_map(
        objective="Confirmar uma transferência recorrente com CTA primário e resumo final.",
        context="Fluxo de pagamentos recorrentes e transferências.",
        epic_or_feature="Transferências recorrentes",
        dominant_domain="Pagamentos",
        top=4,
    )

    assert result["matches"]
    labels = [
        (
            str(item.get("domain", "") or ""),
            str(item.get("journey", "") or ""),
            str(item.get("flow", "") or ""),
        )
        for item in result["matches"]
    ]
    assert any(domain == "Pagamentos" and ("transfer" in journey.lower() or "recorr" in flow.lower()) for domain, journey, flow in labels)


def test_serialize_story_flow_match_avoids_repeated_title_segments():
    result = search_story_flow_map(
        objective="Dashboard com resumo e agenda.",
        context="Cards principais e agenda.",
        epic_or_feature="Dashboard",
        dominant_domain="Dashboard",
        top=3,
    )

    assert result["matches"]
    serialized = [serialize_story_flow_match(item) for item in result["matches"]]
    assert any(item["title"] == "agenda" or "Agenda" in item["title"] for item in serialized)
    assert all(" · " not in item["title"] or len({part.strip().lower() for part in item["title"].split(" · ")}) > 1 for item in serialized)


def test_story_flow_map_surfaces_documentos_upload_seed():
    result = search_story_flow_map(
        objective="Submeter e consultar documentos digitais com upload e consulta.",
        context="Fluxo documental do site.",
        epic_or_feature="Documentos digitais",
        dominant_domain="Documentos",
        top=4,
    )

    assert result["matches"]
    labels = [
        (
            str(item.get("domain", "") or ""),
            str(item.get("journey", "") or ""),
            str(item.get("flow", "") or ""),
        )
        for item in result["matches"]
    ]
    assert any(domain == "Documentos" and ("upload" in flow.lower() or "digital" in flow.lower()) for domain, journey, flow in labels)


def test_story_flow_map_surfaces_operacoes_assinatura_digital_seed():
    result = search_story_flow_map(
        objective="Autorizar operação pendente com assinatura digital.",
        context="Fluxo de operações pendentes e assinatura digital.",
        epic_or_feature="Assinatura digital",
        dominant_domain="",
        top=4,
    )

    assert result["matches"]
    labels = [
        (
            str(item.get("domain", "") or ""),
            str(item.get("journey", "") or ""),
            str(item.get("flow", "") or ""),
        )
        for item in result["matches"]
    ]
    assert any(domain == "Operações" and "digitalsignature" in flow.lower() for domain, journey, flow in labels)

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_user_story_lane_backend(monkeypatch):
    import user_story_lane

    blobs = {}
    rows = {}
    search_index_docs = {}

    async def _blob_upload_json(container: str, blob_name: str, payload: dict):
        ref = f"{container}/{blob_name}"
        blobs[ref] = payload
        return {"blob_ref": ref, "container": container, "blob_name": blob_name}

    async def _blob_download_json(container: str, blob_name: str):
        return blobs.get(f"{container}/{blob_name}")

    async def _table_insert(table_name: str, entity: dict):
        rows.setdefault(table_name, {})[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)
        return True

    async def _table_merge(table_name: str, entity: dict):
        table = rows.setdefault(table_name, {})
        key = (entity["PartitionKey"], entity["RowKey"])
        current = dict(table.get(key, {}))
        current.update(entity)
        table[key] = current

    async def _table_query(table_name: str, filter_str: str = "", top: int = 50):
        values = list(rows.get(table_name, {}).values())
        partition_match = None
        row_match = None
        if "PartitionKey eq '" in filter_str:
            partition_match = filter_str.split("PartitionKey eq '", 1)[1].split("'", 1)[0].replace("''", "'")
        if "RowKey eq '" in filter_str:
            row_match = filter_str.split("RowKey eq '", 1)[1].split("'", 1)[0].replace("''", "'")
        filtered = []
        for item in values:
            if partition_match is not None and item.get("PartitionKey") != partition_match:
                continue
            if row_match is not None and item.get("RowKey") != row_match:
                continue
            filtered.append(dict(item))
        return filtered[:top]

    async def _tool_search_workitems(query, top=30, filter_expr=None):
        _ = filter_expr
        return {
            "items": [
                {
                    "id": "WI-1001",
                    "title": "ACME | Pagamentos | Transferências | Recorrências | Configurar transferência recorrente",
                    "content": f"Story semelhante a {query} com CTA primário e validações de recorrência.",
                    "url": "https://dev.azure.com/mock/_workitems/edit/1001",
                    "score": 0.93,
                }
            ][:top]
        }

    async def _tool_query_workitems(wiql_where, fields=None, top=200, user_sub: str = ""):
        _ = (fields, top, user_sub)
        where = str(wiql_where or "")
        if "[System.Id] = 100513 OR [System.Id] = 100123" in where or "[System.Id] = 100123 OR [System.Id] = 100513" in where:
            return {
                "items": [
                    {
                        "id": 100513,
                        "type": "Feature",
                        "title": "ACME — Pagamentos — Transferências recorrentes",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                        "description": "Feature de transferências recorrentes.",
                    },
                    {
                        "id": 100123,
                        "type": "User Story",
                        "title": "ACME — Pagamentos — Transferências recorrentes — resumo da operação",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100123",
                        "description": "Story de proveniência citada no corpus curado.",
                    },
                ]
            }
        if "[System.WorkItemType] = 'Epic'" in where and "Pagamentos" in where:
            return {
                "items": [
                    {
                        "id": 100886,
                        "type": "Epic",
                        "title": "ACME — Pagamentos",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100886",
                        "description": "Epic de pagamentos.",
                    }
                ]
            }
        if "[System.WorkItemType] = 'Feature'" in where and "Transferências recorrentes" in where:
            return {
                "items": [
                    {
                        "id": 100513,
                        "type": "Feature",
                        "title": "ACME — Pagamentos — Transferências recorrentes",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                        "description": "Feature de transferências recorrentes.",
                    }
                ]
            }
        if "[System.Id] = 100513" in where:
            return {
                "items": [
                    {
                        "id": 100513,
                        "type": "Feature",
                        "title": "ACME — Pagamentos — Transferências recorrentes",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                        "description": "Feature de transferências recorrentes.",
                    }
                ]
            }
        return {"items": []}

    async def _tool_query_hierarchy(parent_id=None, parent_type="Epic", child_type="User Story", area_path=None, title_contains=None, parent_title_hint=None, user_sub: str = ""):
        _ = (parent_type, child_type, area_path, title_contains, parent_title_hint, user_sub)
        if parent_id == 100886:
            return {
                "parent_id": 100886,
                "items": [
                    {
                        "id": 100513,
                        "type": "Feature",
                        "title": "ACME — Pagamentos — Transferências recorrentes",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                    }
                ],
            }
        if parent_id == 100513:
            return {
                "parent_id": 100513,
                "items": [
                    {
                        "id": 101001,
                        "type": "User Story",
                        "title": "ACME — Pagamentos — Transferências recorrentes — configurar periodicidade",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/101001",
                        "description": "Story irmã da mesma feature para escolher a periodicidade.",
                        "acceptance_criteria": "Permitir selecionar frequência e validar datas.",
                    },
                    {
                        "id": 101002,
                        "type": "User Story",
                        "title": "ACME — Pagamentos — Transferências recorrentes — rever resumo final",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "url": "https://dev.azure.com/mock/_workitems/edit/101002",
                        "description": "Story irmã focada no resumo antes de confirmar.",
                        "acceptance_criteria": "Mostrar resumo, CTA primário Confirmar e dados da recorrência.",
                    },
                ],
            }
        return {"items": []}

    async def _tool_search_website(query, top=10):
        return {
            "items": [
                {
                    "id": "site-1",
                    "tag": "Mapa do site",
                    "content": f"O fluxo {query} pertence à jornada Pagamentos > Transferências > Recorrentes.",
                    "url": "https://example.com/sitemap",
                    "score": 0.88,
                }
            ][:top]
        }

    async def _tool_search_figma(query: str = "", file_key: str = "", node_id: str = "", figma_url: str = ""):
        _ = (query, node_id, figma_url)
        if file_key == "ScEWCybXZAshmfzCSv9bT4":
            return {
                "items": [
                    {
                        "id": "11:20",
                        "name": "Transferências recorrentes - confirmação",
                        "type": "FRAME",
                        "file_key": file_key,
                        "file_name": "ACME | Pagamentos [Handoff] III",
                        "page_name": "Transferências",
                        "ui_components": ["Card", "Primary CTA", "Stepper"],
                        "transition_targets": ["11:21"],
                        "url": "https://figma.test/payments-confirm",
                    },
                    {
                        "id": "11:30",
                        "name": "Agendar recorrência",
                        "type": "FRAME",
                        "file_key": file_key,
                        "file_name": "ACME | Pagamentos [Handoff] III",
                        "page_name": "Transferências",
                        "ui_components": ["Dropdown", "Input", "Primary CTA"],
                        "transition_targets": ["11:20"],
                        "url": "https://figma.test/payments-schedule",
                    },
                ]
            }
        if file_key == "6g0o3EMIZrm0HSeTUMlWt7":
            return {
                "items": [
                    {
                        "id": "22:10",
                        "name": "Resumo e Agenda",
                        "type": "FRAME",
                        "file_key": file_key,
                        "file_name": "ACME | Dashboard II [Handoff]",
                        "page_name": "Home Dashboard",
                        "ui_components": ["Card", "Bloco", "CTA"],
                        "transition_targets": ["22:11"],
                        "url": "https://figma.test/dashboard-home",
                    }
                ]
            }
        return {"items": []}

    async def _tool_search_uploaded_document(query: str = "", conv_id: str = "", user_sub: str = ""):
        _ = user_sub
        if not conv_id:
            return {"items": []}
        return {
            "items": [
                {
                    "filename": "site-map.md",
                    "chunk_index": 0,
                    "text": f"O CTA primário do fluxo {query} é 'Confirmar'.",
                    "score": 0.81,
                }
            ]
        }

    async def _load_writer_profile(author_name: str, owner_sub: str = ""):
        _ = (author_name, owner_sub)
        return {"style_analysis": "Prefere títulos ACME e linguagem objetiva, sem excesso de contexto técnico."}

    def _search_curated_story_examples(**kwargs):
        _ = kwargs
        return {
            "matches": [
                {
                    "id": "100135",
                    "title": "Revamp | Pagamentos | Transferências | Recorrências | Confirmar transferência recorrente",
                    "created_by": "Mary Johnson",
                    "domain": "Pagamentos",
                    "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
                    "ux_terms": ["CTA", "Card", "Stepper"],
                    "score": 0.91,
                    "url": "https://dev.azure.com/mock/_workitems/edit/100135",
                    "sections": {
                        "proveniência": "Fluxo Pagamentos > Transferências > Recorrências.",
                        "comportamento": "Mostrar card resumo e CTA Confirmar antes da submissão.",
                    },
                    "workitem_refs": [100513, 100123],
                }
            ],
            "title_patterns": ["[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]"],
            "preferred_lexicon": ["CTA", "Card", "Stepper"],
            "notes": ["O corpus curado aponta Pagamentos como domínio provável."],
            "corpus_stats": {"count": 154},
        }

    async def _llm_with_fallback(*args, **kwargs):
        _ = (args, kwargs)
        draft = {
            "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar transferência recorrente",
            "narrative": {
                "as_a": "cliente do banco",
                "i_want": "configurar e confirmar uma transferência recorrente com CTA primário claro",
                "so_that": "consiga repetir pagamentos sem refazer o fluxo manualmente",
            },
            "business_goal": "Aumentar a autonomia do cliente no fluxo de pagamentos recorrentes.",
            "provenance": [
                "Fluxo encaixado em Pagamentos > Transferências > Recorrentes.",
                "O passo é acionado após seleção de conta e beneficiário.",
            ],
            "conditions": [
                "Cliente autenticado com conta elegível.",
                "Feature flag de recorrências ativa.",
            ],
            "rules_constraints": [
                "Usar terminologia UX consistente: CTA primário, card e dropdown.",
                "Mostrar validações de montante e periodicidade antes da confirmação.",
            ],
            "acceptance_criteria": [
                {"id": "CA-01", "text": "O CTA primário Confirmar fica disponível apenas com dados válidos."},
                {"id": "CA-02", "text": "O utilizador vê resumo final em card antes da submissão."},
            ],
            "test_scenarios": [
                {
                    "id": "CT-01",
                    "title": "Confirmação com dados válidos",
                    "category": "happy_path",
                    "preconditions": "Cliente autenticado e com conta elegível.",
                    "test_data": "Transferência mensal de 50 EUR.",
                    "given": "o cliente preenche periodicidade e montante válidos",
                    "when": "clica no CTA primário Confirmar",
                    "then": "o sistema apresenta resumo e agenda a recorrência",
                    "covers": ["CA-01", "CA-02"],
                }
            ],
            "dependencies": ["Motor de pagamentos recorrentes disponível."],
            "observations": ["Copy final alinhado com glossary do site."],
            "clarification_questions": [],
            "source_keys": ["devops:WI-1001", "knowledge:site-1", "upload:site-map.md:0"],
            "confidence": 0.87,
        }
        return SimpleNamespace(content=json.dumps(draft, ensure_ascii=False))

    async def _create_workitem_in_devops(**kwargs):
        return {
            "created": True,
            "id": 4321,
            "url": "https://dev.azure.com/mock/_workitems/edit/4321",
            "title": kwargs.get("title", ""),
            "work_item_type": "User Story",
            "area_path": kwargs.get("area_path", "") or "(default)",
        }

    async def _search_story_examples_index(*, query_text: str, dominant_domain: str = "", top: int = 4):
        tokens = {token for token in query_text.lower().split() if len(token) >= 4}
        results = []
        for draft_id, doc in search_index_docs.items():
            search_text = " ".join(
                [
                    str(doc.get("title", "") or ""),
                    str(doc.get("domain", "") or ""),
                    str(doc.get("journey", "") or ""),
                    str(doc.get("flow", "") or ""),
                    str(doc.get("content", "") or ""),
                ]
            ).lower()
            score = 0.6 + 0.05 * sum(1 for token in tokens if token in search_text)
            if dominant_domain and str(doc.get("domain", "") or "").lower() == str(dominant_domain or "").lower():
                score += 0.25
            entry = dict(doc)
            entry["id"] = draft_id
            entry["origin"] = "promoted_curated_story"
            entry["score"] = round(score, 4)
            results.append(entry)
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return {
            "matches": results[:top],
            "promoted_count": len(search_index_docs),
            "source": "azure_ai_search_story_examples",
        }

    async def _search_story_devops_index(*, query_text: str, team_scope: str = "", dominant_domain: str = "", work_item_types=None, top: int = 8):
        _ = (team_scope, work_item_types)
        domain = dominant_domain or ("Pagamentos" if "pag" in query_text.lower() or "transfer" in query_text.lower() else "Dashboard")
        items = [
            {
                "id": 100513,
                "title": "ACME — Pagamentos — Transferências recorrentes",
                "content": "Feature indexada do backlog com CTA primário, card resumo e fluxo de recorrências.",
                "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                "type": "Feature",
                "state": "Active",
                "area": r"AcmeProduct\Digital\Channels\ACME",
                "parent_id": 100886,
                "parent_title": "ACME — Pagamentos",
                "parent_type": "Epic",
                "score": 0.92 if domain == "Pagamentos" else 0.61,
                "origin": "azure_ai_search_story_devops",
            }
        ]
        return {"items": items[:top], "total_results": len(items), "source": "azure_ai_search_story_devops"}

    async def _search_story_knowledge_index(*, query_text: str, dominant_domain: str = "", team_scope: str = "", top: int = 3):
        _ = team_scope
        domain = dominant_domain or ("Pagamentos" if "pag" in query_text.lower() or "transfer" in query_text.lower() else "Dashboard")
        items = [
            {
                "id": "story-knowledge-1",
                "title": "Mapa do site - Transferências recorrentes",
                "content": f"O fluxo {query_text} pertence à jornada Pagamentos > Transferências > Recorrências e termina num resumo com CTA primário Confirmar.",
                "url": "https://example.com/story-knowledge/transferencias-recorrentes",
                "tag": "Mapa do site",
                "domain": domain,
                "journey": "Transferências",
                "flow": "Recorrências",
                "detail": "Resumo e confirmação",
                "site_section": "Pagamentos > Transferências",
                "ux_terms": ["Primary CTA", "Card", "Stepper"],
                "score": 0.91 if domain == "Pagamentos" else 0.63,
                "origin": "azure_ai_search_story_knowledge",
            }
        ]
        return {"items": items[:top], "total_results": len(items), "source": "azure_ai_search_story_knowledge"}

    def _select_story_feature_pack(**kwargs):
        placement = kwargs.get("placement", {}) if isinstance(kwargs.get("placement"), dict) else {}
        feature = (placement.get("selected_feature", {}) or {})
        if str(feature.get("id", "") or "") != "100513" and feature.get("id") != 100513:
            return {}
        return {
            "feature_id": "100513",
            "feature_title": "ACME | Pagamentos | Transferências recorrentes",
            "area_path": r"AcmeProduct\Digital\Channels\ACME",
            "domain": "Pagamentos",
            "journey": "Transferências",
            "flow": "Recorrências",
            "story_count": 2,
            "canonical_title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
            "top_ux_terms": ["CTA", "Card", "Stepper"],
            "top_flows": ["Configurar recorrência", "Resumo e confirmação"],
            "top_titles": [
                "ACME | Pagamentos | Transferências | Recorrências | Configurar periodicidade",
                "ACME | Pagamentos | Transferências | Recorrências | Rever resumo final",
            ],
            "notes": [
                "Pack extraído da feature 100513.",
                "As stories filhas reforçam CTA primário, card resumo e stepper.",
            ],
            "figma_url": "https://figma.test/feature-100513",
            "stories": [
                {
                    "id": "101001",
                    "title": "ACME | Pagamentos | Transferências | Recorrências | Configurar periodicidade",
                    "snippet": "Definir periodicidade e validações antes do resumo final.",
                    "url": "https://dev.azure.com/mock/_workitems/edit/101001",
                    "ux_terms": ["CTA", "Dropdown", "Input"],
                    "origin": "devops_feature_pack",
                },
                {
                    "id": "101002",
                    "title": "ACME | Pagamentos | Transferências | Recorrências | Rever resumo final",
                    "snippet": "Mostrar card resumo e CTA Confirmar antes de agendar a recorrência.",
                    "url": "https://dev.azure.com/mock/_workitems/edit/101002",
                    "ux_terms": ["CTA", "Card", "Stepper"],
                    "origin": "devops_feature_pack",
                },
            ],
            "score": 1.0,
        }

    async def _upsert_story_example_index_document(*, draft_id: str, entry: dict, row: dict | None = None):
        search_index_docs[draft_id] = {
            "title": entry.get("title", ""),
            "domain": entry.get("domain", ""),
            "journey": entry.get("journey", ""),
            "flow": entry.get("flow", ""),
            "detail": entry.get("detail", ""),
            "title_pattern": entry.get("title_pattern", ""),
            "description_text": entry.get("description_text", ""),
            "acceptance_text": entry.get("acceptance_text", ""),
            "sections": dict(entry.get("sections", {}) or {}),
            "ux_terms": list(entry.get("ux_terms", []) or []),
            "tags": list(entry.get("tags", []) or []),
            "workitem_refs": list(entry.get("workitem_refs", []) or []),
            "quality_score": float(entry.get("quality_score", 0.0) or 0.0),
            "url": entry.get("url", "") or str((row or {}).get("PublishedWorkItemUrl", "") or ""),
            "content": entry.get("search_text", ""),
            "source_draft_id": draft_id,
            "source_user_sub": entry.get("source_user_sub", ""),
        }
        return {"ok": True, "document_id": draft_id}

    async def _delete_story_example_index_document(draft_id: str):
        search_index_docs.pop(draft_id, None)
        return {"ok": True, "document_id": draft_id, "deleted": True}

    monkeypatch.setattr(user_story_lane, "blob_upload_json", _blob_upload_json)
    monkeypatch.setattr(user_story_lane, "blob_download_json", _blob_download_json)
    monkeypatch.setattr(user_story_lane, "table_insert", _table_insert)
    monkeypatch.setattr(user_story_lane, "table_merge", _table_merge)
    monkeypatch.setattr(user_story_lane, "table_query", _table_query)
    monkeypatch.setattr(user_story_lane, "tool_search_workitems", _tool_search_workitems)
    monkeypatch.setattr(user_story_lane, "tool_query_workitems", _tool_query_workitems)
    monkeypatch.setattr(user_story_lane, "tool_query_hierarchy", _tool_query_hierarchy)
    monkeypatch.setattr(user_story_lane, "tool_search_website", _tool_search_website)
    monkeypatch.setattr(user_story_lane, "tool_search_figma", _tool_search_figma)
    monkeypatch.setattr(user_story_lane, "tool_search_uploaded_document", _tool_search_uploaded_document)
    monkeypatch.setattr(user_story_lane, "_load_writer_profile", _load_writer_profile)
    monkeypatch.setattr(user_story_lane, "search_curated_story_examples", _search_curated_story_examples)
    monkeypatch.setattr(user_story_lane, "llm_with_fallback", _llm_with_fallback)
    monkeypatch.setattr(user_story_lane, "create_workitem_in_devops", _create_workitem_in_devops)
    monkeypatch.setattr(user_story_lane, "search_story_examples_index", _search_story_examples_index)
    monkeypatch.setattr(user_story_lane, "search_story_devops_index", _search_story_devops_index)
    monkeypatch.setattr(user_story_lane, "search_story_knowledge_index", _search_story_knowledge_index)
    monkeypatch.setattr(user_story_lane, "select_story_feature_pack", _select_story_feature_pack)
    monkeypatch.setattr(user_story_lane, "upsert_story_example_index_document", _upsert_story_example_index_document)
    monkeypatch.setattr(user_story_lane, "delete_story_example_index_document", _delete_story_example_index_document)
    return {"rows": rows, "blobs": blobs, "search_index_docs": search_index_docs}


@pytest.mark.asyncio
async def test_generate_user_story_returns_structured_draft(fake_user_story_lane_backend):
    import user_story_lane

    result = await user_story_lane.generate_user_story(
        {
            "objective": "Quero permitir ao cliente confirmar uma transferência recorrente com resumo final.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
            "context": "No site existe um card resumo e um CTA primário de confirmação.",
            "conversation_id": "conv-us-1",
        },
        user_sub="jane",
    )

    assert result["draft_id"]
    assert result["draft"]["title"].startswith("ACME |")
    assert result["draft"]["description_html"].startswith("<div>Eu como")
    assert result["validation"]["quality_score"] >= 0.7
    assert result["publish_ready"] is True
    assert result["placement"]["selected_epic"]["id"] == 100886
    assert result["placement"]["selected_feature"]["id"] == 100513
    assert result["design_flow"]
    assert result["feature_siblings"]
    assert result["feature_pack"]["feature_id"] == "100513"
    assert result["feature_pack"]["story_count"] == 2
    assert result["curated_workitem_refs"]
    assert result["curated_workitem_refs"][0]["origin"] == "curated_story_workitem_ref"
    assert result["feature_siblings"][0]["origin"] == "devops_feature_hierarchy"
    assert result["design_flow"][0]["title"]


@pytest.mark.asyncio
async def test_context_preview_explicit_feature_id_infers_parent_epic(fake_user_story_lane_backend):
    import user_story_lane

    preview = await user_story_lane.build_context_preview(
        {
            "objective": "Quero melhorar o resumo final da configuração de recorrências.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "100513",
            "context": "A feature já existe e pertence ao domínio de pagamentos.",
            "conversation_id": "conv-us-explicit-id",
        },
        user_sub="jane",
    )

    assert preview["placement"]["selected_feature"]["id"] == 100513
    assert preview["placement"]["selected_epic"]["id"] == 100886
    assert preview["feature_pack"]["feature_id"] == "100513"
    assert any("Epic > Feature" in item for item in preview["placement"]["reasoning"])
    assert len(preview["feature_siblings"]) >= 1
    assert len(preview["curated_workitem_refs"]) >= 1


@pytest.mark.asyncio
async def test_publish_user_story_uses_persisted_draft(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Gerar história para confirmação de transferências recorrentes.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    result = await user_story_lane.publish_user_story(
        {
            "draft_id": generated["draft_id"],
            "area_path": r"AcmeProduct\Digital\Channels\ACME",
            "tags": "AI-Draft",
        },
        user_sub="jane",
    )

    assert result["published"] is True
    assert result["work_item"]["id"] == 4321


@pytest.mark.asyncio
async def test_publish_user_story_uses_final_draft_when_provided(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Gerar história para confirmação de transferências recorrentes.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    final_draft = {
        **generated["draft"],
        "title": "ACME | Pagamentos | Transferências | Recorrências | Rever resumo antes de confirmar",
        "business_goal": "Reduzir erros antes da confirmação final de recorrências.",
        "acceptance_criteria": [
            {"id": "CA-01", "text": "O resumo final mostra conta, montante e periodicidade antes da submissão."},
            {"id": "CA-02", "text": "O CTA primário Confirmar só fica ativo com todos os dados válidos."},
        ],
    }

    result = await user_story_lane.publish_user_story(
        {
            "draft_id": generated["draft_id"],
            "area_path": r"AcmeProduct\Digital\Channels\ACME",
            "tags": "AI-Draft",
            "final_draft": final_draft,
        },
        user_sub="jane",
    )

    assert result["published"] is True
    assert result["title"] == final_draft["title"]
    assert result["work_item"]["title"] == final_draft["title"]
    assert "title" in result["diff_summary"]["changed_fields"]


@pytest.mark.asyncio
async def test_feedback_promotes_previous_example_for_same_user(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Criar user story para confirmação de transferências recorrentes.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    feedback = await user_story_lane.record_user_story_feedback(
        {
            "draft_id": generated["draft_id"],
            "outcome": "accepted",
            "note": "Saiu quase pronta.",
        },
        user_sub="jane",
    )
    assert feedback["outcome"] == "accepted"

    preview = await user_story_lane.build_context_preview(
        {
            "objective": "Preciso de outra história para recorrências em pagamentos.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    assert preview["context_pack"]["previous_examples"]
    assert preview["context_pack"]["previous_examples"][0]["title"].startswith("ACME |")


@pytest.mark.asyncio
async def test_feedback_persists_final_draft_for_learning(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Criar user story para confirmação de transferências recorrentes.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    final_draft = {
        **generated["draft"],
        "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar recorrência com validação reforçada",
        "rules_constraints": [
            "Usar terminologia UX consistente: CTA primário, card e dropdown.",
            "Bloquear confirmação quando a periodicidade não for suportada.",
        ],
    }

    feedback = await user_story_lane.record_user_story_feedback(
        {
            "draft_id": generated["draft_id"],
            "outcome": "edited",
            "note": "Ajustei o título e uma regra de bloqueio.",
            "final_draft": final_draft,
        },
        user_sub="jane",
    )

    assert feedback["outcome"] == "edited"
    assert "title" in feedback["diff_summary"]["changed_fields"]
    assert "rules_constraints" in feedback["diff_summary"]["changed_fields"]


@pytest.mark.asyncio
async def test_context_preview_includes_figma_design_map_for_transfers(fake_user_story_lane_backend):
    import user_story_lane

    preview = await user_story_lane.build_context_preview(
        {
            "objective": "Preciso de uma história para confirmar uma transferência recorrente com CTA primário e resumo final.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
            "context": "O fluxo de pagamentos deve mostrar card resumo e stepper de confirmação.",
        },
        user_sub="jane",
    )

    assert preview["design_sources"]
    assert any("Pagamentos" in item["title"] or "Transferências" in item["title"] for item in preview["design_sources"])
    assert preview["context_pack"]["design_map"]["matches"]
    assert preview["domain_profile"]["domain"] == "Pagamentos"
    assert "Pagamentos" in preview["domain_profile"]["design_file_title"]
    assert preview["story_policy_pack"]["domain"] == "Pagamentos"
    assert "acceptance_criteria" in preview["story_policy_pack"]["mandatory_sections"]
    assert preview["design_flow"]
    assert preview["design_flow"][0]["page_name"] == "Transferências"
    assert preview["curated_examples"]
    assert preview["context_pack"]["curated_corpus"]["title_patterns"]
    assert preview["placement"]["selected_feature"]["id"] == 100513
    assert preview["similar_items"]
    assert preview["similar_items"][0]["origin"] == "azure_ai_search_story_devops"
    assert preview["context_pack"]["knowledge_sources"]
    assert preview["context_pack"]["knowledge_sources"][0]["origin"] == "azure_ai_search_story_knowledge"


@pytest.mark.asyncio
async def test_dashboard_preview_uses_dashboard_design_source(fake_user_story_lane_backend):
    import user_story_lane

    preview = await user_story_lane.build_context_preview(
        {
            "objective": "Quero uma user story para o dashboard com resumo e agenda.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Dashboard",
            "context": "O home deve destacar os cards principais e os blocos de agenda.",
        },
        user_sub="jane",
    )

    assert preview["design_sources"]
    assert "Dashboard" in preview["design_sources"][0]["title"]
    assert preview["domain_profile"]["domain"] == "Dashboard"
    assert preview["story_policy_pack"]["domain"] == "Dashboard"
    assert preview["design_flow"]
    assert "Agenda" in preview["design_flow"][0]["title"] or "Resumo" in preview["design_flow"][0]["title"]


@pytest.mark.asyncio
async def test_eval_summary_reports_domain_metrics_and_edit_burden(fake_user_story_lane_backend):
    import user_story_lane

    payments = await user_story_lane.generate_user_story(
        {
            "objective": "Criar história para confirmação de transferências recorrentes em pagamentos.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )
    final_draft = {
        **payments["draft"],
        "title": "ACME | Pagamentos | Transferências | Recorrências | Rever resumo e confirmar recorrência",
        "business_goal": "Reduzir erros antes da confirmação final.",
    }
    await user_story_lane.publish_user_story(
        {
            "draft_id": payments["draft_id"],
            "final_draft": final_draft,
        },
        user_sub="jane",
    )
    await user_story_lane.record_user_story_feedback(
        {
            "draft_id": payments["draft_id"],
            "outcome": "edited",
            "note": "Foi preciso ajustar o título e o objetivo.",
            "final_draft": final_draft,
        },
        user_sub="jane",
    )
    await user_story_lane.generate_user_story(
        {
            "objective": "Criar mais uma história para recorrências em pagamentos com CTA primário de confirmação.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
            "context": "Mesmo fluxo de pagamentos com resumo e stepper.",
        },
        user_sub="jane",
    )

    await user_story_lane.generate_user_story(
        {
            "objective": "Quero uma história para o dashboard com resumo e agenda.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Dashboard",
            "context": "O home deve destacar cards e bloco de agenda.",
        },
        user_sub="maria",
    )

    jane_summary = await user_story_lane.build_user_story_eval_summary(user_sub="jane", top=50)
    assert jane_summary["totals"]["draft_count"] == 2
    assert jane_summary["totals"]["published_count"] == 1
    assert jane_summary["domains"][0]["domain"] == "Pagamentos"
    assert jane_summary["domains"][0]["feedback_breakdown"]["edited"] == 1
    assert jane_summary["domains"][0]["curated_example_count"] >= 1
    assert "corpus_coverage_low" in jane_summary["domains"][0]["alerts"]
    assert jane_summary["corpus_gaps"][0]["domain"] == "Pagamentos"
    assert jane_summary["recommendations"]
    assert jane_summary["recommendations"][0]["action"] in {"curate_examples", "review_policy_pack"}
    assert jane_summary["curation_candidates"]
    assert jane_summary["curation_candidates"][0]["domain"] == "Pagamentos"
    assert jane_summary["most_edited"][0]["draft_id"] == payments["draft_id"]
    assert jane_summary["most_edited"][0]["edit_burden"] > 0.0

    global_summary = await user_story_lane.build_user_story_eval_summary(top=50)
    assert global_summary["totals"]["draft_count"] == 3
    assert global_summary["totals"]["published_count"] == 1
    assert global_summary["totals"]["domain_count"] == 2
    assert global_summary["totals"]["recommendation_count"] >= 1
    assert global_summary["totals"]["curation_candidate_count"] >= 1
    assert {item["domain"] for item in global_summary["domains"]} == {"Pagamentos", "Dashboard"}
    assert global_summary["corpus"]["count"] >= 1


@pytest.mark.asyncio
async def test_promoted_candidate_enters_curated_retrieval(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Criar história para confirmação de transferências recorrentes em pagamentos.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )

    final_draft = {
        **generated["draft"],
        "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar recorrência com revisão final",
    }

    await user_story_lane.publish_user_story(
        {
            "draft_id": generated["draft_id"],
            "final_draft": final_draft,
        },
        user_sub="jane",
    )
    await user_story_lane.record_user_story_feedback(
        {
            "draft_id": generated["draft_id"],
            "outcome": "accepted",
            "note": "Pronta para reaproveitar.",
            "final_draft": final_draft,
        },
        user_sub="jane",
    )

    promoted = await user_story_lane.promote_user_story_to_curated_corpus(
        draft_id=generated["draft_id"],
        source_user_sub="jane",
        promoted_by="admin",
        note="Promoção de teste",
    )
    assert promoted["submitted"] is True
    assert promoted["status"] == "candidate"
    assert not fake_user_story_lane_backend["search_index_docs"]

    pending_preview = await user_story_lane.build_context_preview(
        {
            "objective": "Preciso de outra história para pagamentos recorrentes com confirmação final.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="maria",
    )
    assert not any(item.get("origin") == "promoted_curated_story" for item in pending_preview["curated_examples"])

    reviewed = await user_story_lane.review_user_story_curated_candidate(
        draft_id=generated["draft_id"],
        action="approve",
        reviewed_by="admin",
        note="Aprovado para corpus promovido",
    )
    assert reviewed["status"] == "active"
    assert generated["draft_id"] in fake_user_story_lane_backend["search_index_docs"]

    preview = await user_story_lane.build_context_preview(
        {
            "objective": "Preciso de outra história para pagamentos recorrentes com confirmação final.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="maria",
    )

    assert preview["curated_examples"]
    assert any(item.get("origin") == "promoted_curated_story" for item in preview["curated_examples"])
    assert int(preview["context_pack"]["curated_corpus"]["promoted_count"]) >= 1

    summary = await user_story_lane.build_user_story_eval_summary(top=50)
    assert summary["curated_registry"]["counts"]["active"] >= 1


@pytest.mark.asyncio
async def test_deactivate_candidate_removes_story_from_search_index(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Criar história para confirmação de transferências recorrentes em pagamentos.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )
    await user_story_lane.publish_user_story({"draft_id": generated["draft_id"]}, user_sub="jane")
    await user_story_lane.record_user_story_feedback(
        {"draft_id": generated["draft_id"], "outcome": "accepted", "note": "Bom draft."},
        user_sub="jane",
    )
    await user_story_lane.promote_user_story_to_curated_corpus(
        draft_id=generated["draft_id"],
        source_user_sub="jane",
        promoted_by="admin",
        note="Submeter",
    )
    await user_story_lane.review_user_story_curated_candidate(
        draft_id=generated["draft_id"],
        action="approve",
        reviewed_by="admin",
        note="Aprovado",
    )

    assert generated["draft_id"] in fake_user_story_lane_backend["search_index_docs"]

    reviewed = await user_story_lane.review_user_story_curated_candidate(
        draft_id=generated["draft_id"],
        action="deactivate",
        reviewed_by="admin",
        note="Retirar do corpus ativo",
    )

    assert reviewed["status"] == "inactive"
    assert generated["draft_id"] not in fake_user_story_lane_backend["search_index_docs"]


@pytest.mark.asyncio
async def test_sync_story_examples_search_index_resyncs_active_rows(fake_user_story_lane_backend):
    import user_story_lane

    generated = await user_story_lane.generate_user_story(
        {
            "objective": "Criar história para confirmação de transferências recorrentes em pagamentos.",
            "team_scope": r"AcmeProduct\Digital\Channels\ACME",
            "epic_or_feature": "Transferências recorrentes",
        },
        user_sub="jane",
    )
    await user_story_lane.publish_user_story({"draft_id": generated["draft_id"]}, user_sub="jane")
    await user_story_lane.record_user_story_feedback(
        {"draft_id": generated["draft_id"], "outcome": "accepted", "note": "Bom draft."},
        user_sub="jane",
    )
    await user_story_lane.promote_user_story_to_curated_corpus(
        draft_id=generated["draft_id"],
        source_user_sub="jane",
        promoted_by="admin",
    )
    await user_story_lane.review_user_story_curated_candidate(
        draft_id=generated["draft_id"],
        action="approve",
        reviewed_by="admin",
        note="Aprovado",
    )

    fake_user_story_lane_backend["search_index_docs"].clear()
    summary = await user_story_lane.sync_user_story_examples_search_index(top=50)

    assert summary["scanned"] >= 1
    assert summary["synced"] >= 1
    assert generated["draft_id"] in fake_user_story_lane_backend["search_index_docs"]

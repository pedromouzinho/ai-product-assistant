"""routes_digest.py — Daily Digest endpoints (Task 6.3 + S4-07)."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from auth import get_current_user
from config import DEVOPS_ORG, DEVOPS_PROJECT, DEVOPS_PROJECT_LABEL, DIGEST_FEEDBACK_SUMMARY_ENABLED
from http_helpers import devops_request_with_retry as _devops_request_with_retry
from route_deps import security, limiter, _user_or_ip_rate_key, log_audit
from storage import table_query
from tools import _devops_url, _devops_headers

logger = logging.getLogger(__name__)

router = APIRouter()

_DIGEST_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "System.AssignedTo",
    "System.CreatedDate",
]


def _digest_format_item(raw_item: dict) -> dict:
    fields = raw_item.get("fields", {}) if isinstance(raw_item, dict) else {}
    assigned = fields.get("System.AssignedTo", "")
    if isinstance(assigned, dict):
        assigned_to = assigned.get("displayName", "")
    else:
        assigned_to = str(assigned or "")
    wi_id = raw_item.get("id")
    return {
        "id": wi_id,
        "title": fields.get("System.Title", ""),
        "state": fields.get("System.State", ""),
        "type": fields.get("System.WorkItemType", ""),
        "assigned_to": assigned_to,
        "created_date": fields.get("System.CreatedDate", ""),
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{wi_id}" if wi_id else "",
    }


async def _run_digest_section(section_name: str, wiql_query: str) -> dict:
    headers = _devops_headers()
    section = {"count": 0, "items": []}
    batch_errors = []

    try:
        wiql_resp = await _devops_request_with_retry(
            "POST",
            _devops_url("wit/wiql?api-version=7.1"),
            headers,
            {"query": wiql_query},
            max_retries=3,
            timeout=60,
        )
        if "error" in wiql_resp:
            section["error"] = wiql_resp["error"]
            return section

        ids = [wi.get("id") for wi in wiql_resp.get("workItems", []) if wi.get("id")]
        section["count"] = len(ids)
        if not ids:
            return section

        details = []
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            batch_resp = await _devops_request_with_retry(
                "POST",
                _devops_url("wit/workitemsbatch?api-version=7.1"),
                headers,
                {"ids": batch, "fields": _DIGEST_FIELDS},
                max_retries=3,
                timeout=60,
            )
            if "error" in batch_resp:
                batch_errors.append(batch_resp["error"])
                continue
            details.extend(batch_resp.get("value", []))

        section["items"] = [_digest_format_item(item) for item in details]
        if batch_errors:
            section["error"] = "; ".join(batch_errors[:3])
        return section
    except Exception as e:
        section["error"] = f"{section_name} failed: {str(e)}"
        return section


@router.get("/api/digest")
@limiter.shared_limit(
    "10/minute",
    scope="chat_budget",
    key_func=_user_or_ip_rate_key,
)
async def api_digest(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)

    sections_wiql = {
        "created_yesterday": (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' "
            "AND [System.WorkItemType] = 'User Story' "
            "AND [System.CreatedDate] >= @Today-1 "
            "AND [System.CreatedDate] < @Today "
            "ORDER BY [System.CreatedDate] DESC"
        ),
        "old_bugs": (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' "
            "AND [System.WorkItemType] = 'Bug' "
            "AND [System.State] = 'Active' "
            "AND [System.CreatedDate] < @Today-7 "
            "ORDER BY [System.CreatedDate] ASC"
        ),
        "unassigned": (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' "
            "AND [System.State] <> 'Closed' "
            "AND [System.State] <> 'Removed' "
            "AND [System.AssignedTo] = '' "
            "ORDER BY [System.ChangedDate] DESC"
        ),
        "closed_this_week": (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' "
            "AND [System.State] = 'Closed' "
            "AND [Microsoft.VSTS.Common.ClosedDate] >= @StartOfWeek "
            "ORDER BY [Microsoft.VSTS.Common.ClosedDate] DESC"
        ),
    }

    payload = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "project": DEVOPS_PROJECT_LABEL,
    }
    section_items = list(sections_wiql.items())
    section_results = await asyncio.gather(
        *[_run_digest_section(section_name, wiql_query) for section_name, wiql_query in section_items],
        return_exceptions=True,
    )
    for (section_name, _), result in zip(section_items, section_results):
        if isinstance(result, Exception):
            payload[section_name] = {
                "count": 0,
                "items": [],
                "error": f"{section_name} failed: {str(result)}",
            }
        else:
            payload[section_name] = result

    # S4-07: Feedback summary — "Melhorámos X respostas esta semana"
    if DIGEST_FEEDBACK_SUMMARY_ENABLED:
        try:
            payload["feedback_summary"] = await _get_weekly_feedback_summary()
        except Exception as e:
            logger.warning("[Digest] feedback summary failed: %s", e)
            payload["feedback_summary"] = {"rules_learned": 0, "avg_rating": 0, "total_feedbacks": 0}

    return payload


async def _get_weekly_feedback_summary() -> dict:
    """Compute weekly feedback stats for digest: rules learned, rating trend."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    summary = {
        "rules_learned": 0,
        "avg_rating": 0,
        "total_feedbacks": 0,
        "positive_count": 0,
        "negative_count": 0,
    }

    try:
        # Count rules learned this week
        rules = await table_query(
            "PromptRules",
            f"Timestamp ge datetime'{week_ago}'",
            top=200,
        )
        summary["rules_learned"] = len(rules) if rules else 0
    except Exception as e:
        logger.debug("[Digest] PromptRules query failed: %s", e)

    try:
        # Count feedbacks this week
        feedbacks = await table_query(
            "feedback",
            f"Timestamp ge datetime'{week_ago}'",
            top=500,
        )
        if feedbacks:
            summary["total_feedbacks"] = len(feedbacks)
            ratings = [int(f.get("Rating", 0) or 0) for f in feedbacks if f.get("Rating")]
            if ratings:
                summary["avg_rating"] = round(sum(ratings) / len(ratings), 1)
            summary["positive_count"] = sum(1 for r in ratings if r >= 7)
            summary["negative_count"] = sum(1 for r in ratings if r <= 4)
    except Exception as e:
        logger.debug("[Digest] feedback query failed: %s", e)

    return summary

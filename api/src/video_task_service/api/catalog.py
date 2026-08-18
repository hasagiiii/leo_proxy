from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import parse_qs, urljoin, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from video_task_service.auth import require_api_key
from video_task_service.config import get_settings
from video_task_service.schemas import ModelCatalogItem, ModelCatalogResponse
from video_task_service.upstream import LeonardoUpstream, UpstreamError

CatalogCardType = Literal["MODEL", "BLUEPRINT", "GUIDE", "ALL"]

INTEGRATED_MODEL_CARDS: tuple[dict[str, object], ...] = (
    {
        "id": "integrated-seed-audio-1.0",
        "type": "MODEL",
        "rank": 0,
        "title": "Seed Audio 1.0",
        "description": (
            "Text-to-speech with selectable voices, speed, volume, pitch, "
            "and up to four audio outputs."
        ),
        "url": "https://app.leonardo.ai/generate?model=seed-audio-1.0",
        "imageUrl": None,
        "videoUrl": None,
    },
)

router = APIRouter(
    prefix="/models",
    tags=["models"],
    dependencies=[Depends(require_api_key)],
)


def create_catalog_upstream() -> LeonardoUpstream:
    return LeonardoUpstream(get_settings())


def model_slug(raw_url: str) -> str | None:
    parsed = urlparse(urljoin("https://app.leonardo.ai", raw_url))
    values = parse_qs(parsed.query).get("model") or []
    value = values[0].strip() if values else ""
    return value or None


def catalog_item(card: dict[str, object]) -> ModelCatalogItem:
    raw_url = str(card.get("url") or "")
    return ModelCatalogItem(
        id=str(card.get("id") or ""),
        type=str(card.get("type") or ""),
        rank=int(card.get("rank") or 0),
        title=str(card.get("title") or ""),
        description=str(card["description"]) if card.get("description") is not None else None,
        url=raw_url,
        image_url=str(card["imageUrl"]) if card.get("imageUrl") is not None else None,
        video_url=str(card["videoUrl"]) if card.get("videoUrl") is not None else None,
        model=model_slug(raw_url),
    )


def with_integrated_models(cards: list[dict[str, object]]) -> list[dict[str, object]]:
    merged = list(cards)
    existing_models = {
        slug
        for card in merged
        if (slug := model_slug(str(card.get("url") or ""))) is not None
    }
    next_rank = max((int(card.get("rank") or 0) for card in merged), default=0)
    for integrated in INTEGRATED_MODEL_CARDS:
        slug = model_slug(str(integrated.get("url") or ""))
        if slug is None or slug in existing_models:
            continue
        next_rank += 1
        merged.append({**integrated, "rank": next_rank})
        existing_models.add(slug)
    return merged


@router.get("", response_model=ModelCatalogResponse)
async def list_models(
    response: Response,
    card_type: Annotated[CatalogCardType, Query(alias="type")] = "MODEL",
) -> ModelCatalogResponse:
    upstream = create_catalog_upstream()
    try:
        cards = await upstream.list_homepage_cards()
    except UpstreamError as exc:
        upstream_status = {
            "UPSTREAM_TIMEOUT": status.HTTP_504_GATEWAY_TIMEOUT,
            "UPSTREAM_NETWORK_ERROR": status.HTTP_503_SERVICE_UNAVAILABLE,
        }.get(exc.code, status.HTTP_502_BAD_GATEWAY)
        raise HTTPException(
            status_code=upstream_status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    finally:
        await upstream.close()

    cards = with_integrated_models(cards)
    selected = (
        cards
        if card_type == "ALL"
        else [card for card in cards if card.get("type") == card_type]
    )
    items = sorted((catalog_item(card) for card in selected), key=lambda item: item.rank)
    response.headers["Cache-Control"] = "private, max-age=300"
    return ModelCatalogResponse(
        provider="leonardo",
        source="homepageCards+integratedModels",
        items=items,
        total=len(items),
    )

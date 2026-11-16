from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

GRAPHQL_BASE_URL = "https://www.lego.com/api/graphql"
ENDING_SOON_WINDOW_SECONDS = 14 * 24 * 3600  # 12096e5 ms (14 days) in LEGO frontend

REWARD_LISTING_QUERY = """
  fragment Dimensions on Dimensions {
    width
    height
  }
  fragment ListingRewardImageAssetDetails on RewardImageAssetDetails {
    id
    url
    listingDimensions {
      ...Dimensions
    }
  }
  query rewardListing($input: RewardListingInputV2) {
    rewardListing: rewardListingV2(input: $input) {
      paginationDetails {
        page
        size
        lastPage
        firstEntry
        lastEntry
        total
      }
      rewards: rewardsV2 {
        id
        title
        pointValue
        startDate
        endDate
        nonLocalisedStartDate
        nonLocalisedEndDate
        rewardId
        urlTitleSlug
        restrictedInCountry
        type
        category
        quantity
        isDistributed
        rewardSkus {
          sku
          skuType
        }
        new
        images {
          ...ListingRewardImageAssetDetails
        }
      }
    }
  }
"""

REWARD_DETAIL_QUERY = """
  query Reward($input: RewardInput!) {
    reward: rewardV4(input: $input) {
      title
      rewardId
      urlTitleSlug
      pointValue
      endDate
      startDate
      nonLocalisedStartDate
      nonLocalisedEndDate
      type
      description
      restrictedInCountry
      chokingHazard
      quantity
      isDistributed
      rewardSkus {
        sku
        skuType
      }
      productCode
      theme
      themeUrl
      category
      categoryUrl
      new
      redemptionLimits {
        timeframeLimit
        lifetimeLimit
      }
      simpleContentSections {
        overview {
          __typename
          ... on RewardSimpleContentSectionEntry_Title {
            text
          }
          ... on RewardSimpleContentSectionEntry_BulletPoints {
            points
          }
          ... on RewardSimpleContentSectionEntry_TextBlock {
            text
          }
        }
      }
      images {
        id
        url
      }
    }
  }
"""


class RewardLifecycle(str, Enum):
    STARTING_SOON = "STARTING_SOON"
    AVAILABLE = "AVAILABLE"
    ENDING_SOON = "ENDING_SOON"
    ENDED = "ENDED"


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@dataclass
class RewardItem:
    reward_id: str
    title: str
    point_value: int
    start_date: datetime | None = None
    end_date: datetime | None = None
    non_localised_start_date: datetime | None = None
    non_localised_end_date: datetime | None = None
    url_title_slug: str | None = None
    restricted_in_country: bool = False
    reward_type: str = "CODE"
    category: str | None = None
    theme: str | None = None
    quantity: int | None = None
    is_distributed: bool | None = None
    skus: list[dict[str, str]] = field(default_factory=list)
    is_new_badge: bool = False
    image_url: str | None = None
    description: str | None = None
    lifetime_limit: int | None = None
    timeframe_limit: int | None = None
    overview_steps: list[str] = field(default_factory=list)
    overview_note: str | None = None
    locale: str = "en-PL"

    @property
    def url(self) -> str:
        loc = self.locale.lower()
        if self.url_title_slug:
            return f"https://www.lego.com/{loc}/reward/{self.url_title_slug}-{self.reward_id}"
        return f"https://www.lego.com/{loc}/reward/{self.reward_id}"

    @property
    def primary_sku(self) -> str | None:
        if not self.skus:
            return None
        country = (self.locale.split("-")[1] if "-" in self.locale else "").upper()
        region = "North America" if country in {"US", "CA", "MX"} else "Rest of the World"
        for entry in self.skus:
            if entry.get("skuType") == region and entry.get("sku"):
                return entry["sku"]
        for entry in self.skus:
            if entry.get("skuType") == "Global" and entry.get("sku"):
                return entry["sku"]
        return self.skus[0].get("sku")

    def lifecycle(self, now: datetime | None = None) -> RewardLifecycle:
        """Matches LEGO's frontend RewardLeaf lifecycle calculation."""
        now = now or datetime.now(timezone.utc)
        if self.start_date and self.start_date > now:
            return RewardLifecycle.STARTING_SOON
        if self.end_date:
            if self.end_date < now:
                return RewardLifecycle.ENDED
            if (self.end_date - now).total_seconds() < ENDING_SOON_WINDOW_SECONDS:
                return RewardLifecycle.ENDING_SOON
        return RewardLifecycle.AVAILABLE

    def is_redeemable(self, now: datetime | None = None) -> bool:
        stage = self.lifecycle(now)
        return stage in (RewardLifecycle.AVAILABLE, RewardLifecycle.ENDING_SOON)

    @property
    def in_stock(self) -> bool:
        # In LEGO's frontend: inStock: typeof e.quantity !== "number" || e.quantity > 0
        # Plus if CODE and isDistributed !== false and no matching SKU, quantity is treated as 0
        if (
            self.reward_type.upper() == "CODE"
            and self.is_distributed is not False
            and self.skus
            and not self.primary_sku
        ):
            return False
        return self.quantity is None or self.quantity > 0

    def is_available_now(self, now: datetime | None = None) -> bool:
        return self.is_redeemable(now) and self.in_stock

    def availability_summary(
        self, tz: ZoneInfo, now: datetime | None = None
    ) -> str:
        now = now or datetime.now(timezone.utc)
        stage = self.lifecycle(now)

        if stage == RewardLifecycle.STARTING_SOON and self.start_date:
            local_start = self.start_date.astimezone(tz)
            delta = self.start_date - now
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes = rem // 60
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0 or days > 0:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            countdown = " ".join(parts)
            return (
                f"⏳ Coming Soon — Available on "
                f"{local_start.strftime('%Y-%m-%d %H:%M %Z')} (in {countdown})"
            )

        if stage == RewardLifecycle.ENDED:
            if self.end_date:
                local_end = self.end_date.astimezone(tz)
                return f"⚫ Expired (ended {local_end.strftime('%Y-%m-%d %H:%M %Z')})"
            return "⚫ Expired"

        if not self.in_stock:
            return "🔴 Out of Stock"

        if stage == RewardLifecycle.ENDING_SOON and self.end_date:
            local_end = self.end_date.astimezone(tz)
            return (
                f"🟠 Available Now (Ending Soon: "
                f"{local_end.strftime('%Y-%m-%d %H:%M %Z')})"
            )

        return "🟢 Available Now (In Stock)"

    @property
    def human_type(self) -> str:
        t = (self.reward_type or "").upper()
        if t == "CODE":
            return "Physical / Promo Code Reward"
        if t == "DOWNLOAD":
            return "Digital Download"
        if t == "SWEEPSTAKE":
            return "Sweepstake / Draw"
        return t or "Reward"

    @classmethod
    def from_graphql(cls, raw: dict[str, Any], locale: str = "en-PL") -> RewardItem:
        images = raw.get("images") or []
        image_url = images[0].get("url") if images else None
        if image_url and "lego.com/cdn/" in image_url and "?" not in image_url:
            image_url = f"{image_url}?fit=bounds&quality=85&width=800&height=800&dpr=1"

        redemption_limits = raw.get("redemptionLimits") or {}
        overview_steps: list[str] = []
        overview_note: str | None = None

        sections = (raw.get("simpleContentSections") or {}).get("overview") or []
        for entry in sections:
            typename = entry.get("__typename", "")
            if typename == "RewardSimpleContentSectionEntry_BulletPoints":
                overview_steps.extend(entry.get("points") or [])
            elif typename == "RewardSimpleContentSectionEntry_TextBlock":
                overview_note = entry.get("text")

        return cls(
            reward_id=str(raw.get("rewardId") or raw.get("id") or ""),
            title=(raw.get("title") or "Untitled Reward").strip(),
            point_value=int(raw.get("pointValue") or 0),
            start_date=parse_iso_datetime(raw.get("startDate")),
            end_date=parse_iso_datetime(raw.get("endDate")),
            non_localised_start_date=parse_iso_datetime(
                raw.get("nonLocalisedStartDate")
            ),
            non_localised_end_date=parse_iso_datetime(raw.get("nonLocalisedEndDate")),
            url_title_slug=raw.get("urlTitleSlug"),
            restricted_in_country=bool(raw.get("restrictedInCountry")),
            reward_type=str(raw.get("type") or "CODE"),
            category=raw.get("category"),
            theme=raw.get("theme"),
            quantity=raw.get("quantity"),
            is_distributed=raw.get("isDistributed"),
            skus=raw.get("rewardSkus") or [],
            is_new_badge=bool(raw.get("new")),
            image_url=image_url,
            description=raw.get("description"),
            lifetime_limit=redemption_limits.get("lifetimeLimit"),
            timeframe_limit=redemption_limits.get("timeframeLimit"),
            overview_steps=overview_steps,
            overview_note=overview_note,
            locale=locale,
        )


class LegoRewardsClient:
    """Client for fetching LEGO Insiders Rewards via LEGO's GraphQL Gateway."""

    def __init__(self, locale: str = "en-PL", timeout: int = 20) -> None:
        self.locale = locale
        self.timeout = timeout

    def _post_graphql(
        self, operation_name: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{GRAPHQL_BASE_URL}/{operation_name}"
        payload = json.dumps(
            {
                "operationName": operation_name,
                "variables": variables,
                "query": query,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-locale": self.locale,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if "errors" in data and not data.get("data"):
                raise RuntimeError(f"GraphQL error in {operation_name}: {data['errors']}")
            return data.get("data") or {}

    def fetch_rewards_page(
        self, page: int = 1, size: int = 24, sort: str = "newest"
    ) -> tuple[list[RewardItem], dict[str, Any]]:
        variables = {
            "input": {
                "sort": sort,
                "filters": {},
                "pagination": {"page": page, "size": size},
                "sectionId": "rewards-listing",
            }
        }
        data = self._post_graphql("rewardListing", REWARD_LISTING_QUERY, variables)
        listing = data.get("rewardListing") or {}
        pagination = listing.get("paginationDetails") or {}
        raw_rewards = listing.get("rewards") or []

        items: list[RewardItem] = []
        for raw in raw_rewards:
            if not raw or raw.get("restrictedInCountry"):
                continue
            item = RewardItem.from_graphql(raw, locale=self.locale)
            if item.reward_id:
                items.append(item)
        return items, pagination

    def fetch_all_rewards(
        self, max_pages: int = 10, sort: str = "newest"
    ) -> list[RewardItem]:
        all_items: list[RewardItem] = []
        seen_ids: set[str] = set()
        page = 1
        while page <= max_pages:
            items, pagination = self.fetch_rewards_page(page=page, sort=sort)
            for item in items:
                if item.reward_id not in seen_ids:
                    seen_ids.add(item.reward_id)
                    all_items.append(item)
            last_page = int(pagination.get("lastPage") or 1)
            if page >= last_page or not items:
                break
            page += 1
        return all_items

    def enrich_with_details(self, item: RewardItem) -> RewardItem:
        """Fetches full reward details (description, theme, limits, steps) via rewardV4."""
        try:
            data = self._post_graphql(
                "Reward",
                REWARD_DETAIL_QUERY,
                {"input": {"rewardId": item.reward_id}},
            )
            raw = data.get("reward")
            if not raw:
                return item
            detailed = RewardItem.from_graphql(raw, locale=self.locale)
            # Preserve localized English category and image_url from listing if present
            if item.category:
                detailed.category = item.category
            if not detailed.image_url and item.image_url:
                detailed.image_url = item.image_url
            return detailed
        except (urllib.error.URLError, RuntimeError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Could not enrich reward %s (%s): %s", item.reward_id, item.title, exc
            )
            return item

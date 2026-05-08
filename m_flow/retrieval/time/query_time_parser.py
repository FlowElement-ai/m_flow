"""
Query time parsing module

Functions:
- Identify time expressions in queries (Chinese/English, relative/absolute)
- Convert time expressions to millisecond timestamp ranges
- Provide query text with time stripped (avoid date numbers polluting hybrid/number bonus)

Design principles:
- Conservative parsing: prefer false negatives over false positives
- Low confidence → weak bonus: reduce confidence when uncertain
- Wide interval strategy: parse vague expressions as wider time ranges
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from enum import Enum


class TimeType(Enum):
    """Time expression type"""

    EXPLICIT_DATE = "explicit_date"  # 2024年3月15日, 2024-03-15
    EXPLICIT_MONTH = "explicit_month"  # 2024年3月
    EXPLICIT_YEAR = "explicit_year"  # 2024年
    RELATIVE_DAY = "relative_day"  # 昨天, 今天, 明天
    RELATIVE_WEEK = "relative_week"  # 上周, 这周, 下周
    RELATIVE_MONTH = "relative_month"  # 上个月, 这个月
    RELATIVE_YEAR = "relative_year"  # 去年, 今年
    FUZZY = "fuzzy"  # 最近, 前阵子, recently


@dataclass
class TimeSpan:
    """Identified time span"""

    start_ms: int
    end_ms: int
    confidence: float
    matched_text: str
    time_type: TimeType

    @property
    def duration_days(self) -> float:
        """Duration in days"""
        return (self.end_ms - self.start_ms) / (1000 * 60 * 60 * 24)


@dataclass
class QueryTimeInfo:
    """Query time parsing result"""

    # Time range (union of all matches)
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    # Overall confidence
    confidence: float = 0.0
    # Query with time stripped (for vector retrieval)
    query_wo_time: str = ""
    # List of matched time spans
    matched_spans: List[TimeSpan] = field(default_factory=list)
    # Original query
    original_query: str = ""

    @property
    def has_time(self) -> bool:
        """Whether valid time is identified"""
        return self.start_ms is not None and self.confidence > 0

    @property
    def duration_days(self) -> float:
        """Duration in days"""
        if self.start_ms is None or self.end_ms is None:
            return 0
        return (self.end_ms - self.start_ms) / (1000 * 60 * 60 * 24)


# ============================================================
# Time expression pattern definitions
# ============================================================

# Chinese relative time words (long words first to ensure priority matching)
_CN_RELATIVE_DAYS = {
    # Long phrases first
    "昨天晚上": -1,
    "今天早上": 0,
    "今天晚上": 0,
    "今天下午": 0,
    "今天上午": 0,
    "前天晚上": -2,
    "明天早上": 1,
    "明天晚上": 1,
    # Colloquial (long words first)
    "一大早": 0,  # 今天一大早
    # Basic
    "昨天": -1,
    "昨日": -1,
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "前天": -2,
    "大前天": -3,
    "后天": 2,
    "大后天": 3,
    # Colloquial dates
    "今儿": 0,
    "明儿": 1,
    "昨儿": -1,  # Northern dialect
    "后儿": 2,
    "前儿": -2,
    # Morning/evening (mapped to full day)
    "昨晚": -1,
    "今晚": 0,
    "明早": 1,
    "今早": 0,
    "昨早": -1,
    "一早": 0,  # 今天一早
}

# Note: Long words first to ensure long words match first
_CN_RELATIVE_WEEKS = {
    # Long words first
    "上上一周": -2,
    "上上周": -2,
    "下下一周": 2,
    "下下周": 2,
    "上上礼拜": -2,
    "上上星期": -2,
    "下下礼拜": 2,
    "下下星期": 2,
    # Standard expressions
    "上一周": -1,
    "上周": -1,
    "下一周": 1,
    "下周": 1,
    "这周": 0,
    "本周": 0,
    # Colloquial: 礼拜/星期
    "上礼拜": -1,
    "这礼拜": 0,
    "下礼拜": 1,
    "上星期": -1,
    "这星期": 0,
    "下星期": 1,
    # Weekend (special handling, mapped to this weekend/last weekend)
    "这周末": 0,
    "上周末": -1,
    "下周末": 1,
    "这个周末": 0,
    "上个周末": -1,
    "下个周末": 1,
}

# Weekday with week reference (safe: has explicit week reference)
# Format: (week offset, weekday 0=Monday...6=Sunday)
_CN_WEEKDAY_WITH_REF = {
    # Last week + weekday
    "上周一": (-1, 0),
    "上周二": (-1, 1),
    "上周三": (-1, 2),
    "上周四": (-1, 3),
    "上周五": (-1, 4),
    "上周六": (-1, 5),
    "上周日": (-1, 6),
    "上周天": (-1, 6),  # colloquial
    "上礼拜一": (-1, 0),
    "上礼拜二": (-1, 1),
    "上礼拜三": (-1, 2),
    "上礼拜四": (-1, 3),
    "上礼拜五": (-1, 4),
    "上礼拜六": (-1, 5),
    "上礼拜天": (-1, 6),
    "上星期一": (-1, 0),
    "上星期二": (-1, 1),
    "上星期三": (-1, 2),
    "上星期四": (-1, 3),
    "上星期五": (-1, 4),
    "上星期六": (-1, 5),
    "上星期天": (-1, 6),
    # This week + weekday
    "这周一": (0, 0),
    "这周二": (0, 1),
    "这周三": (0, 2),
    "这周四": (0, 3),
    "这周五": (0, 4),
    "这周六": (0, 5),
    "这周日": (0, 6),
    "这周天": (0, 6),
    "本周一": (0, 0),
    "本周二": (0, 1),
    "本周三": (0, 2),
    "本周四": (0, 3),
    "本周五": (0, 4),
    "本周六": (0, 5),
    "本周日": (0, 6),
    "这礼拜一": (0, 0),
    "这礼拜二": (0, 1),
    "这礼拜三": (0, 2),
    "这礼拜四": (0, 3),
    "这礼拜五": (0, 4),
    "这礼拜六": (0, 5),
    "这礼拜天": (0, 6),
    "这星期一": (0, 0),
    "这星期二": (0, 1),
    "这星期三": (0, 2),
    "这星期四": (0, 3),
    "这星期五": (0, 4),
    "这星期六": (0, 5),
    "这星期天": (0, 6),
    # Next week + weekday
    "下周一": (1, 0),
    "下周二": (1, 1),
    "下周三": (1, 2),
    "下周四": (1, 3),
    "下周五": (1, 4),
    "下周六": (1, 5),
    "下周日": (1, 6),
    "下周天": (1, 6),
    "下礼拜一": (1, 0),
    "下礼拜二": (1, 1),
    "下礼拜三": (1, 2),
    "下礼拜四": (1, 3),
    "下礼拜五": (1, 4),
    "下礼拜六": (1, 5),
    "下礼拜天": (1, 6),
    "下星期一": (1, 0),
    "下星期二": (1, 1),
    "下星期三": (1, 2),
    "下星期四": (1, 3),
    "下星期五": (1, 4),
    "下星期六": (1, 5),
    "下星期天": (1, 6),
}

# Note: longer words go first
_CN_RELATIVE_MONTHS = {
    "上上个月": -2,
    "上上月": -2,  # longer words first
    "下下个月": 2,
    "下下月": 2,
    "上个月": -1,
    "上月": -1,
    "这个月": 0,
    "本月": 0,
    "下个月": 1,
    "下月": 1,
}

_CN_RELATIVE_YEARS = {
    "去年": -1,
    "前年": -2,
    "大前年": -3,
    "今年": 0,
    "明年": 1,
    "后年": 2,
    "大后年": 3,
}

# Chinese quarters (mapped to month offset, returns 3-month range of the quarter)
_CN_RELATIVE_QUARTERS = {
    "上季度": -1,
    "上个季度": -1,
    "本季度": 0,
    "这个季度": 0,
    "下季度": 1,
    "下个季度": 1,
}

# Chinese intra-year time periods
_CN_YEAR_PERIODS = {
    # (start_month, end_month, confidence)
    "年初": (1, 2, 0.6),  # Jan-Feb
    "年中": (5, 7, 0.5),  # May-Jul
    "年末": (11, 12, 0.6),  # Nov-Dec
    "年底": (11, 12, 0.6),
    "上半年": (1, 6, 0.7),
    "下半年": (7, 12, 0.7),
    # Colloquial
    "开年": (1, 2, 0.6),  # beginning of year
    "岁末": (11, 12, 0.5),  # end of year (slightly formal)
    "年关": (12, 12, 0.6),  # year-end (before lunar new year)
    "年头": (1, 2, 0.5),  # beginning of year (colloquial)
    "年尾": (11, 12, 0.5),  # end of year (colloquial)
}

# Chinese intra-month time periods
_CN_MONTH_PERIODS = {
    # (start_day, end_day, confidence)
    "月初": (1, 10, 0.6),
    "月中": (10, 20, 0.5),
    "月底": (20, 31, 0.6),
    "月末": (25, 31, 0.6),
    # Colloquial
    "月头": (1, 10, 0.5),  # beginning of month (colloquial)
    "月尾": (20, 31, 0.5),  # end of month (colloquial)
}

# Chinese fuzzy time words (only keep those that can independently determine time range, no coreference resolution needed)
# Note: longer phrases go first
_CN_FUZZY = {
    # Longer phrases first (sorted by length)
    "一个半月前": (45, 0.6),  # about 45 days ago
    "大半年前": (200, 0.4),  # about 6-9 months ago
    "半个月前": (15, 0.6),  # about 15 days ago
    "十来天前": (12, 0.5),  # about 10-12 days ago
    "好几天前": (6, 0.5),  # about 5-7 days ago
    "前些日子": (14, 0.5),  # some days ago
    "这段时间": (14, 0.4),  # this period (relative to current)
    "早些时候": (3, 0.5),  # earlier (today or recent days)
    # Quantity colloquial
    "头两天": (3, 0.6),  # first two-three days
    "这两天": (3, 0.7),  # last two days
    "前两天": (3, 0.7),  # two-three days ago
    "过两天": (3, 0.6),  # in two-three days (future)
    "头几天": (5, 0.6),  # first few days
    "前些天": (7, 0.6),  # some days ago
    "这些天": (7, 0.5),  # these days
    # Colloquial phrases
    "这阵子": (14, 0.5),  # lately (recently)
    "这会儿": (1, 0.6),  # right now (today)
    "眼下": (3, 0.5),  # at present (now/recently)
    "眼前": (3, 0.5),  # at present (now/recently)
    # Immediate expressions (mapped to today)
    "现在": (1, 0.7),  # now (today)
    "此刻": (1, 0.7),  # this moment (today)
    "当下": (1, 0.6),  # at present (today)
    "目前": (3, 0.5),  # currently (recent days)
    "如今": (7, 0.4),  # nowadays (recently)
    "当前": (3, 0.5),  # currently (recent days)
    "即刻": (1, 0.6),  # immediately
    "马上": (1, 0.5),  # right away
    "立刻": (1, 0.6),  # immediately
    "立即": (1, 0.6),  # immediately
    "顷刻": (1, 0.5),  # in an instant (literary)
    "即时": (1, 0.6),  # instant
    # Standard expressions
    "最近": (7, 0.5),  # recently, 7 days
    "近期": (14, 0.5),  # near term, 14 days
    "近来": (14, 0.4),  # lately, 14 days
    "近几天": (5, 0.6),  # last few days
    "这几天": (5, 0.6),  # these few days (relative to today)
    "刚才": (1, 0.6),  # just now (within today)
    "刚刚": (1, 0.6),  # just now (within today)
    "方才": (1, 0.5),  # just now (literary, within today)
    "不久前": (14, 0.5),  # not long ago
    "前阵子": (30, 0.4),  # a while ago
    # Not supported (require coreference resolution):
    # - "那段时间", "那时", "那时候", "那个时候", "当时" → need to know what "that/then" refers to
    # - "之前", "以前", "过去", "曾经" → no explicit reference point
}

# English relative time words (longer phrases first)
_EN_RELATIVE_DAYS = {
    # Longer phrases first
    "the day before yesterday": -2,
    "day before yesterday": -2,
    "the day after tomorrow": 2,
    "day after tomorrow": 2,
    # Colloquial time-of-day (mapped to the same day)
    "this morning": 0,
    "this afternoon": 0,
    "this evening": 0,
    "tonight": 0,
    "last night": -1,  # last night
    "tomorrow morning": 1,
    "tomorrow night": 1,
    "yesterday morning": -1,
    "yesterday afternoon": -1,
    "yesterday evening": -1,
    # Basic
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
}

# English week expressions (longer phrases first)
_EN_RELATIVE_WEEKS = {
    # Longer phrases first
    "the week before last": -2,
    "week before last": -2,
    "a couple of weeks ago": -2,
    "a few weeks back": -2,
    "a few weeks ago": -2,
    "two weeks ago": -2,
    # Weekend colloquial
    "over the weekend": 0,  # this weekend
    "this weekend": 0,
    "last weekend": -1,
    "next weekend": 1,
    # Basic
    "last week": -1,
    "this week": 0,
    "next week": 1,
}

# English weekday with week reference (safe: has explicit week reference)
# Format: (week offset, weekday 0=Monday...6=Sunday)
_EN_WEEKDAY_WITH_REF = {
    # last + weekday
    "last monday": (-1, 0),
    "last tuesday": (-1, 1),
    "last wednesday": (-1, 2),
    "last thursday": (-1, 3),
    "last friday": (-1, 4),
    "last saturday": (-1, 5),
    "last sunday": (-1, 6),
    # this + weekday
    "this monday": (0, 0),
    "this tuesday": (0, 1),
    "this wednesday": (0, 2),
    "this thursday": (0, 3),
    "this friday": (0, 4),
    "this saturday": (0, 5),
    "this sunday": (0, 6),
    # next + weekday
    "next monday": (1, 0),
    "next tuesday": (1, 1),
    "next wednesday": (1, 2),
    "next thursday": (1, 3),
    "next friday": (1, 4),
    "next saturday": (1, 5),
    "next sunday": (1, 6),
}

_EN_RELATIVE_MONTHS = {
    "last month": -1,
    "this month": 0,
    "next month": 1,
    # Extended
    "the month before last": -2,
    "month before last": -2,
    "two months ago": -2,
}

_EN_RELATIVE_YEARS = {
    "last year": -1,
    "this year": 0,
    "next year": 1,
    # Extended
    "the year before last": -2,
    "year before last": -2,
    "two years ago": -2,
}

# English quarters
_EN_RELATIVE_QUARTERS = {
    "last quarter": -1,
    "this quarter": 0,
    "next quarter": 1,
    "previous quarter": -1,
}

# English fuzzy time words (only keep those that can independently determine time range, no coreference resolution needed)
# Note: longer phrases first to ensure priority matching
_EN_FUZZY = {
    # Longest phrases first (sorted by length to ensure priority matching)
    "as recently as last week": (7, 0.7),
    "over the past few months": (90, 0.4),
    "in the last few months": (90, 0.4),
    "over the past few weeks": (21, 0.5),
    "in the last few weeks": (21, 0.5),
    "around two weeks ago": (14, 0.6),
    "about two weeks ago": (14, 0.6),
    "roughly a week ago": (7, 0.6),
    "around a week ago": (7, 0.6),
    "about a week ago": (7, 0.6),
    "half a month ago": (15, 0.6),
    "for the past few days": (5, 0.6),
    "in the last few days": (5, 0.6),
    "over the last few days": (5, 0.6),
    "over the past year": (365, 0.4),
    "over the past month": (30, 0.5),
    "over the past week": (7, 0.6),
    "in the past year": (365, 0.4),
    "in the past month": (30, 0.5),
    "in the past week": (7, 0.6),
    "a couple of days ago": (3, 0.7),
    "just last week": (7, 0.7),
    "only yesterday": (1, 0.8),
    "just yesterday": (1, 0.8),
    "a few days back": (5, 0.6),
    "a few days ago": (5, 0.6),
    "the past few days": (5, 0.6),
    "past few days": (5, 0.6),
    # Medium length
    "these days": (7, 0.5),
    "nowadays": (14, 0.4),
    "at the moment": (1, 0.6),
    "at present": (3, 0.5),
    "as of now": (1, 0.6),
    "as of today": (1, 0.7),
    "right now": (1, 0.7),
    "the other day": (7, 0.5),
    "not long ago": (14, 0.5),
    "a while ago": (30, 0.4),
    "a while back": (30, 0.4),
    "earlier today": (1, 0.7),
    "earlier on": (3, 0.5),
    # Short words
    "recently": (7, 0.5),
    "presently": (1, 0.5),
    "just now": (1, 0.6),
    "lately": (14, 0.4),
    "currently": (1, 0.5),
    # Immediate/near-term adverbs
    "immediately": (1, 0.5),  # mapped to today
    "shortly": (3, 0.4),  # mapped to recent days
    "soon": (7, 0.3),  # mapped to near term, low confidence
    # Not supported (require coreference resolution):
    # - "back then" → need to know what "then" refers to
    # - "before", "previously" → no explicit reference point when used alone
    # - "earlier" (alone) → could mean earlier today or before some event
}

# "N days/weeks/months ago" pattern - handled separately with regex
# These are dynamically parsed with regex in parse_query_time

# Explicit date regex patterns
_EXPLICIT_DATE_PATTERNS = [
    # 2024年3月15日 / 2024年3月15号
    (r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", "ymd_cn"),
    # 2024-03-15 / 2024/03/15 / 2024.03.15
    (r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", "ymd_iso"),
    # 3月15日 / 3月15号 (current year)
    (r"(\d{1,2})月(\d{1,2})[日号]", "md_cn"),
    # March 15, 2024 / Mar 15 2024 / 15 March 2024
    (
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})",
        "mdy_en",
    ),
    (
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?),?\s*(\d{4})",
        "dmy_en",
    ),
]

_EXPLICIT_MONTH_PATTERNS = [
    # 2024年3月 (Year-Month in Chinese)
    (r"(\d{4})年(\d{1,2})月", "ym_cn"),
    # 2024-03 / 2024/03
    (r"(\d{4})[-/](\d{1,2})(?![-/\d])", "ym_iso"),
    # March 2024 / Mar 2024
    (
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})",
        "my_en",
    ),
]

_EXPLICIT_YEAR_PATTERNS = [
    # 2024年 (Year in Chinese)
    (r"(\d{4})年(?!\d)", "y_cn"),
]

# Quarter patterns
_QUARTER_PATTERNS = [
    # Q1 2024 / 2024 Q1 / 2024Q1
    (r"[Qq]([1-4])\s*(\d{4})", "q_en"),
    (r"(\d{4})\s*[Qq]([1-4])", "yq_en"),
    # 2024年第一季度 / 2024年一季度 / 2024年1季度
    (r"(\d{4})年第?([一二三四1-4])季度?", "yq_cn"),
]

# "N days/weeks/months/years ago" regex patterns
_N_AGO_PATTERNS = [
    # 3 days ago / three days ago
    (r"(\d+)\s*days?\s+ago", "days_ago"),
    (r"(\d+)\s*weeks?\s+ago", "weeks_ago"),
    (r"(\d+)\s*months?\s+ago", "months_ago"),
    (r"(\d+)\s*years?\s+ago", "years_ago"),
    # Chinese: 3天前 / 三天前
    (r"(\d+)天前", "days_ago_cn"),
    (r"(\d+)周前", "weeks_ago_cn"),
    (r"(\d+)个?月前", "months_ago_cn"),
    (r"(\d+)年前", "years_ago_cn"),
]

# Month name mapping
_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# Chinese number mapping
_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
}


# ============================================================
# Quick screening
# ============================================================


def contains_time_hint(text: str) -> bool:
    """
    Quick check if text may contain time expressions.
    Used for quick screening before calling LLM extraction to reduce cost.

    Design principles:
    - Prefer false positives (high recall): this is just a quick screen, precise parsing follows
    - But avoid overly broad words (e.g., standalone "年" may cause false positives)
    """
    # Chinese time words (high certainty, no coreference resolution needed)
    cn_hints_safe = (
        # Relative dates
        "昨天",
        "今天",
        "明天",
        "前天",
        "后天",
        "昨日",
        "今日",
        "明日",
        "昨晚",
        "今晚",
        "明早",
        "今早",
        "昨早",
        "大前天",
        "大后天",
        "今儿",
        "明儿",
        "昨儿",
        "后儿",
        "前儿",  # northern dialect
        "一大早",
        "一早",  # time-of-day colloquial
        # Relative weeks
        "上周",
        "本周",
        "下周",
        "这周",
        "上一周",
        "下一周",
        "上上周",
        "下下周",
        "上礼拜",
        "这礼拜",
        "下礼拜",  # colloquial
        "上星期",
        "这星期",
        "下星期",  # colloquial
        "这周末",
        "上周末",
        "下周末",
        # Weekday with week reference
        "上周一",
        "上周二",
        "上周三",
        "上周四",
        "上周五",
        "上周六",
        "上周日",
        "上周天",
        "这周一",
        "这周二",
        "这周三",
        "这周四",
        "这周五",
        "这周六",
        "这周日",
        "这周天",
        "本周一",
        "本周二",
        "本周三",
        "本周四",
        "本周五",
        "本周六",
        "本周日",
        "下周一",
        "下周二",
        "下周三",
        "下周四",
        "下周五",
        "下周六",
        "下周日",
        "下周天",
        # Relative months
        "上个月",
        "本月",
        "下个月",
        "这个月",
        "上月",
        "下月",
        "上上个月",
        "下下个月",
        # Relative years
        "去年",
        "今年",
        "明年",
        "前年",
        "后年",
        "大前年",
        "大后年",
        # Quarters
        "季度",
        "上季度",
        "本季度",
        "下季度",
        # Fuzzy time (relative to current time, no coreference resolution needed)
        "最近",
        "近期",
        "近来",
        "近几天",
        "这几天",
        "这段时间",
        "这些天",
        "刚才",
        "刚刚",
        "方才",
        "不久前",
        "前阵子",
        "早些时候",
        "这阵子",
        "这会儿",
        "眼下",
        "眼前",  # colloquial
        "前些天",
        "前些日子",
        "头几天",  # colloquial
        "这两天",
        "前两天",
        "头两天",
        "过两天",  # quantity colloquial
        "好几天前",
        "十来天前",
        "半个月前",  # quantity colloquial
        "一个半月前",
        "大半年前",  # quantity colloquial
        # Immediate expressions (mapped to today/recently)
        "现在",
        "此刻",
        "当下",
        "目前",
        "如今",
        "当前",
        "即刻",
        "马上",
        "立刻",
        "立即",
        "顷刻",
        "即时",
        # Intra-year time periods
        "年初",
        "年中",
        "年末",
        "年底",
        "上半年",
        "下半年",
        "开年",
        "岁末",
        "年关",
        "年头",
        "年尾",  # colloquial
        # Intra-month time periods
        "月初",
        "月中",
        "月底",
        "月末",
        "月头",
        "月尾",
    )

    # Chinese time words (requiring numeric prefix)
    cn_hints_with_number = (
        "天前",
        "周前",
        "月前",
        "年前",
    )

    # English time words (no coreference resolution needed)
    en_hints = (
        # Relative dates
        "yesterday",
        "today",
        "tomorrow",
        "day before yesterday",
        "day after tomorrow",
        "just yesterday",
        "only yesterday",  # emphasis
        # Colloquial time-of-day
        "this morning",
        "this afternoon",
        "this evening",
        "tonight",
        "last night",
        "tomorrow morning",
        "tomorrow night",
        "yesterday morning",
        "yesterday afternoon",
        "yesterday evening",
        # Weekday with week reference
        "last monday",
        "last tuesday",
        "last wednesday",
        "last thursday",
        "last friday",
        "last saturday",
        "last sunday",
        "this monday",
        "this tuesday",
        "this wednesday",
        "this thursday",
        "this friday",
        "this saturday",
        "this sunday",
        "next monday",
        "next tuesday",
        "next wednesday",
        "next thursday",
        "next friday",
        "next saturday",
        "next sunday",
        # Relative weeks
        "last week",
        "this week",
        "next week",
        "week before last",
        "weeks ago",
        "this weekend",
        "last weekend",
        "next weekend",
        "over the weekend",
        "a few weeks ago",
        "a few weeks back",
        "a couple of weeks ago",
        "just last week",  # emphasis
        # Relative months
        "last month",
        "this month",
        "next month",
        "month before last",
        "months ago",
        "half a month ago",  # half a month
        # Relative years
        "last year",
        "this year",
        "next year",
        "year before last",
        "years ago",
        # Quarters
        "quarter",
        "last quarter",
        "this quarter",
        "next quarter",
        # Fuzzy (relative to current time)
        "recently",
        "lately",
        "just now",
        "currently",
        "presently",
        "nowadays",
        "these days",
        "at the moment",
        "right now",
        "at present",
        "as of now",
        "as of today",  # immediate
        "immediately",
        "shortly",
        "soon",  # near-term
        "days ago",
        "a while ago",
        "a while back",
        "not long ago",
        "past few days",
        "past week",
        "past month",
        "the past few days",
        "in the past week",
        "in the past month",
        "over the past week",
        "over the past month",
        "in the last few days",
        "over the last few days",
        "in the last few weeks",
        "over the past few weeks",
        "a few days ago",
        "a few days back",
        "a couple of days ago",
        "about a week ago",
        "around a week ago",
        "roughly a week ago",
        "about two weeks ago",
        "around two weeks ago",
        "the other day",
        "earlier today",
        "earlier on",
        # ago wildcard
        "ago",
    )

    text_lower = text.lower()

    # Check high-certainty Chinese words
    for hint in cn_hints_safe:
        if hint in text:
            return True

    # Check Chinese words requiring numeric prefix
    for hint in cn_hints_with_number:
        if hint in text and re.search(r"\d", text):
            return True

    # Check English words
    for hint in en_hints:
        if hint in text_lower:
            return True

    # Check date formats
    # 2024-03-15 / 2024/03/15 / 2024.03.15 / 2024年
    if re.search(r"\d{4}[-/.]", text):
        return True

    # Check Q1/Q2/Q3/Q4 quarter format
    if re.search(r"[Qq][1-4]", text):
        return True

    # Check English month names
    month_pattern = r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b"
    # Note: May excluded because it is also a modal verb
    if re.search(month_pattern, text, re.IGNORECASE):
        return True

    # Check Chinese date format: X月X日
    if re.search(r"\d{1,2}月\d{1,2}[日号]", text):
        return True

    return False


# ============================================================
# Core parsing function
# ============================================================


def parse_query_time(
    query: str,
    now_ms: Optional[int] = None,
    tz_offset_hours: float = 8.0,  # default UTC+8
) -> QueryTimeInfo:
    """
    Parse time expressions in query.

    Args:
        query: Original query text
        now_ms: Current timestamp (milliseconds), None to use system time
        tz_offset_hours: Timezone offset (hours), default UTC+8

    Returns:
        QueryTimeInfo: Parse result
    """
    if not query:
        return QueryTimeInfo(original_query=query, query_wo_time=query)

    # Current time
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    now_dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    # Adjust to local timezone for date boundary calculation
    local_offset = timedelta(hours=tz_offset_hours)
    now_local = now_dt + local_offset

    matched_spans: List[TimeSpan] = []
    positions_to_remove: List[Tuple[int, int]] = []

    # 1. Parse explicit dates (highest priority)
    for pattern, ptype in _EXPLICIT_DATE_PATTERNS:
        for m in re.finditer(pattern, query):
            span = _parse_explicit_date(m, ptype, now_local)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((m.start(), m.end()))

    # 2. Parse explicit months
    for pattern, ptype in _EXPLICIT_MONTH_PATTERNS:
        for m in re.finditer(pattern, query):
            # Avoid overlap with already matched dates
            if _overlaps(m.start(), m.end(), positions_to_remove):
                continue
            span = _parse_explicit_month(m, ptype, now_local)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((m.start(), m.end()))

    # 3. Parse explicit years
    for pattern, _ptype in _EXPLICIT_YEAR_PATTERNS:
        for m in re.finditer(pattern, query):
            if _overlaps(m.start(), m.end(), positions_to_remove):
                continue
            span = _parse_explicit_year(m, now_local)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((m.start(), m.end()))

    # 4. Parse Chinese relative time
    for word, offset in _CN_RELATIVE_DAYS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_day_span(now_local, offset, word, TimeType.RELATIVE_DAY)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    # 4.0.1 Parse weekday with week reference (Chinese) - parse before weeks to ensure longer words match first
    for word, (week_offset, weekday) in _CN_WEEKDAY_WITH_REF.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_weekday_span(now_local, week_offset, weekday, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _CN_RELATIVE_WEEKS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_week_span(now_local, offset, word, TimeType.RELATIVE_WEEK)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _CN_RELATIVE_MONTHS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_month_span(now_local, offset, word, TimeType.RELATIVE_MONTH)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _CN_RELATIVE_YEARS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_year_span(now_local, offset, word, TimeType.RELATIVE_YEAR)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, (days, conf) in _CN_FUZZY.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_fuzzy_span(now_local, days, conf, word)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    # 5. Parse English relative time
    query_lower = query.lower()

    for word, offset in _EN_RELATIVE_DAYS.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_day_span(now_local, offset, word, TimeType.RELATIVE_DAY)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    # 5.0.0 Parse weekday with week reference (English) - parse before weeks to ensure longer words match first
    for word, (week_offset, weekday) in _EN_WEEKDAY_WITH_REF.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_weekday_span(now_local, week_offset, weekday, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _EN_RELATIVE_WEEKS.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_week_span(now_local, offset, word, TimeType.RELATIVE_WEEK)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _EN_RELATIVE_MONTHS.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_month_span(now_local, offset, word, TimeType.RELATIVE_MONTH)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _EN_RELATIVE_YEARS.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_year_span(now_local, offset, word, TimeType.RELATIVE_YEAR)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    for word, (days, conf) in _EN_FUZZY.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_fuzzy_span(now_local, days, conf, word)
            matched_spans.append(span)
            positions_to_remove.append((idx, idx + len(word)))

    # 5.1 Parse quarters (Chinese and English)
    for word, offset in _CN_RELATIVE_QUARTERS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_quarter_span(now_local, offset, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    for word, offset in _EN_RELATIVE_QUARTERS.items():
        idx = query_lower.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_quarter_span(now_local, offset, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    # 5.2 Parse "N days/weeks/months/years ago" patterns
    for pattern, ptype in _N_AGO_PATTERNS:
        for m in re.finditer(pattern, query if "cn" in ptype else query_lower):
            if _overlaps(m.start(), m.end(), positions_to_remove):
                continue
            span = _parse_n_ago(m, ptype, now_local)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((m.start(), m.end()))

    # 5.3 Parse intra-year time periods (beginning/middle/end of year)
    for word, (start_month, end_month, conf) in _CN_YEAR_PERIODS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_year_period_span(now_local, start_month, end_month, conf, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    # 5.4 Parse intra-month time periods (beginning/middle/end of month)
    for word, (start_day, end_day, conf) in _CN_MONTH_PERIODS.items():
        idx = query.find(word)
        if idx >= 0 and not _overlaps(idx, idx + len(word), positions_to_remove):
            span = _make_month_period_span(now_local, start_day, end_day, conf, word)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((idx, idx + len(word)))

    # 5.5 Parse quarter regex patterns (Q1 2024 / 2024Q1 / 2024年第一季度)
    for pattern, ptype in _QUARTER_PATTERNS:
        for m in re.finditer(pattern, query):
            if _overlaps(m.start(), m.end(), positions_to_remove):
                continue
            span = _parse_explicit_quarter(m, ptype)
            if span:
                matched_spans.append(span)
                positions_to_remove.append((m.start(), m.end()))

    # 6. Build query with time stripped
    query_wo_time = _remove_positions(query, positions_to_remove)
    query_wo_time = re.sub(r"\s+", " ", query_wo_time).strip()

    # 7. Merge time ranges
    if matched_spans:
        # Take union of all matches
        start_ms = min(s.start_ms for s in matched_spans)
        end_ms = max(s.end_ms for s in matched_spans)
        # Take highest confidence
        confidence = max(s.confidence for s in matched_spans)
    else:
        start_ms = None
        end_ms = None
        confidence = 0.0

    return QueryTimeInfo(
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
        query_wo_time=query_wo_time if query_wo_time else query,
        matched_spans=matched_spans,
        original_query=query,
    )


# ============================================================
# Helper functions
# ============================================================


def _overlaps(start: int, end: int, positions: List[Tuple[int, int]]) -> bool:
    """Check if interval overlaps with existing positions."""
    for ps, pe in positions:
        if not (end <= ps or start >= pe):
            return True
    return False


def _remove_positions(text: str, positions: List[Tuple[int, int]]) -> str:
    """Remove specified positions from text."""
    if not positions:
        return text

    # Sort by start position
    positions = sorted(positions, key=lambda x: x[0])

    result = []
    last_end = 0
    for start, end in positions:
        if start > last_end:
            result.append(text[last_end:start])
        last_end = max(last_end, end)

    if last_end < len(text):
        result.append(text[last_end:])

    return "".join(result)


def _parse_explicit_date(m: re.Match, ptype: str, now: datetime) -> Optional[TimeSpan]:
    """Parse explicit date."""
    try:
        if ptype == "ymd_cn" or ptype == "ymd_iso":
            year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))
        elif ptype == "md_cn":
            year = now.year
            month = int(m.group(1))
            day = int(m.group(2))
        else:
            return None

        # Validate date
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None

        dt = datetime(year, month, day, tzinfo=timezone.utc)
        start_ms = int(dt.timestamp() * 1000)
        end_ms = int((dt + timedelta(days=1)).timestamp() * 1000)

        return TimeSpan(
            start_ms=start_ms,
            end_ms=end_ms,
            confidence=0.95,  # high confidence for explicit dates
            matched_text=m.group(0),
            time_type=TimeType.EXPLICIT_DATE,
        )
    except (ValueError, OverflowError):
        return None


_MONTH_NAME_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_explicit_month(m: re.Match, ptype: str, now: datetime) -> Optional[TimeSpan]:
    """Parse explicit month."""
    try:
        if ptype == "my_en":
            # Pattern: (MonthName) (Year) — group(1)=month name, group(2)=year
            month_str = m.group(1).lower()
            month = _MONTH_NAME_MAP.get(month_str)
            if month is None:
                return None
            year = int(m.group(2))
        else:
            # ym_cn, ym_iso: group(1)=year, group(2)=month number
            year = int(m.group(1))
            month = int(m.group(2))

        if not (1 <= month <= 12):
            return None

        dt_start = datetime(year, month, 1, tzinfo=timezone.utc)

        # Calculate end of month
        if month == 12:
            dt_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            dt_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(dt_start.timestamp() * 1000),
            end_ms=int(dt_end.timestamp() * 1000),
            confidence=0.90,
            matched_text=m.group(0),
            time_type=TimeType.EXPLICIT_MONTH,
        )
    except (ValueError, OverflowError):
        return None


def _parse_explicit_year(m: re.Match, now: datetime) -> Optional[TimeSpan]:
    """Parse explicit year."""
    try:
        year = int(m.group(1))

        dt_start = datetime(year, 1, 1, tzinfo=timezone.utc)
        dt_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(dt_start.timestamp() * 1000),
            end_ms=int(dt_end.timestamp() * 1000),
            confidence=0.85,  # wider year range, slightly lower confidence
            matched_text=m.group(0),
            time_type=TimeType.EXPLICIT_YEAR,
        )
    except (ValueError, OverflowError):
        return None


def _make_day_span(now: datetime, offset: int, text: str, ttype: TimeType) -> TimeSpan:
    """Create day-level time span."""
    # Calculate target date (date only, no time)
    target = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset)

    start_ms = int(target.timestamp() * 1000)
    end_ms = int((target + timedelta(days=1)).timestamp() * 1000)

    return TimeSpan(
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=0.9,
        matched_text=text,
        time_type=ttype,
    )


def _make_week_span(now: datetime, offset: int, text: str, ttype: TimeType) -> TimeSpan:
    """Create week-level time span."""
    # Calculate Monday of target week
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = today.weekday()
    monday = today - timedelta(days=weekday) + timedelta(weeks=offset)
    sunday = monday + timedelta(days=7)

    return TimeSpan(
        start_ms=int(monday.timestamp() * 1000),
        end_ms=int(sunday.timestamp() * 1000),
        confidence=0.85,
        matched_text=text,
        time_type=ttype,
    )


def _make_weekday_span(
    now: datetime,
    week_offset: int,
    target_weekday: int,  # 0=Monday, 6=Sunday
    text: str,
) -> Optional[TimeSpan]:
    """Create time span for a specific weekday."""
    try:
        # Calculate Monday of target week
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        current_weekday = today.weekday()
        monday = today - timedelta(days=current_weekday) + timedelta(weeks=week_offset)

        # Target date
        target = monday + timedelta(days=target_weekday)

        start_ms = int(target.timestamp() * 1000)
        end_ms = int((target + timedelta(days=1)).timestamp() * 1000)

        return TimeSpan(
            start_ms=start_ms,
            end_ms=end_ms,
            confidence=0.9,  # high confidence for weekday with week reference
            matched_text=text,
            time_type=TimeType.RELATIVE_DAY,
        )
    except (ValueError, OverflowError):
        return None


def _make_month_span(now: datetime, offset: int, text: str, ttype: TimeType) -> TimeSpan:
    """Create month-level time span."""
    # Calculate target month
    year = now.year
    month = now.month + offset

    # Handle year wrap-around
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1

    start = datetime(year, month, 1, tzinfo=timezone.utc)

    # Calculate start of next month
    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1
    end = datetime(next_year, next_month, 1, tzinfo=timezone.utc)

    return TimeSpan(
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
        confidence=0.8,
        matched_text=text,
        time_type=ttype,
    )


def _make_year_span(now: datetime, offset: int, text: str, ttype: TimeType) -> TimeSpan:
    """Create year-level time span."""
    year = now.year + offset

    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

    return TimeSpan(
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
        confidence=0.75,  # wide year range, lower confidence
        matched_text=text,
        time_type=ttype,
    )


def _make_fuzzy_span(now: datetime, days_back: int, confidence: float, text: str) -> TimeSpan:
    """Create fuzzy time span."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=1)  # include today

    return TimeSpan(
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
        confidence=confidence,
        matched_text=text,
        time_type=TimeType.FUZZY,
    )


def _make_quarter_span(now: datetime, offset: int, text: str) -> Optional[TimeSpan]:
    """Create quarter-level time span."""
    try:
        # Current quarter (1-4)
        current_quarter = (now.month - 1) // 3 + 1
        current_year = now.year

        # Target quarter
        target_quarter = current_quarter + offset
        target_year = current_year

        # Handle year wrap-around
        while target_quarter <= 0:
            target_quarter += 4
            target_year -= 1
        while target_quarter > 4:
            target_quarter -= 4
            target_year += 1

        # Quarter start month
        start_month = (target_quarter - 1) * 3 + 1
        end_month = start_month + 3
        end_year = target_year

        if end_month > 12:
            end_month = 1
            end_year += 1

        start = datetime(target_year, start_month, 1, tzinfo=timezone.utc)
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            confidence=0.8,
            matched_text=text,
            time_type=TimeType.RELATIVE_MONTH,  # treat quarter as month-level
        )
    except (ValueError, OverflowError):
        return None


def _parse_n_ago(m: re.Match, ptype: str, now: datetime) -> Optional[TimeSpan]:
    """Parse 'N days/weeks/months/years ago' patterns."""
    try:
        n = int(m.group(1))

        # Safety limit: do not process excessively large numbers
        if n > 100:
            return None

        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if "days" in ptype or "天" in ptype:
            start = today - timedelta(days=n)
            end = today - timedelta(days=n - 1)
            confidence = 0.85 if n <= 7 else 0.7
        elif "weeks" in ptype or "周" in ptype:
            # N weeks ago refers to that entire week
            start = today - timedelta(weeks=n, days=today.weekday())
            end = start + timedelta(days=7)
            confidence = 0.75
        elif "months" in ptype or "月" in ptype:
            # Simplified: N months ago ≈ N*30 days ago
            days_ago = n * 30
            start = today - timedelta(days=days_ago + 15)  # mid-month
            end = today - timedelta(days=days_ago - 15)
            confidence = 0.65
        elif "years" in ptype or "年" in ptype:
            target_year = now.year - n
            start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
            end = datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)
            confidence = 0.6
        else:
            return None

        return TimeSpan(
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            confidence=confidence,
            matched_text=m.group(0),
            time_type=TimeType.RELATIVE_DAY if "days" in ptype or "天" in ptype else TimeType.FUZZY,
        )
    except (ValueError, OverflowError):
        return None


def _make_year_period_span(
    now: datetime,
    start_month: int,
    end_month: int,
    confidence: float,
    text: str,
) -> Optional[TimeSpan]:
    """Create intra-year time period span (beginning/middle/end of year, etc.)."""
    try:
        year = now.year

        start = datetime(year, start_month, 1, tzinfo=timezone.utc)

        # Calculate end time
        if end_month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, end_month + 1, 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            confidence=confidence,
            matched_text=text,
            time_type=TimeType.FUZZY,
        )
    except (ValueError, OverflowError):
        return None


def _make_month_period_span(
    now: datetime,
    start_day: int,
    end_day: int,
    confidence: float,
    text: str,
) -> Optional[TimeSpan]:
    """Create intra-month time period span (beginning/middle/end of month, etc.)."""
    try:
        year = now.year
        month = now.month

        # Ensure end_day does not exceed the number of days in the month
        import calendar

        max_day = calendar.monthrange(year, month)[1]
        end_day = min(end_day, max_day)

        start = datetime(year, month, start_day, tzinfo=timezone.utc)

        # End of end_day = start of end_day + 1
        if end_day >= max_day:
            # End of month, jump to next month
            if month == 12:
                end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month, end_day + 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            confidence=confidence,
            matched_text=text,
            time_type=TimeType.FUZZY,
        )
    except (ValueError, OverflowError):
        return None


def _parse_explicit_quarter(m: re.Match, ptype: str) -> Optional[TimeSpan]:
    """Parse explicit quarter (Q1 2024 / 2024Q1 / 2024年第一季度)."""
    try:
        if ptype == "q_en":
            quarter = int(m.group(1))
            year = int(m.group(2))
        elif ptype == "yq_en":
            year = int(m.group(1))
            quarter = int(m.group(2))
        elif ptype == "yq_cn":
            year = int(m.group(1))
            q_str = m.group(2)
            quarter = _CN_NUMBERS.get(q_str, 0)
            if quarter == 0:
                return None
        else:
            return None

        if not (1 <= quarter <= 4):
            return None

        # Quarter start month
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 3
        end_year = year

        if end_month > 12:
            end_month = 1
            end_year += 1

        start = datetime(year, start_month, 1, tzinfo=timezone.utc)
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)

        return TimeSpan(
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            confidence=0.9,  # high confidence for explicit quarters
            matched_text=m.group(0),
            time_type=TimeType.EXPLICIT_MONTH,
        )
    except (ValueError, OverflowError):
        return None

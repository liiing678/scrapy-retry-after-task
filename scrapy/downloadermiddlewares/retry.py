"""
An extension to retry failed requests that are potentially caused by temporary
problems such as a connection timeout or HTTP 500 error.

You can change the behaviour of this middleware by modifying the scraping settings:
RETRY_TIMES - how many times to retry a failed page
RETRY_HTTP_CODES - which HTTP response codes to retry
"""

from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from logging import Logger, getLevelNamesMapping, getLogger
from time import time
from typing import TYPE_CHECKING, Self

from scrapy import Spider, signals
from scrapy.exceptions import NotConfigured
from scrapy.utils.decorators import _warn_spider_arg
from scrapy.utils.misc import _load_objects
from scrapy.utils.python import global_object_name, to_unicode
from scrapy.utils.response import response_status_message

if TYPE_CHECKING:
    import scrapy
    from scrapy.crawler import Crawler
    from scrapy.http import Response
    from scrapy.http.request import Request
    from scrapy.settings import BaseSettings


retry_logger = getLogger(__name__)


def _parse_retry_after(value: str | bytes | None) -> float | None:
    """Return the delay in seconds asked by a Retry-After header *value*.

    The header carries either a number of seconds or an HTTP date. Dates are
    measured against the wall clock, so that a response generated before the
    request reached this process is handled like a zero delay. Anything that is
    neither a non-negative number nor a valid date is ignored.
    """
    if not value:
        return None
    value = to_unicode(value, encoding="latin1").strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if date.tzinfo is None:
            # HTTP dates always use GMT; an asctime()-format date has no zone.
            date = date.replace(tzinfo=UTC)
        seconds = date.timestamp() - time()
    if seconds < 0:
        return None
    return seconds


def get_retry_request(
    request: Request,
    *,
    spider: scrapy.Spider,
    reason: str | Exception | type[Exception] = "unspecified",
    max_retry_times: int | None = None,
    priority_adjust: int | None = None,
    logger: Logger = retry_logger,
    give_up_log_level: int | str | None = None,
    stats_base_key: str = "retry",
) -> Request | None:
    """
    Returns a new :class:`~scrapy.Request` object to retry the specified
    request, or ``None`` if retries of the specified request have been
    exhausted.

    For example, in a :class:`~scrapy.Spider` callback, you could use it as
    follows:

    .. code-block:: python

        def parse(self, response):
            if not response.text:
                new_request_or_none = get_retry_request(
                    response.request,
                    spider=self,
                    reason="empty",
                )
                return new_request_or_none

    *spider* is the :class:`~scrapy.Spider` instance which is asking for the
    retry request. It is used to access the :ref:`settings <topics-settings>`
    and :ref:`stats <topics-stats>`, and to provide extra logging context (see
    :func:`logging.debug`).

    *reason* is a string, an :class:`Exception` subclass or an
    :class:`Exception` object that indicates the reason why the request needs
    to be retried. It is used to name retry stats.

    *max_retry_times* is a number that determines the maximum number of times
    that *request* can be retried. If not specified or ``None``, the number is
    read from the :reqmeta:`max_retry_times` meta key of the request. If the
    :reqmeta:`max_retry_times` meta key is not defined or ``None``, the number
    is read from the :setting:`RETRY_TIMES` setting.

    *priority_adjust* is a number that determines how the priority of the new
    request changes in relation to *request*. If not specified, the number is
    read from the :setting:`RETRY_PRIORITY_ADJUST` setting.

    *logger* is the logging.Logger object to be used when logging messages

    *give_up_log_level* is the :ref:`logging level <levels>` used for the
    message logged when a request exceeds its retries. See
    :setting:`RETRY_GIVE_UP_LOG_LEVEL` for details.

    .. versionadded:: 2.17.0
       The *give_up_log_level* parameter.

    *stats_base_key* is a string to be used as the base key for the
    retry-related job stats
    """
    settings = spider.crawler.settings
    stats = spider.crawler.stats
    retry_times = request.meta.get("retry_times", 0) + 1
    if max_retry_times is None:
        max_retry_times = request.meta.get("max_retry_times")
        if max_retry_times is None:
            max_retry_times = settings.getint("RETRY_TIMES")
    if retry_times <= max_retry_times:
        logger.debug(
            "Retrying %(request)s (failed %(retry_times)d times): %(reason)s",
            {"request": request, "retry_times": retry_times, "reason": reason},
            extra={"spider": spider},
        )
        new_request: Request = request.copy()
        new_request.meta["retry_times"] = retry_times
        new_request.meta["skip_dupefilter_once"] = True
        if priority_adjust is None:
            priority_adjust = settings.getint("RETRY_PRIORITY_ADJUST")
        new_request.priority = request.priority + priority_adjust

        if callable(reason):
            reason = reason()
        if isinstance(reason, Exception):
            reason = global_object_name(reason.__class__)

        stats.inc_value(f"{stats_base_key}/count")
        stats.inc_value(f"{stats_base_key}/reason_count/{reason}")
        return new_request
    if give_up_log_level is None:
        give_up_log_level = settings["RETRY_GIVE_UP_LOG_LEVEL"]
    if isinstance(give_up_log_level, str):
        level = getLevelNamesMapping().get(give_up_log_level)
        if level is None:
            raise ValueError(f"Invalid give-up log level: {give_up_log_level!r}")
        give_up_log_level = level
    stats.inc_value(f"{stats_base_key}/max_reached")
    assert give_up_log_level is not None
    logger.log(
        give_up_log_level,
        "Gave up retrying %(request)s (failed %(retry_times)d times): %(reason)s",
        {"request": request, "retry_times": retry_times, "reason": reason},
        extra={"spider": spider},
    )
    return None


class RetryMiddleware:
    crawler: Crawler

    def __init__(self, settings: BaseSettings):
        if not settings.getbool("RETRY_ENABLED"):
            raise NotConfigured
        self.max_retry_times = settings.getint("RETRY_TIMES")
        self.retry_http_codes = {int(x) for x in settings.getlist("RETRY_HTTP_CODES")}
        self.priority_adjust = settings.getint("RETRY_PRIORITY_ADJUST")
        self.give_up_log_level = settings["RETRY_GIVE_UP_LOG_LEVEL"]
        self.exceptions_to_retry = _load_objects(settings.getlist("RETRY_EXCEPTIONS"))
        self.retry_after_cooldown_enabled = settings.getbool(
            "RETRY_AFTER_COOLDOWN_ENABLED"
        )
        self.retry_after_cooldown_max_delay = settings.getfloat(
            "RETRY_AFTER_COOLDOWN_MAX_DELAY"
        )
        self._retry_after_statuses = self.retry_http_codes & {429, 503}

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        o = cls(crawler.settings)
        o.crawler = crawler
        # The signal fires inside the downloader, before the transfer of the
        # request ends and its slot is offered back to queued requests, which
        # is when the cooldown has to be in place for them to honor it.
        if o.retry_after_cooldown_enabled:
            crawler.signals.connect(
                o._response_downloaded, signal=signals.response_downloaded
            )
        return o

    def _response_downloaded(
        self, response: Response, request: Request, spider: Spider
    ) -> None:
        if (
            response.status not in self._retry_after_statuses
            or request.meta.get("dont_retry", False)
            or request.meta.get("retry_times", 0)
            >= request.meta.get("max_retry_times", self.max_retry_times)
        ):
            return
        self._apply_retry_after_cooldown(request, response)

    @_warn_spider_arg
    def process_response(
        self,
        request: Request,
        response: Response,
        spider: scrapy.Spider | None = None,
    ) -> Request | Response:
        if request.meta.get("dont_retry", False):
            return response
        if response.status in self.retry_http_codes:
            reason = response_status_message(response.status)
            return self._retry(request, reason) or response
        return response

    def _apply_retry_after_cooldown(self, request: Request, response: Response) -> None:
        """Hold the download slot of *request* back for the Retry-After header
        of *response*, if it carries a usable value.
        """
        delay = _parse_retry_after(response.headers.get("Retry-After"))
        if delay is None:
            return
        if self.retry_after_cooldown_max_delay:
            delay = min(delay, self.retry_after_cooldown_max_delay)
        self.crawler.engine.downloader._set_slot_cooldown(request, delay)
        retry_logger.debug(
            f"Cooling down download slot for {delay:g}s after "
            f"Retry-After on {request.url}"
        )

    @_warn_spider_arg
    def process_exception(
        self,
        request: Request,
        exception: Exception,
        spider: scrapy.Spider | None = None,
    ) -> Request | Response | None:
        if isinstance(exception, self.exceptions_to_retry) and not request.meta.get(
            "dont_retry", False
        ):
            return self._retry(request, exception)
        return None

    def _retry(
        self, request: Request, reason: str | Exception | type[Exception]
    ) -> Request | None:
        max_retry_times = request.meta.get("max_retry_times", self.max_retry_times)
        priority_adjust = request.meta.get("priority_adjust", self.priority_adjust)
        give_up_log_level = request.meta.get(
            "give_up_log_level", self.give_up_log_level
        )
        assert self.crawler.spider
        return get_retry_request(
            request,
            reason=reason,
            spider=self.crawler.spider,
            max_retry_times=max_retry_times,
            priority_adjust=priority_adjust,
            give_up_log_level=give_up_log_level,
        )

#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import json
import logging
import logging.config
import queue
from json.decoder import JSONDecodeError
from logging.handlers import QueueHandler, QueueListener
from typing import Any, Callable, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

import parsedatetime
from requests.exceptions import HTTPError
from requests.models import Response

import shopgoodwill


def get_timedelta_to_time(
    end_time: datetime.datetime, truncate_microseconds: Optional[bool] = True
) -> datetime.timedelta:
    """
    Given a datetime object (hopefully in the future),
    return a timedelta which is timezone aware or unaware,
    depending on the input datetime

    :param end_time: A datetime object (timezone aware or unaware)
        from which to get a timedelta from now until then
    :type end_time: datetime.datetime
    :param truncate_microseconds: If set, truncate microseconds from datetimes
    :type truncate_microseconds: Optional[bool]
    :return: A timedelta from end_time to now
    :rtype: datetime.timedelta
    """

    if truncate_microseconds:
        end_time = end_time.replace(microsecond=0)
        now = datetime.datetime.now().replace(microsecond=0)

    else:
        now = datetime.datetime.now()

    if end_time.tzinfo is None:
        return end_time - now

    else:
        # by default, astimezone will return the local timezone
        return end_time - now.astimezone()


class BidSniper:
    def outage_check_hook(self, http_response: Response, *args, **kwargs):
        if http_response.status_code in range(500, 600):
            if self.outage_start_time is None:
                # start tracking this outage
                self.outage_start_time = datetime.datetime.now(datetime.timezone.utc)
                self.logger.error(
                    f"Outage detected - SGW returned HTTP {http_response.status_code} for URL {http_response.url}"
                )
            else:
                # already tracking this error, don't do anything
                pass

        else:
            # If there's no error and there was an ongoing outage,
            # stop tracking it and alert on the elapsed time
            if self.outage_start_time is not None:
                elapsed_outage_time = (
                    datetime.datetime.now(datetime.timezone.utc)
                    - self.outage_start_time
                )
                self.outage_start_time = None

                self.logger.info(f"Outage ended - time elapsed: {elapsed_outage_time}")

        # TODO this should instead call SGW's shopgoodwill_err_hook method
        # TODO move the above from a function to a method!
        http_response.raise_for_status()

    def __init__(self, config: Dict, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.dry_run_msg = "DRY-RUN: " if dry_run else ""

        self.event_loop = asyncio.new_event_loop()

        self.outage_start_time = None

        # if set, the default note to assign favorites that don't have a note
        self.default_note = self.config["bid_sniper"].get("favorite_default_note", None)

        self.date_format = "%Y-%m-%dT%H:%M:%S"

        # logging setup
        logging_conf = config.get("logging", dict())
        self.logger = logging.getLogger("shopgoodwill_bid_sniper")
        # check if we're using logging.config.dictConfig or not
        #
        # load entire logging config from dictConfig format
        if logging_conf.get("version", 0) >= 1:
            logging.config.dictConfig(logging_conf)

        # legacy logging config format
        else:
            logging.basicConfig()
            self.logger.setLevel(logging_conf.get("log_level", logging.INFO))
            log_queue = queue.Queue()
            queue_handler = QueueHandler(log_queue)

            if "gotify" in logging_conf:
                from gotify_handler import GotifyHandler

                queue_listener = QueueListener(
                    log_queue, GotifyHandler(**logging_conf["gotify"])
                )
                self.logger.addHandler(queue_handler)
                queue_listener.start()

        # TODO since this is a daemon,
        # we'll actually have to bother refreshing the token!

        # check if the user wants to use separate accounts for commands/bids
        # (purely for ban evasion)
        if config["auth_info"].get("auth_type", "universal") == "command_bid":
            self.shopgoodwill_client = shopgoodwill.Shopgoodwill(
                config["auth_info"]["command_account"]
            )
            self.bid_shopgoodwill_client = shopgoodwill.Shopgoodwill(
                config["auth_info"]["bid_account"]
            )

        else:
            self.shopgoodwill_client = shopgoodwill.Shopgoodwill(config["auth_info"])
            self.bid_shopgoodwill_client = self.shopgoodwill_client

        # modify the hooks for our shopgoodwill sessions
        self.shopgoodwill_client.shopgoodwill_session.hooks["response"] = (
            self.outage_check_hook
        )
        self.bid_shopgoodwill_client.shopgoodwill_session.hooks["response"] = (
            self.outage_check_hook
        )

        # custom time alerting setup
        self.alert_time_deltas = list()
        cal = parsedatetime.Calendar()
        for time_delta_str in config["bid_sniper"].get("alert_time_deltas", list()):
            time_delta = (
                cal.parseDT(time_delta_str, sourceTime=datetime.datetime.min)[0]
                - datetime.datetime.min
            )
            if time_delta != datetime.timedelta(0):
                self.alert_time_deltas.append(time_delta)
            else:
                self.logger.warning("Invalid time delta string '{time_delta_str}'")

        # bid placing setup
        bid_time_delta_str = self.config["bid_sniper"].get(
            "bid_snipe_time_delta", "30 seconds"
        )
        self.bid_time_delta = (
            cal.parseDT(bid_time_delta_str, sourceTime=datetime.datetime.min)[0]
            - datetime.datetime.min
        )
        if self.bid_time_delta == datetime.timedelta(0):
            self.logger.warning("Invalid time delta string '{time_delta_str}'")

        self.favorites_cache = {
            "last_updated": datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc),
            "favorites": dict(),
        }

        self.scheduled_tasks = (
            set()
        )  # Will contain itemIds tentatively scheduled for actions

        return

    def update_favorites_cache(self, max_cache_time: int) -> None:
        """
        Simple function to update the local favorites cache

        :param max_cache_time: The number of seconds
            after which the cache should be refreshed
        :type max_cache_time: int
        :rtype: None
        """

        if (
            datetime.datetime.now(datetime.timezone.utc)
            - self.favorites_cache["last_updated"]
        ).seconds > max_cache_time:
            try:
                self.favorites_cache = {
                    "favorites": self.shopgoodwill_client.get_favorites(),
                    "last_updated": datetime.datetime.now(datetime.timezone.utc),
                }

            except BaseException as be:
                # TODO this should list all possible exceptions that SGW could raise
                if self.outage_start_time is not None:
                    self.logger.error(
                        f"{type(be).__name__} updating favorites cache - {be}"
                    )

    def task_err_handler(self, finished_task: asyncio.Task) -> None:
        """
        Simple Task callback function to log exceptions from tasks, if present

        :param finished_task:
        :type finished_task: asyncio.Task
        :rtype: None
        """

        coro_exception = finished_task.exception()
        if coro_exception:
            self.logger.error(
                f'Exception in coroutine "{getattr(finished_task.get_coro(), "__name__", "null")}" - {type(coro_exception).__name__} - {coro_exception}'
            )

    async def schedule_task(
        self,
        coroutine,
        execution_datetime: datetime.datetime,
        callbacks: Optional[Iterable[Callable[[asyncio.Task], Any]]] = None,
    ) -> None:
        """
        Simple function to delay a coroutine's execution
        until a given timezone-aware datetime

        :param coroutine: The coroutine to execute at execution_datetime
        :param execution_datetime: A timezone-aware datetime
        :type execution_datetime: datetime.datetime
        :param callbacks: If specified, an iterable of callback functions
            to be applied to the task to schedule
        :type callbacks: Optional[Iterable[Callable[[asyncio.Task], Any]]]
        :rtype: None
        """

        now = datetime.datetime.now(datetime.timezone.utc)
        await asyncio.sleep((execution_datetime - now).total_seconds())
        task = self.event_loop.create_task(coroutine)

        if callbacks:
            for callback in callbacks:
                task.add_done_callback(callback)
                callbacks = [self.task_err_handler]

    async def time_alert(self, item_id: int, end_time: datetime.datetime) -> None:
        """
        Simply logs an alert to remind the user that an auction is ending

        :param item_id: A valid ShopGoodwill item ID
        :type item_id: int
        :param end_time: The end time of the auction
        :type end_time: datetime.datetime
        :rtype: None
        """

        self.update_favorites_cache(
            self.config["bid_sniper"].get("favorites_max_cache_seconds", 60)
        )

        # Check if we still want to alert on this item
        favorite = self.favorites_cache["favorites"].get(item_id, None)
        if favorite:
            delta_until_end = get_timedelta_to_time(end_time)
            delta_until_end = end_time.replace(microsecond=0) - datetime.datetime.now(
                datetime.timezone.utc
            ).replace(microsecond=0)
            self.logger.warning(
                f"Time alert - {favorite['title']} ending in {delta_until_end}"
            )

        return None

    async def place_bid(self, item_id: int) -> None:
        """
        Given an item ID, do the following:
            Check if it's still in our favorites
            If it is, see if we've set a max bid amount for it
            If our max bid amount is greater than the current price,
                place a bid for that amount

        :param item_id: A valid ShopGoodwill item ID
        :type item_id: int
        :rtype: None
        """

        # we must be _absolutely_ sure that the user still wishes to place a bid -
        # the config could've changed between scheduling and this function call

        # force an update of the favorites cache,
        # as we don't want to place erronous bids
        self.update_favorites_cache(5)

        # find the max_bid amount (if present) for this itemId
        favorite = self.favorites_cache["favorites"].get(item_id, None)
        if not favorite:
            return None

        notes = favorite.get("notes", None)
        if not notes:
            return None

        try:
            notes_js = json.loads(notes)
        except JSONDecodeError:
            # TODO should this be treated as an error? I don't think so.
            return None

        max_bid = notes_js.get("max_bid", None)
        if not max_bid:
            return None

        try:
            max_bid = float(max_bid)
        except ValueError:
            self.logger.error(f"ValueError casting max_bid value '{max_bid}' as float")
            return None

        # if we want to use the friend_list feature,
        # we must get the highest bidder before placing a bid
        if self.config.get("friend_list", list()):

            # attempt to get item info, but continue to place bid if we can't
            try:
                item_info = self.shopgoodwill_client.get_item_info(item_id)
                # Don't bid if the current highest bidder is on our friend list
                bid_summary = item_info["bidHistory"].get("bidSummary", list())
                if bid_summary:
                    bidder_name = bid_summary[0]["bidderName"]
                    if bidder_name in self.config.get("friend_list", list()):
                        self.logger.info(
                            "Canceling bid due to friendship for item '{favorite['title']}' - current high bidder {bidder_name}"
                        )
                        return None

            except BaseException as be:
                self.logger.error(
                    f"{type(be).__name__} getting info for item ID '{item_id} - {be} - continuing"
                )

        # Finally place a bid
        if not self.dry_run:
            # TODO in the future, address how SGW uses quantity
            # I don't think they use it in auctions
            #
            # Note that the account used is bidding_shopgoodwill_client
            try:
                self.bid_shopgoodwill_client.place_bid(
                    item_id, max_bid, favorite["sellerId"], quantity=1
                )
            except HTTPError as he:
                self.logger.error(
                    f"HTTPError placing bid on '{favorite['title']}' - {he}"
                )
                return None

        # only log the message after we've already placed the bid
        self.logger.warning(
            f"{self.dry_run_msg}Placing bid on '{favorite['title']}' for {max_bid}"
        )

        return None

    def start(self) -> None:
        """
        Simple method to start the bid sniper instance's event loop

        :rtype: None
        """

        self.event_loop.create_task(self.main_loop())
        self.event_loop.run_forever()

    async def main_loop(self) -> None:
        refresh_seconds = self.config["bid_sniper"].get("refresh_seconds", 300)
        favorites_cache_max_seconds = self.config["bid_sniper"].get(
            "favorites_max_cache_seconds", 60
        )

        # sort all tasks to schedule (time alerts and bid placing) from "soonest" to "futhest"
        # once sorted, use the "soonest" delta to determine when to schedule events
        min_scheduling_timedelta = sorted(
            self.alert_time_deltas + [self.bid_time_delta]
        )[::-1][0]

        while True:
            now = datetime.datetime.now(datetime.timezone.utc)

            # update favorites
            self.update_favorites_cache(favorites_cache_max_seconds)

            for item_id, favorite_info in self.favorites_cache["favorites"].items():
                # Don't double-schedule tasks
                if item_id in self.scheduled_tasks:
                    continue

                # SGW has been including microseconds inconsistently,
                # and one is liable to see '2025-04-29T23:00:17.45' in
                # the same response as '2025-05-01T22:09:00'
                end_time = favorite_info["endTime"]
                date_format = self.date_format
                if "." in end_time:
                    date_format += ".%f"

                # so close but yet so far away from ISO-8601
                # SGW simply trims the "PDT" (or PST?) off of the timestamps
                # TODO validate that the site only uses a single timezone!
                end_time = (
                    datetime.datetime.strptime(end_time, date_format)
                    .replace(tzinfo=ZoneInfo("US/Pacific"))
                    .astimezone(ZoneInfo("Etc/UTC"))
                )

                # only schedule tasks for the item if the "nearest" task is within refresh_seconds * 3 seconds
                # TODO flip this to "if less than, schedule thing"
                if (end_time - min_scheduling_timedelta) <= now + datetime.timedelta(
                    seconds=refresh_seconds * 3
                ):
                    # schedule reminders for whenever the user configured
                    for alert_time_delta in self.alert_time_deltas:
                        execution_datetime = end_time - alert_time_delta

                        # skip events in the past
                        if execution_datetime < now:
                            continue

                        self.event_loop.create_task(
                            self.schedule_task(
                                self.time_alert(item_id, end_time),
                                execution_datetime,
                                [self.task_err_handler],
                            )
                        ).add_done_callback(self.task_err_handler)

                    # schedule a tentative max_bid for this item
                    self.event_loop.create_task(
                        self.schedule_task(
                            self.place_bid(item_id),
                            end_time - self.bid_time_delta,
                            [self.task_err_handler],
                        )
                    ).add_done_callback(self.task_err_handler)

                    # mark this item ID as "scheduled"
                    self.logger.debug(
                        f"Scheduled events for item {favorite_info['title']}"
                    )
                    self.scheduled_tasks.add(item_id)

                # if configured, set notes for entries that do not have notes
                if self.default_note and not favorite_info.get("notes", ""):
                    self.shopgoodwill_client.add_favorite(
                        item_id, note=self.default_note
                    )

            await asyncio.sleep(refresh_seconds)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config file - defaults to ./config.json",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="If set, do not perform any actions "
        "that modify state on ShopGoodwill (eg. placing bids)",
    )
    args = parser.parse_args()

    return args


def main():
    args = parse_args()
    with open(args.config, "r") as f:
        config = json.load(f)

    bid_sniper = BidSniper(config, args.dry_run)
    bid_sniper.start()


if __name__ == "__main__":
    # TODO this doesn't seem to call main()...
    # with daemon.DaemonContext():
    #    main()
    main()

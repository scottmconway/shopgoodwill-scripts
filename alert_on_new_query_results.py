#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import re
from typing import Dict, List
from zoneinfo import ZoneInfo

import shopgoodwill

RELEVANT_LISTING_KEYS = [
    "buyNowPrice",
    "discountedBuyNowPrice",
    "endTime",
    "minimumBid",
    "remainingTime",
    "title",
]

USELESS_ATTRS = [
    "price",
    "sort",
    "categoryName",
    "sellerName",
    "layout",
    "searchOption",
]

SAVED_SEARCH_TO_QUERY_PARAMS = {
    "categoryLevelNum": "categoryLevelNo",
    "isWedding": "isWeddingCategory",
    "categoryLevelNum": "categoryLevel",
    "selectedCategoryIds": "catIds",
}


def saved_search_to_query(saved_search: Dict) -> Dict:
    """
    Contorts a saved search Dict to a valid query Dict
    """

    for attr in USELESS_ATTRS:
        del saved_search[attr]

    for old_name, new_name in SAVED_SEARCH_TO_QUERY_PARAMS.items():
        saved_search[new_name] = saved_search[old_name]
        del saved_search[old_name]

    # TODO how the hell does "categoryId work?"
    cat_ids = saved_search["catIds"].split(",")
    max_cat_id = max([int(i) for i in cat_ids])
    saved_search["selectedCategoryIds"] = max_cat_id

    for k, v in saved_search.items():
        saved_search[k] = str(v).lower()  # Thanks SGW

    # TODO we might need to worry about the query's `categoryId` field
    # it appears to be the middle ID in this instance
    #
    # catIds = "12,112,392"
    # categoryId = 112

    # This seems to work fine without it, though

    return saved_search


def filter_listings(query_json: Dict, listings: List[Dict]) -> List[Dict]:
    """
    Given a list of query results, filter the query results
    according to attributes in the query JSON.

    At this time, that means to enforce that quotes in the query text
    appear in the resulting listings' titles

    :param query_json: A query json for use with sgw.get_query_listings
    :type query_json: Dict
    :param listings: a list of listings, as returned by sgw.get_query_listings
    :type listings: List[Dict]
    :return: listings, filtered by rules defined in the query JSON
    :param listings: A list of listings, as returned by sgw.get_query_listings
    :type listings: List[Dict]
        filtered by the aforementioned rules
    :rtype: List[Dict]
    """

    final_listings = list()

    # enforce quotes in query strings
    # case-insensitive at the time being

    # TODO note that quotes can start or end with ' or " (or a mix therein!)
    # There's probably a better way to do this, but I am not privy to it
    quote_regex = re.compile(r"[\'\"].+?[\'\"]")
    search_string = query_json["searchText"].lower()
    quotes = quote_regex.findall(search_string)

    for listing in listings:
        failure = False

        for quote in quotes:
            if quote[1:-1] not in listing["title"].lower():
                failure = True
                break

        if not failure:
            final_listings.append(listing)

    return final_listings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-q", "--query-name", type=str, help="The name of the query to execute"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="If set, execute all queries for the configured data source",
    )
    parser.add_argument(
        "-l",
        "--list-queries",
        action="store_true",
        help="If set, list all queries that can be executed "
        "for the current data source and exit",
    )
    parser.add_argument(
        "-d",
        "--data-source",
        choices=["local", "saved_searches"],
        default="local",
        help="Data source for this query. "
        "If `saved_searches` is selected, "
        "Shopgoodwill credentials are required in the configuration file",
    )
    args = parser.parse_args()

    with open("config.json", "r") as f:
        config = json.load(f)

    # logging setup
    logger = logging.getLogger("shopgoodwill_alert_on_new_query_results")
    logging_conf = config.get("logging", dict())
    logger.setLevel(logging_conf.get("log_level", logging.INFO))
    if "gotify" in logging_conf:
        from gotify_handler import GotifyHandler

        logger.addHandler(GotifyHandler(**logging_conf["gotify"]))

    # data source setup
    if args.data_source == "saved_searches":
        auth_info = config.get("auth_info", None)
        if auth_info is None:
            raise Exception(
                "SGW authenication required for `saved_searches` data source"
            )

        sgw = shopgoodwill.Shopgoodwill(auth_info)

        # SGW doesn't let you name your queries,
        # so I guess we'll rely on their IDs
        saved_searches = sgw.get_saved_searches()

        if not saved_searches:
            saved_queries = dict()

        else:
            saved_queries = {
                str(i["savedSearchId"]): saved_search_to_query(i)
                for i in saved_searches
            }

        list_query_string = "Saved queries: %s" % (
            ", ".join(sorted(saved_queries.keys()))
        )

    else:
        sgw = shopgoodwill.Shopgoodwill()
        saved_queries = config["saved_queries"]
        list_query_string = "Saved queries: %s" % (", ".join(saved_queries.keys()))

    if args.list_queries:
        print(list_query_string)
        return

    # init seen listings
    seen_listings_filename = config.get("seen_listings_filename", "seen_listings.json")
    if os.path.isfile(seen_listings_filename):
        with open(seen_listings_filename, "r") as f:
            seen_listings = json.load(f)

        # if the user has an old seen_listings file,
        # delete all entries (and let them know about it)
        if not isinstance(seen_listings, dict):
            logger.warning(
                "Deprecated seen_listings file format detected - "
                "clearing existing seen_listings"
            )
            seen_listings = dict()
    else:
        seen_listings = dict()

    if not args.all and args.query_name not in saved_queries:
        logger.error(f'Invalid query_name "{args.query_name}" - exiting')
        exit(1)

    if args.all:
        queries_to_run = saved_queries
    else:
        queries_to_run = {args.query_name: saved_queries[args.query_name]}

    for query_name, query_json in queries_to_run.items():
        query_res = sgw.get_query_results(query_json)
        total_listings = filter_listings(query_json, query_res)

        alert_queue = list()

        for listing in total_listings:
            item_id = str(listing["itemId"])

            # skip seen listings
            if item_id in seen_listings:
                continue

            relevant_attrs = dict()
            for key in RELEVANT_LISTING_KEYS:
                relevant_attrs[key] = str(listing[key])
                relevant_attrs["url"] = f"https://shopgoodwill.com/item/{item_id}"

            seen_listings[item_id] = sgw.convert_timestamp_to_datetime(
                listing["endTime"]
            ).isoformat()
            alert_queue.append(relevant_attrs)

        if alert_queue:
            formatted_msg_lines = [
                f'{len(alert_queue)} new results for shopgoodwill query "{query_name}"',
                "",
            ]
            for alert in alert_queue:
                alert_lines = [
                    alert["title"] + ":",
                    alert["minimumBid"],
                    alert["endTime"],
                    alert["url"],
                    "",
                ]
                formatted_msg_lines.extend(alert_lines)

            logger.info("\n".join(formatted_msg_lines))

    # save new results of seen listings

    # but before we do, trim the stale entries
    now = datetime.datetime.now().astimezone(ZoneInfo("Etc/UTC"))
    keys_to_drop = list()

    for item_id, end_time in seen_listings.items():
        if now > datetime.datetime.fromisoformat(end_time):
            keys_to_drop.append(item_id)

    for item_id in keys_to_drop:
        del seen_listings[item_id]

    with open(seen_listings_filename, "w") as f:
        json.dump(seen_listings, f)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import argparse
import json
import logging
import os
from time import sleep
from typing import Dict, List

import requests

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-q", "--query-name", type=str, help="The name of the query to execute"
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
        # TODO connect to shopgoodwill
        auth_info = config.get("auth_info", None)
        if auth_info is None:
            raise Exception(
                "SGW authenication required for `saved_searches` data source"
            )

        sgw = shopgoodwill.Shopgoodwill(auth_info)

        # SGW doesn't let you name your queries,
        # so I guess we'll rely on their IDs
        saved_searches = sgw.get_saved_searches()

        # TODO this is in some way faulty
        # we'll need to contruct our own queries from the info shown here
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
        saved_queries = sorted(config["saved_queries"])
        list_query_string = "Saved queries: %s" % (", ".join(saved_queries))

    if args.list_queries:
        print(list_query_string)
        return

    # init seen listings
    seen_listings_filename = config.get("seen_listings_filename", "seen_listings.json")
    if os.path.isfile(seen_listings_filename):
        with open(seen_listings_filename, "r") as f:
            seen_listings = json.load(f)
    else:
        seen_listings = list()

    if not args.query_name or args.query_name not in saved_queries:
        logger.error(f'Invalid query_name "{args.query_name}" - exiting')
        exit(1)

    total_listings = sgw.get_query_results(saved_queries[args.query_name])

    alert_queue = list()

    for listing in total_listings:
        # skip seen listings
        item_id = listing["itemId"]
        if item_id in seen_listings:
            continue

        relevant_attrs = dict()
        for key in RELEVANT_LISTING_KEYS:
            relevant_attrs[key] = str(listing[key])
            relevant_attrs["url"] = f"https://shopgoodwill.com/item/{item_id}"

        seen_listings.append(item_id)
        alert_queue.append(relevant_attrs)

    if alert_queue:
        formatted_msg_lines = [
            f"{len(alert_queue)} new results for shopgoodwill query",
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
    with open(seen_listings_filename, "w") as f:
        json.dump(seen_listings, f)


if __name__ == "__main__":
    main()

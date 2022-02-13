#!/usr/bin/env python3

import argparse
import json
import logging
import os
from time import sleep
from typing import Dict, List

import requests

QUERY_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
RELEVANT_LISTING_KEYS = [
    "buyNowPrice",
    "discountedBuyNowPrice",
    "endTime",
    "minimumBid",
    "remainingTime",
    "title",
]


def get_all_query_results(query_json: Dict) -> List[Dict]:
    """
    :param query_json: A valid Shopgoodwill query JSON
    :type query_json: Dict
    :return: A list of query results across all valid result pages
    :rtype: List[Dict]
    """

    query_json["page"] = 1
    query_json["pageSize"] = 40
    total_listings = list()
    while True:
        query_res = requests.post(QUERY_URL, json=query_json)
        query_res.raise_for_status()
        page_listings = query_res.json()["searchResults"]["items"]

        # break if this page is empty
        if not page_listings:
            return total_listings

        else:
            query_json["page"] += 1
            total_listings += page_listings

            # break if we've seen all that we expect to see
            if len(total_listings) == query_res.json()["searchResults"]["itemCount"]:
                return total_listings

        sleep(5)  # naive rate-limiting


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-q",
        "--query_name",
        type=str,
        help="The name of the query to execute. "
        "This must be present under the config's `saved_queries` section",
    )
    parser.add_argument(
        "-l",
        "--list-queries",
        action="store_true",
        help="If set, list all queries that can be executed, and exit",
    )
    args = parser.parse_args()

    with open("config.json", "r") as f:
        config = json.load(f)

    if args.list_queries:
        print("Saved queries: %s" % (", ".join(sorted(config["saved_queries"]))))
        return

    logger = logging.getLogger("shopgoodwill_alert_on_new_query_results")
    logging_conf = config.get("logging", dict())
    logger.setLevel(logging_conf.get("log_level", logging.INFO))
    if "gotify" in logging_conf:
        from gotify_handler import GotifyHandler

        logger.addHandler(GotifyHandler(**logging_conf["gotify"]))

    # init seen listings
    seen_listings_filename = config.get("seen_listings_filename", "seen_listings.json")
    if os.path.isfile(seen_listings_filename):
        with open(seen_listings_filename, "r") as f:
            seen_listings = json.load(f)
    else:
        seen_listings = list()

    if not args.query_name or args.query_name not in config["saved_queries"]:
        logger.error(f'Invalid query_name "{args.query_name}" - exiting')
        exit(1)

    total_listings = get_all_query_results(config["saved_queries"][args.query_name])

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

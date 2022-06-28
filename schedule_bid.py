#!/usr/bin/env python3

import argparse
import json

import shopgoodwill


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "item_id",
        type=int,
        help="The item ID for which to schedule a bid",
    )
    parser.add_argument(
        "bid_amount",
        type=float,
        help="The max bid amount to submit",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to config file - defaults to ./config.json",
    )
    args = parser.parse_args()

    return args


def main():
    args = parse_args()
    with open(args.config, "r") as f:
        config = json.load(f)

    bid_note_json = {"max_bid": float(args.bid_amount)}

    # init the command account
    if config["auth_info"].get("auth_type", "universal") == "command_bid":
        shopgoodwill_client = shopgoodwill.Shopgoodwill(
            config["auth_info"]["command_account"]
        )
    else:
        shopgoodwill_client = shopgoodwill.Shopgoodwill(config["auth_info"])

    # if the item is already favorited, it'll still work
    shopgoodwill_client.add_favorite(int(args.item_id), note=json.dumps(bid_note_json))


if __name__ == "__main__":
    main()

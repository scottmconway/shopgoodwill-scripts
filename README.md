# Shopgoodwill Scripts
A collection of scripts for programmatically interacting with [Shopgoodwill](https://shopgoodwill.com).

## Requirements
* python3
* requests
* gotify-handler (optional - required only if you'd like to log to gotify)

## Scripts
### `alert_on_new_query_results.py`

This script executes an "advanced query" as specified by the user, and logs and results that haven't been seen before. The "itemID" is used to track listings. "Seen listings" are tracked globally across all queries, so you should only be alerted once about a given item. However, I've seen shopgoodwill sometimes re-upload auctions with no changes, except for the Item ID. Those listings will be considered "new".

#### Arguments
|Name|Type|Description|
|-|-|-|
|`-q`|`str`|The name of the query to execute. This must be present under the config's `saved_queries` section|`
|`-l`|`bool`|If set, list all queries that can be executed, and exit|

#### Query Generation
The easiest way to generate a query JSON is to make an [Advanced Search](https://shopgoodwill.com/search/advancedsearch) on Shopgoodwill. Simply craft the query you'd like, open the network console, and click the search button. The XHR POST request to `https://buyerapi.shopgoodwill.com/api/Search/ItemListing` contains the JSON that you're looking for.

Alternatively, if you can create one from scratch, if you'd like to guess at the query values. See `config.json.example`'s `saved_queries` section for the required fields. 

Once you have a query, you can insert it into the configuration file under `saved_queries` with a distinctive name.
*Note* - the `page` and `pageSize` attributes in a query will be ignored, and the query will paginate until all results have been accounted for. Additionally, `closedAuctionEndingDate` can be adjusted to an invalid date (eg. 1/1/1), which _should_ cover all of time. Since the search function only returns active listings, there isn't concern of getting stale results.

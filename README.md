# ShopGoodwill Scripts
A collection of scripts for programmatically interacting with [ShopGoodwill](https://shopgoodwill.com).

## Requirements
* python3
* see requirements.txt

## Configuration Setup
See `config.json.example` for an example configuration file.

### `auth_info`
This section is only needed if you want to use functionality requiring a ShopGoodwill account.

There are three different ways you can choose to log into ShopGoodwill:
* Access Token
* Plaintext username/password
* "Encrypted" username/password

Only one type of authentication method should be specified, but in case multiple are provided, the following order of precedence is used:
* Access Token
* "Encrypted" username/password
* Plaintext username/password

If an invalid access token is provided, the application will fallback to username/password authentication methods.

Note that plaintext or "encrypted" username/password are the recommended options, and the "encryption" seriously does not matter (more on this later).

`bid_sniper` (as discussed below) can utilize multiple accounts if desired, for read-only operations (reading favorites, sending time alerts on expiring auctions) and write operations (placing bids). If this setup is desired, `auth_info` should contain two dictionaries with the same format as `auth_info`, with the names `command_account`, and `bid_account`.

Additionally, the `auth_type` attribute must be set to `command_bid`.

eg.
```json
{
    "auth_info": {
        "auth_type": "command_bid",
        "command_account": {
            "username": "",
            "password": ""
        },
        "bid_account": {
            "username": "",
            "password": ""
        }
}
```

In the future, if additional scripts utilize login features, I'll have to standardize this.

#### Access Token
With a valid ShopGoodwill session, authenticated requests will contain an `Authorization` header. Simply provide the token (coming after the text `Bearer `) here.

#### Plaintext Username/Password
Simply put your plaintext username/password in the `username` and `password` fields. That's it.

#### "Encrypted" Username/Password
If you'd like a sprinkle of obfuscation in your config, you can store the username and password fields in the way that they're directly communicated to ShopGoodwill.

If you're interested in why I have quotes around "encryption", check out [my blog post on it](https://conway.scot/shopgoodwill-reversing/).

Anyway, to find the "encrypted" variants of these parameters, fire up your browser of choice, open the network monitor, and log in to the service. The `POST` request to `https://buyerapi.shopgoodwill.com/api/SignIn/Login` will contain the values that you're looking for. Those values should be stored in the `encrypted_username` and `encrypted_password` fields.

### `logging`
`log_level` - sets the log level to subscribe to
`gotify` - only required if you wish to use gotify as a logging destination

### `seen_listings_filename`
This is the path of the file that will have "seen" listings written to, so we can track "new" ones. This is used by `alert_on_new_query_results.py`, and should probably be moved elsewhere.

### `saved_queries`
This section contains `{query_friendly_name: query}` JSON objects, for use by `alert_on_new_query_results.py`. `query` should be a query JSON, as described below.

## Scripts
### `bid_sniper.py`

This script can run as a daemon to "snipe" bids on watched auctions, and issue time-based alerts at user-configured times until auction ending.

The configured state (watched auctions, max bid prices) is handled entirely in ShopGoodwill itself! With a valid ShopGoodwill account, you can "favorite" items by clicking on the heart icon in any query page. Once a favorite is set, it should appear on [this page](https://shopgoodwill.com/shopgoodwill/favorites), under the tab that matches the item's current auction state (open or closed). Once an item appears under the "open" tab, time-based alerts will be picked up the next time the daemon queries your favorites.

If you wish to snipe a bid, you'll have to store the max bid price in ShopGoodwill _somehow_. Rather conveniently, you can set a 500 character note for each listing in your favorites. Thus, if you'd like to set a max bid, save a JSON-formatted note for the listing, with the key `max_bid` mapping to an int or float value. Other keys can be included - the program just reads from `max_bid`. Notes that aren't JSONs or don't contain `max_bid` will be ignored when being evaluated for bidding.

eg.
```json
{
    "max_bid": 10.5
}
```

The favorites cache is forcibly updated right before any bids are placed, so what you see on ShopGoodwill's site should truly reflect the actions that this script will take.

If a listing has been removed from your favorites, it _will not_ be bid on. However, it's possible that you can get erroneous time-based alerts. If you'd like to change this, simply set `favorites_max_cache_seconds` to `0` in your config file.


#### Arguments
|Short Name|Long Name|Type|Description|
|-|-|-|-|
||`--config`|`str`|Path to config file - defaults to `./config.json`|
|`-n`|`--dry-run`|`bool`|If set, do not perform any actions that modify state on ShopGoodwill (eg. placing bids)|

### Configuration
The following values are under `bid_sniper` in the example config file.

|Name|Type|Description|
|-|-|-|
|`refresh_seconds`|`int`|The number of seconds apart to schedule execution of the program's main loop|
|`bid_snipe_time_delta`|`str`|A valid time delta string representing the time before an auction ending, when a bid will be placed|
|`favorites_max_cache_seconds`|`int`|If the favorites cache is older than this number of seconds, it will be refreshed. This value is ignored when placing a bid. At that time, it's forcibly refreshed|
|`friend_list`|`List[str]`|A list of users that you don't want to outbid. Usernames should be in the obfuscated format of first character, four asterisks, and the last character. eg. `a****b`. Usernames are case-sensitive.|
|`alert_time_deltas`|`List[str]`|A list of time delta strings for which to alert the user of an auction's ending time. eg. "1 hour" will cause a notification 1 hour before the end of every watched auction|

### `alert_on_new_query_results.py`

This script executes an "advanced query" as specified by the user, and logs and results that haven't been seen before. `itemID` is used to track listings. "Seen listings" are tracked globally across all queries, so you should only be alerted once about a given item. However, I've seen ShopGoodwill sometimes re-upload auctions with no changes, except for the `itemID`. Those listings will be considered "new".

Note - this query has _advanced_ capabilities over that of ShopGoodwill. At this time, it will further filter results as according to the `searchText`'s use of quotation marks.

eg. the `searchText` string `"foo bar"` will _not_ match with a SGW listing of the title "foo baz bar", whereas it _would_ match in the web application. Note that the search operation is the same, but results are filtered to further enforce the will of the user.

Further improvements to come!

#### Arguments
|Short Name|Long Name|Type|Description|
|-|-|-|-|
|`-q`|`--query-name`|`str`|The name of the query to execute. This must be present in the data source's list of queries|
|N/A|`--all`|`bool`|If set, execute all queries under the configured data source|
|`-l`|`--list-queries`|`bool`|If set, list all queries that can be executed by this data source and exit|
|`-d`|`--data-source`|`str`|Either `local` or `saved_searches`. The former reads query JSONs from the config file's `saved_queries` section. The latter reads from a ShopGoodwill account's "Saved Searches"|
|N/A|`--markdown`|`bool`|If set, log URLs in markdown format (for gotify)|
|N/A|`--config`|`str`|Path to config file - defaults to ./config.json|

#### Query Generation
The easiest way to generate a query JSON is to make an [Advanced Search](https://shopgoodwill.com/search/advancedsearch) on ShopGoodwill. Simply craft the query you'd like, open the network console, and click the search button. The XHR POST request to `https://buyerapi.shopgoodwill.com/api/Search/ItemListing` contains the JSON that you're looking for.

Alternatively, you can create one from scratch if you'd like to guess at the query values. See `config.json.example`'s `saved_queries` section for the required fields. Note that all fields have default values, so you can just specify the non-default attributes in this config section.

Once you have a query, you can insert it into the configuration file under `saved_queries` with a distinctive name.
*Note* - the `page` and `pageSize` attributes in a query will be ignored, and the query will paginate until all results have been accounted for. Additionally, `closedAuctionEndingDate` can be adjusted to an invalid date (eg. 1/1/1), which _should_ cover all of time. Since the search function only returns active listings, there isn't concern of getting stale results.

#### Final Notes
It's worth noting that the logic to derive a query JSON from a ShopGoodwill saved search may not be 100% accurate. Thus, I'd recommend using query JSONs in the config file if possible. If you're interested in knowing why I take this view, check out how saved searches actually generate queries in the web UI. It's not straight-forward. Not to take this time to rant, but the API is _dirty_.

### `schedule_bid.py`

This is a simple script to automate favoriting and making a note to have `bid_sniper` bid on a given item before it ends.

#### Arguments
|Short Name|Long Name|Type|Description|
|-|-|-|-|
|N/A|`item_id`|`int`|The item ID for which to schedule a bid|
|N/A|`bid_amount`|`float`|The max bid amount to submit|
|N/A|`--config`|`str`|Path to config file - defaults to ./config.json|

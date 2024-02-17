import logging
import base64
import datetime
import os
import re
import urllib.parse
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad
from requests.exceptions import HTTPError
from requests.models import PreparedRequest, Response

# TODO add pagination

_SHIPPING_COST_PATTERN = re.compile(
    r"Shipping: <span id='shipping-span'>\$(\d+\.\d+) \(.*\)<\/span>"
)

LOG = logging.getLogger(__name__)


class Shopgoodwill:
    LOGIN_PAGE_URL = 'https://shopgoodwill.com/signin'
    API_ROOT = 'https://buyerapi.shopgoodwill.com/api'
    ENCRYPTION_INFO = {
        'key': b'6696D2E6F042FEC4D6E3F32AD541143B',
        'iv': b'0000000000000000',  # You love to see it
        'block_size': 16,
    }
    FAVORITES_MAX_NOTE_LENGTH = 256
    INVALID_AUTH_MESSAGE = 'The username or password are incorrect'

    def shopgoodwill_err_hook(self, res: Response, *args, **kwargs) -> None:
        res.raise_for_status()
        # res_js = res.json()

        # TODO sometimes the status field appears, other times it does not
        # eg. it's absent in the query response page
        # if not res_js['status']:
        #    raise Exception("Error in ShopGoodwill API response")

        # TODO investigate possible values of "message" field
        # so far I've seen "Success" and "Ok"
        #
        # sometimes this field is absent, too.

        # TODO sometimes we'll get 403s,
        # seemingly indicating that our session prematurely ended
        #
        # Next steps - re-login if we get a 403,
        # attempt X (5) times, then raise the _real_ 40X
        #
        # TODO but how can we _really_ tell if a 403 is a session outage?
        # Maybe try getting another predefined page that requires login
        # eg. profile info

    def __init__(self, auth_info: Optional[Dict] = {}):
        self.session = requests.Session()

        # SGW doesn't take kindly to the default requests user-agent
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:12.0) Gecko/20100101 Firefox/12.0'
        }
        self.session.hooks['response'] = self.shopgoodwill_err_hook

        self.authenticated = False

        # NOTE: OS environment variables for authentication always takes
        # precedence over the config file; useful especially when tools
        # are run in a Docker environment
        for auth_method in [
            self._attempt_token_authentication,
            self._attempt_encrypted_username_login,
            self._attempt_plaintext_login,
        ]:
            self.authenticated = auth_method(auth_info)
            LOG.debug(f'{auth_method} -> {self.authenticated}')
            if self.authenticated:
                break

        if not self.authenticated:
            raise Exception('Invalid authentication credentials or auth_info provided!')

    def _attempt_token_authentication(self, auth_info: Dict) -> bool:
        if access_token := os.getenv(
            'SHOPGOODWILL_ACCESS_TOKEN', auth_info.get('access_token')
        ):
            LOG.warning('Attempting auth with access_token')
            if self.access_token_is_valid(access_token):
                self.session.headers['Authorization'] = f'Bearer {access_token}'
                return True
        return False

    def _attempt_encrypted_username_login(self, auth_info: Dict) -> bool:
        # NOTE: should the wacky encrypted variation even be encouraged?
        if encrypted_username := auth_info.get('encrypted_username'):
            if encrypted_password := auth_info.get('encrypted_password'):
                self.login(encrypted_username, encrypted_password)
                return True
        return False

    def _attempt_plaintext_login(self, auth_info: Dict) -> bool:
        if username := os.getenv('SHOPGOODWILL_USERNAME', auth_info.get('username')):
            if password := os.getenv(
                'SHOPGOODWILL_PASSWORD', auth_info.get('password')
            ):
                LOG.info(f'Attempting auth with username={username}')
                auth_info['encrypted_username'] = self._encrypt_login_value(username)
                auth_info['encrypted_password'] = self._encrypt_login_value(password)
                return self._attempt_encrypted_username_login(auth_info)
        return False

    def convert_timestamp_to_datetime(self, sgw_timestamp: str) -> datetime.datetime:
        """
        Given a timestamp string from SGW,
        return a datetime.datetime object,
        accounting for the implied timezone (PST/PDT)

        :param sgw_timestamp: A string timestamp from SGW
        :type sgw_timestamp: str
        :return: A datetime.datetime object representing the timestamp
        :rtype: datetime.datetime
        """

        # if there are any milliseconds in this timestamp, truncate
        if '.' in sgw_timestamp:
            sgw_timestamp = sgw_timestamp[: sgw_timestamp.find('.')]

        return (
            datetime.datetime.fromisoformat(sgw_timestamp)
            .replace(tzinfo=ZoneInfo('US/Pacific'))
            .astimezone(ZoneInfo('Etc/UTC'))
        )

    def _encrypt_login_value(self, plaintext: str) -> str:
        """
        Replicates SGW's "encryption" on username/password fields.
        It really isn't neccessary since you can
        rip the encrypted values from your browser,
        but it'll make initial config just a tad easier

        :param plaintext: The string value to be "encrypted"
        :type plaintext: str
        :return: An "encrypted" string that can be used for authentication
        :rtype: str
        """

        padded = pad(plaintext.encode(), Shopgoodwill.ENCRYPTION_INFO['block_size'])
        cipher = AES.new(
            Shopgoodwill.ENCRYPTION_INFO['key'],
            AES.MODE_CBC,
            Shopgoodwill.ENCRYPTION_INFO['iv'],
        )
        ciphertext = cipher.encrypt(padded)
        return urllib.parse.quote(base64.b64encode(ciphertext))

    def access_token_is_valid(self, access_token: str) -> bool:
        """
        Simple function to test an access token
        by looking at the user's saved searches
        """
        token_is_valid = False
        try:
            # temporarily set access token and "logged_in" status to test it
            self.authenticated = True

            self.session.headers['Authorization'] = f'Bearer {access_token}'
            self.session.post(Shopgoodwill.API_ROOT + '/SaveSearches/GetSaveSearches')
        except HTTPError as he:
            if he.response.status_code != 401:
                raise he
        except Exception as e:
            raise e
        else:
            token_is_valid = True
        finally:
            del self.session.headers['Authorization']
            self.authenticated = False

        return token_is_valid

    def requires_auth(func):
        """
        Simple decorator that raises an exception if an endpoint requiring login
        is called without valid authentication.
        """

        def inner(self, *args, **kwargs):
            if not self.authenticated:
                raise Exception('This function requires authentication to Shopgoodwill')
            return func(self, *args, **kwargs)

        return inner

    def login(self, username: str, password: str):
        # I don't know how they set clientIpAddress or appVersion,
        # I just nabbed these from my browsers' requests
        login_params = {
            'browser': 'firefox',
            'remember': False,
            'clientIpAddress': '0.0.0.4',
            'appVersion': '00099a1be3bb023ff17d',
            'username': username,
            'password': password,
        }

        # Temporarily drop the requests hook
        # so we can add the set-cookies from this HTML page
        self.session.hooks['response'] = None

        # TODO we should still check for exceptions here
        self.session.get(Shopgoodwill.LOGIN_PAGE_URL)

        self.session.hooks['response'] = self.shopgoodwill_err_hook

        res_json = self.session.post(
            Shopgoodwill.API_ROOT + '/SignIn/Login', json=login_params
        ).json()

        if res_json['message'] == Shopgoodwill.INVALID_AUTH_MESSAGE:
            raise Exception('Invalid credentials')

        self.session.headers['Authorization'] = f"Bearer {res_json['accessToken']}"
        # TODO deal with refresh token

        return True

    @requires_auth
    def get_saved_searches(self):
        res = self.session.post(Shopgoodwill.API_ROOT + '/SaveSearches/GetSaveSearches')
        return res.json()['data']

    @requires_auth
    def get_favorites(self, favorite_type: str = 'open') -> Dict[int, Dict]:
        """
        Returns the logged in user's favorites, and all of their (visible)
        attributes.

        Note that this parses the list of dicts into a properly parsed dict,
        keyed on itemId, for my sanity.

        :param favorite_type: One of "open", "close", or "all"
            only listings that fit the type are returned
        :type favorite_type: str
        :return: A dict of item_id: item_info_dict items
        :rtype:
        """

        # nb - this is _not_ paginated
        # it seems that it just returns _all_ favorites
        # (which is great for us)
        #
        # TODO should this default to all?
        # we just don't care about closed listings

        res = self.session.post(
            Shopgoodwill.API_ROOT + '/Favorite/GetAllFavoriteItemsByType',
            params={'Type': favorite_type},
            json={},
        )
        parsed_favorites = dict()

        # It'd be nice if their formatting was consistent
        if favorites := res.json()['data']:
            for favorite in favorites:
                parsed_favorites[int(favorite['itemId'])] = favorite

        return parsed_favorites

    @requires_auth
    def add_favorite(self, item_id: int, note: Optional[str] = None) -> None:
        """
        Given an Item ID, attampt to add it to the logged in user's favorites,
        optionally with a note.

        :param item_id: A valid item ID
        :type item_id: int
        :param note: If specified,
            text to add to the favorite after its creation
        :type note: Optional[str]
        :rtype: None
        """

        self.session.get(
            f'{Shopgoodwill.API_ROOT}/Favorite/AddToFavorite',
            params={'itemId': item_id},
        )
        if note:
            self.add_favorite_note(item_id, note)

    @requires_auth
    def add_favorite_note(self, item_id: int, note: str) -> None:
        """
        Given an Item ID of an item in the logged in user's favorites,
        add the requested note to it.

        :param item_id: A valid item ID
        :type item_id: int
        :param note: If specified,
            text to add to the favorite after its creation
        :type note: Optional[str]
        :rtype: None
        """

        if len(note) > Shopgoodwill.FAVORITES_MAX_NOTE_LENGTH:
            LOG.warning(
                f"Truncating note '{note}' to {Shopgoodwill.FAVORITES_MAX_NOTE_LENGTH} chars"
            )
            note = note[:256]

        favorites = self.get_favorites()
        if item_id not in favorites:
            raise Exception(f"Item {item_id} not in user's favorites!")

        watchlist_id = favorites[item_id]['watchlistId']

        # note that the webapp passes a "date" value, but it is not necessary
        self.session.post(
            f'{Shopgoodwill.API_ROOT}/Favorite/Save',
            json={'notes': note, 'watchlistId': watchlist_id},
        )

    @requires_auth
    def place_bid(
        self, item_id: int, bid_amount: float, seller_id: int, quantity: int = 1
    ):
        bid_json = {
            'itemId': item_id,
            'bidAmount': '%.2f' % bid_amount,
            'sellerId': seller_id,
            'quantity': quantity,
        }
        response = self.session.post(
            f'{Shopgoodwill.API_ROOT}/ItemBid/PlaceBid', json=bid_json
        ).json()

        """
        Possible bid responses:

        Immediately outbid:
            <h3><b>You have already been outbid. </b></h3><p>This occurred because someone specfied a higher maximum bid than you. </p><p>Did you know?  You can choose to not receive bid notifications by email. Simply visit your <a href='https://shopgoodwill.com//shopgoodwill/personal-information'> Buyer/Contact Information page.</a></p>

        High Bidder (w/ templated date/time):
            <h3><b>Bid Received! </b></h3><p>You are <strong>currently</strong> the high bidder for this auction. </p><p>This item ends at %-m/%-d/%Y -%-H:%M:%S %p PT, check back then for results. </p><p>Did you know? You can choose to not receive bid notifications by email. Simply visit your <a href='https://shopgoodwill.com//shopgoodwill/personal-information'> Buyer/Contact Information page.</a></p>

        Auction Closed (w/ templated date/time):
            result: -3
            message: "Auction has closed. Auction ended as of 12/20/2022 7:01:00 PM PT"
            currentPrice: null
            status: false
        """

        # TODO should we return the outcome?
        return

    def get_item_info(self, item_id: int) -> Dict:
        """
        Simple function to get all info for a given item.
        Returns the contents shown on /item/$ITEM_ID pages on the SGW site.

        :param item_id: A valid item ID
        :type item_id: int
        :return: A dict containing all item attributes from SGW
        :rtype: Dict
        """

        return self.session.get(
            f'{Shopgoodwill.API_ROOT}/itemDetail/GetItemDetailModelByItemId/{item_id}'
        ).json()

    def get_item_bid_info(self, item_id: int) -> Dict:
        """
        Simple function to get all info
        provided for an item by the "quick bid" action.

        Note that this function is significantly quicker than get_item_info,
        but it doesn't contain as much information.

        (137ms to 257ms with a sample size of 1 comparison)

        It does contain the seller ID, which is needed for placing a bid.

        :param item_id: A valid item ID
        :type item_id: int
        :return: A dict containing some item attributes from SGW
        :rtype: Dict
        """

        return self.session.get(
            f'{Shopgoodwill.API_ROOT}/itemBid/ShowBidModal', params={'itemId': item_id}
        ).json()

    def get_query_results(
        self, query_json: Dict, page_size: Optional[int] = 40
    ) -> List[Dict]:
        """
        Given a valid query JSON, return the results of the query

        :param query_json: A valid Shopgoodwill query JSON
        :type query_json: Dict
        :param page_size: Page size
        :type page_size: Optional[int]
        :return: A list of query results across all valid result pages
        :rtype: List[Dict]
        """

        query_json['page'] = 1
        query_json['pageSize'] = page_size
        total_listings = list()

        while True:
            response = self.session.post(
                Shopgoodwill.API_ROOT + '/Search/ItemListing', json=query_json
            )

            # err check
            # see https://github.com/scottmconway/shopgoodwill-scripts/issues/12
            if response.json().get('categoryListModel', None) is None:
                raise Exception('Error response from query endpoint')

            search_results = response.json()['searchResults']
            if page_listings := search_results['items']:
                query_json['page'] += 1
                total_listings += page_listings
            else:
                # break if this page is empty
                return total_listings

            # break if we've seen all that we expect to see
            if len(total_listings) == search_results['itemCount']:
                return total_listings

    def get_item_shipping_estimate(
        self, item_id: int, zip_code: str
    ) -> Optional[float]:
        """
        Given an item id and a zip code, returns the extracted estimated
        shipping cost result.

        :param item_id: A valid Shopgoodwill item id
        :type item_id: int
        :param zip_code: A valid US zip or zip+4 code
        :type zip_code: str
        :return: A float representation of the estimated shipping cost
        extracted from the xml api response
        :rtype: float
        """

        resp = self.session.post(
            f'{Shopgoodwill.API_ROOT}/itemDetail/CalculateShipping',
            json={
                'itemId': item_id,
                'zipCode': zip_code,
                'country': 'US',
                'province': None,
                'quantity': 1,
                'clientIP': '0.0.0.0',
            },
        )

        shipping_est_price = _SHIPPING_COST_PATTERN.findall(resp.text)
        if len(shipping_est_price) > 0:
            shipping_est_price = float(shipping_est_price[0])
        else:
            shipping_est_price = None
        return shipping_est_price

    # TODO maybe if there's any internal consistency
    def paginate_request(self, prepared_request: PreparedRequest) -> List[Dict]:
        """
        Given a prepared request, paginate by modifying the body's "page"
        parameter until we hit the last page
        """

        pass

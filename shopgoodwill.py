from typing import Dict, List, Optional

import requests
from requests.exceptions import HTTPError
from requests.models import PreparedRequest, Response

# TODO add pagination


def shop_goodwill_err_hook(res: Response, *args, **kwargs):
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

    return res


class Shopgoodwill:
    LOGIN_PAGE_URL = "https://shopgoodwill.com/signin"
    API_ROOT = "https://buyerapi.shopgoodwill.com/api"

    def __init__(self, auth_info: Optional[Dict] = None):
        self.shopgoodwill_session = requests.Session()

        # SGW doesn't take kindly to the default requests user-agent
        self.shopgoodwill_session.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:12.0) Gecko/20100101 Firefox/12.0"
        }
        self.shopgoodwill_session.hooks["response"] = shop_goodwill_err_hook
        self.logged_in = False

        if auth_info:
            # check if auth token exists, and if it works
            access_token = auth_info.get("access_token", None)
            if self.access_token_is_valid(access_token):
                self.shopgoodwill_session.headers[
                    "Authorization"
                ] = f"Bearer {access_token}"

            else:
                self.login(auth_info["username"], auth_info["password"])

            self.logged_in = True

    def access_token_is_valid(self, access_token: str) -> bool:
        """
        Simple function to test an access token
        by looking at the user's saved searches
        """

        # if access_token is None:
        #    return False

        # temporarily set access token and "logged_in" status to test it
        self.logged_in = True
        self.shopgoodwill_session.headers["Authorization"] = f"Bearer {access_token}"

        try:
            res = self.shopgoodwill_session.post(
                Shopgoodwill.API_ROOT + "/SaveSearches/GetSaveSearches"
            )

        except HTTPError as he:
            if he.response.status_code == 401:
                self.logged_in = False
                del self.shopgoodwill_session.headers["Authorization"]

                return False

            else:
                self.logged_in = False
                del self.shopgoodwill_session.headers["Authorization"]
                raise he

        self.logged_in = False
        del self.shopgoodwill_session.headers["Authorization"]
        return True

    def requires_auth(func):
        """
        Simple decorator to raise an exception if an endpoint requiring login
        is called without valid auth
        """

        def inner(self, *args, **kwargs):
            if not self.logged_in:
                raise Exception("This function requires login to Shopgoodwill")

            return func(self, *args, **kwargs)

        return inner

    def login(self, username: str, password: str):

        # I don't know how they set clientIpAddress or appVersion,
        # I just nabbed these from my browsers' requests
        login_params = {
            "browser": "firefox",
            "remember": False,
            "clientIpAddress": "0.0.0.4",
            "appVersion": "00099a1be3bb023ff17d",
            "username": username,  # TODO deal with encryption
            "password": password,  # TODO deal with encryption
        }

        # Temporarily drop the requests hook,
        # so we can add the set-cookies from this HTML page
        self.shopgoodwill_session.hooks["response"] = None
        self.shopgoodwill_session.get(Shopgoodwill.LOGIN_PAGE_URL)
        self.shopgoodwill_session.hooks["response"] = shop_goodwill_err_hook

        res = self.shopgoodwill_session.post(
            Shopgoodwill.API_ROOT + "/SignIn/Login", json=login_params
        )
        self.shopgoodwill_session.headers[
            "Authorization"
        ] = f"Bearer {res.json()['accessToken']}"
        # TODO deal with refresh token

        return True

    @requires_auth
    def get_saved_searches(self):
        res = self.shopgoodwill_session.post(
            Shopgoodwill.API_ROOT + "/SaveSearches/GetSaveSearches"
        )
        return res.json()["data"]

    @requires_auth
    def get_favorites(self, favorite_type: str = "open") -> List[Dict]:
        # TODO nb - this is _not_ paginated
        # it seems that it just returns _all_ favorites
        # (which is great for us)
        #
        # TODO should this default to all?
        # we just don't care about closed listings

        # for docs - favorite_type: [open, close, all]

        res = self.shopgoodwill_session.post(
            Shopgoodwill.API_ROOT + "/Favorite/GetAllFavoriteItemsByType",
            params={"Type": favorite_type},
            json={},
        )
        return res.json()["data"]

    def get_query_results(
        self, query_json: Dict, page_size: Optional[int] = 40
    ) -> List[Dict]:
        """
        Given a valid query JSON, return the results of the query

        :param query_json: A valid Shopgoodwill query JSON
        :type query_json: Dict
        :return: A list of query results across all valid result pages
        :rtype: List[Dict]
        """

        query_json["page"] = 1
        query_json["pageSize"] = page_size
        total_listings = list()

        while True:
            query_res = self.shopgoodwill_session.post(
                Shopgoodwill.API_ROOT + "/Search/ItemListing", json=query_json
            )
            page_listings = query_res.json()["searchResults"]["items"]

            # break if this page is empty
            if not page_listings:
                return total_listings

            else:
                query_json["page"] += 1
                total_listings += page_listings

                # break if we've seen all that we expect to see
                if (
                    len(total_listings)
                    == query_res.json()["searchResults"]["itemCount"]
                ):
                    return total_listings

    # TODO maybe if there's any internal consistency
    def paginate_request(self, prepared_request: PreparedRequest) -> List[Dict]:
        """
        Given a prepared request, paginate by modifying the body's "page"
        parameter until we hit the last page
        """

        pass

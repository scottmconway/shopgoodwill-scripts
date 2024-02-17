# Reverse Engineering ShopGoodwill for Fun and Profit

Scott Conway Information Security Researcher, Apr 3, 2022
Source: https://conway.scot/shopgoodwill-reversing/

### Posting

I’ve recently re-discovered the sheer amount of great stuff that can be found both in physical thrift stores and on [ShopGoodwill](https://shopgoodwill.com/). If you’re unfamiliar, it’s Goodwill’s online auction platform. Like most Internet services that I interact with, I’d love to use it in an automated fashion, but they don’t make their API documentation public. That’s no matter at all, but reversing some webapp wouldn’t be a good enough topic for me to blog about. Instead, I’m going to talk about the _weird_ stuff that goes on interfacing with ShopGoodwill’s site.

That said - the scripts that I’ve written for interacting with ShopGoodwill can be found [here](https://github.com/scottmconway/shopgoodwill-scripts), if you’re interested in getting cron-scheduled query digest updates or bid sniping.

When I started writing “bid\_sniper”, I of course recognized that I needed to log in programmatically. So, I fired up my browser’s network console and sent my credentials to their login page. And… that’s weird. It’s a simple REST login endpoint, but it looks like the username and password are encoded. Of course, the encoded credentials shown here aren’t valid.

```json
POST https://buyerapi.shopgoodwill.com/api/SignIn/Login

{
  "userName": "%2BTYKS7w6YCrMchyAyT6vYXNwPJuDVIfyUsoKLNLWkfiPl%2BQjBFuXg7jY8VCFiREf",
  "password": "cdmW%2B4ZNN4VMUAHGO4JJofvxZ9CYnBuylzBMVoc0pU0SWMxposFd%2BZam2Lnu2Pny",
  "remember": false,
  "appVersion": "00099a1be3bb023ff17d",
  "clientIpAddress": "0.0.0.4",
  "browser": "firefox"
}
```

Hmm, ok, no trailing equals sign, and there’s a % in the username, so I guess it’s not base64 encoded. What the hell is going on here? And why? Also, I’m just not going to question how “clientIpAddress” is sourced or used. Seems broken, and I don’t care.

So, first off, I tried logging in several times with the same credentials. The output was consistent, and all authentication attempts succeeded. So I assume that it’s using some non-standard encoding scheme that I’m not aware of.

I have to note, at this point, there’s no need for me to figure out what’s going on here. I _can_ log in programmatically, I’ll just have to get my encoded credentials from the site before I can plug them into my config file. But that’s no fun.

When loading [the sign-in page](https://shopgoodwill.com/signin), I see six JavaScript scripts being downloaded - four of which from shopgoodwill.com. Let’s start with those.

```plaintext
https://cdnjs.cloudflare.com/ajax/libs/cookieconsent2/3.0.3/cookieconsent.min.js
https://js.braintreegateway.com/web/dropin/1.30.1/js/dropin.min.js
https://shopgoodwill.com/runtime.13783a388351b026ceca.js
https://shopgoodwill.com/polyfills.9c38c3242fc36df5a877.js
https://shopgoodwill.com/scripts.6680d0d6cb00153f6d71.js
https://shopgoodwill.com/main.00099a1be3bb023ff17d.js
```

“main.js” is the largest script by far - 1.63 MB compared the rest, which are all smaller than 200 kB. What’s in it? A lot, it turns out. So, as you do, I downloaded and prettified it. It’s still minified, but there’s a lot of useful stuff we can figure out without having to reverse most of it.

```bash
$ grep -i username

// skipping a bunch of garbage results
this.userLoginRequestModel.userName =
  this.commonService.encryptModelValue(this.userLoginRequestModel.userName)
```

_Ok_… “encryption”, huh? Searching for “encryptModelValue” eventually brought me to this fun block of code.

```json
e.prototype.encryptModelValue = function(e) {
  var t = r.enc.Utf8.parse(this.encryptSecretKeyURL),
  n = r.enc.Utf8.parse("0000000000000000"),
  i = r.AES.encrypt(r.enc.Utf8.parse(e), t, {
    iv: n,
    padding: r.pad.Pkcs7,
    mode: r.mode.CBC
  }).toString();
  return encodeURIComponent(i) }
```

Nice IV. Even with obfuscated variable names, this is pretty readable. Given “e”, the plaintext value, the function encrypts it (after being URL-encoded) with some secret key and a useless IV with AES-CBC.

```bash
$ grep encryptSecretKeyURL

this.encryptSecretKeyURL = a.a.secretKeyURL

$ grep secretKey

e.secretKey = "0123456789123456"
e.secretKeyURL = "6696D2E6F042FEC4D6E3F32AD541143B"
```

Another great choice for a random value. Cool! Lets see if either of them work. Turns out the second one was valid, as expected. You can see my Python implementation [here](https://github.com/scottmconway/shopgoodwill-scripts/blob/main/shopgoodwill.py#L78).

So, with the mystery solved, I have to ask - _why_? Why are they doing this? What benefit does “encryption” like this add at all, if any? Whatever the answer, it’ll just be conjecture. I’ve seen many other questionable things in the ShopGoodwill API that made me gauge the competency of the designers - see my comments in the [aforementioned Github repo](https://github.com/scottmconway/shopgoodwill-scripts) if you’re looking for examples.

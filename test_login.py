import urllib.request, urllib.error, json, http.cookiejar

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
data = json.dumps({"usuario": "admin", "password": "admin123"}).encode()
req = urllib.request.Request("http://localhost:5000/api/login", data=data, headers={"Content-Type": "application/json"})
try:
    r = json.loads(opener.open(req).read())
except urllib.error.HTTPError as e:
    r = json.loads(e.read())
print("Login:", r)

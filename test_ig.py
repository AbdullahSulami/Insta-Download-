import requests
import re
def get_instagram_url(url):
    try:
        data = {"q": url, "t": "media", "lang": "en"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        resp = requests.post("https://saveig.app/api/ajaxSearch", data=data, headers=headers, timeout=15)
        html_content = resp.json().get('data', '')
        print("HTML length:", len(html_content))
        # Look for the first download link
        match = re.search(r'href="([^"]+&dl=1[^"]*)"', html_content)
        if match:
            return match.group(1).replace("&amp;", "&")
    except Exception as e:
        print("Error:", e)
    return None

import sys
url = "https://www.instagram.com/reel/DUTdiDaCB18"
dl_url = get_instagram_url(url)
print("DOWNLOAD URL:", dl_url)

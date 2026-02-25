import requests
import re

def test_ddinstagram(url):
    dd_url = url.replace("instagram.com", "ddinstagram.com")
    print(f"Testing {dd_url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(dd_url, headers=headers, timeout=10)
        print(f"Status: {r.status_code}")
        # ddinstagram usually has the video URL in a meta tag
        match = re.search(r'property="og:video" content="([^"]+)"', r.text)
        if match:
            return match.group(1)
        # Or just use the URL as is if it's a direct link (unlikely)
    except Exception as e:
        print(f"Error: {e}")
    return None

url = "https://www.instagram.com/reel/DUTdiDaCB18"
video_url = test_ddinstagram(url)
print(f"VIDEO URL: {video_url}")

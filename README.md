# 🚀 2GIS Scraper

2GIS Scraper is a **free and open-source** scraper that gets you **unlimited** detailed 2GIS data for free.

## ✨ What Can I Get?

- 🏢 **6.8M+ places in 170+ cities and regions across 17 countries** (787K+ in Moscow, 498K+ in the UAE) — name, category, address, coordinates, rating, hours and features
- 📞 **Contacts right in the search results** — phones, emails, websites, WhatsApp, Telegram, Instagram and more
- ⭐ **Every review, down to the last one** (2,400+ on Dubai Mall alone) — text, stars, author, photos, the owner's reply and the source
- 📸 **Photos, chains and categories** — 2,200+ photos of Dubai Mall alone, every branch of a chain and the full category tree of any city

## 📍 Example: A Full 2GIS Place

```json
{
  "id": "70000001017274587",
  "name": "Atlantis The Palm Dubai",
  "full_name": "Atlantis The Palm Dubai, hotel",
  "link": "https://2gis.ae/dubai/firm/70000001017274587",
  "category": "Hotels",
  "rating": 5,
  "review_count": 374,
  "rating_count": 1703,
  "address": "Atlantis The Palm Dubai Hotel, 3, Crescent East street",
  "location": { "district": "The Palm Jumeirah", "city": "Dubai", "country": "UAE", "latitude": 25.130685, "longitude": 55.116528 },
  "website": "http://www.atlantis.com",
  "phone": "+97144260000",
  "phones": [
    { "number": "+97144260000", "display": "+971 4 4260000", "comment": "multi-channel" },
    { "number": "+97144261000", "display": "+971 4 4261000", "comment": "booking center" }
  ],
  "social": [
    { "type": "instagram", "link": "https://instagram.com/atlantisthepalm" },
    { "type": "facebook", "link": "https://facebook.com/atlantisthepalm" }
  ],
  "opening_hours": { "is_open_24_7": true },
  "chain": { "name": "Atlantis", "branch_count": 2 },
  "features": [
    { "group": "Places to stay", "name": "Room rates 1500–6000 AED" },
    { "group": "Places to stay", "name": "Outdoor pool" }
  ],
  "photo_count": 547,
  "nearest_stations": [{ "name": "Atlantis Aquaventure", "distance_meters": 430, "transport_types": ["monorail"] }]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free 2GIS Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/2gis-scraper
cd 2gis-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

No proxy needed: 2GIS answers from your own connection.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/places/details?id=70000001017274587"
```

```json
{
  "id": "70000001017274587",
  "name": "Atlantis The Palm Dubai",
  "full_name": "Atlantis The Palm Dubai, hotel",
  "link": "https://2gis.ae/dubai/firm/70000001017274587",
  "category": "Hotels",
  "rating": 5,
  "review_count": 374,
  "rating_count": 1703,
  "address": "Atlantis The Palm Dubai Hotel, 3, Crescent East street",
  "location": { "district": "The Palm Jumeirah", "city": "Dubai", "country": "UAE", "latitude": 25.130685, "longitude": 55.116528 },
  "website": "http://www.atlantis.com",
  "phone": "+97144260000",
  "social": [
    { "type": "instagram", "link": "https://instagram.com/atlantisthepalm" }
  ],
  "chain": { "name": "Atlantis", "branch_count": 2, "rating": 4.9, "review_count": 448 },
  "photo_count": 547
}
```

All 9 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

9 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Place Details | `/places/details` | The whole business profile in a single call (website contacts: hosted API only) |
| Place Suggestions | `/places/suggestions` | What 2GIS suggests as you type: categories, chains, places |
| Search Places | `/places/search` | Up to 10,000 places per search, 15+ filters, 50 per page (website contacts: hosted API only) |
| Place Reviews | `/places/reviews` | Every review with text, stars, photos and the owner's reply |
| Place Photos | `/places/photos` | Every photo and video by album, in full size |
| Bulk Places | `/places/batch` | Up to 100 full business profiles in one call (website contacts: hosted API only) |
| Place Branches | `/places/branches` | Every branch of a chain in a city (website contacts: hosted API only) |
| Categories | `/categories/list` | A city's full category tree with place counts |
| Cities | `/cities/list` | Every city and region 2GIS covers, with place counts |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-2gis-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-2gis-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-2gis-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/places/details",
    params={"id": "70000001017274587"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about 2GIS Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/2gis-scraper-github-whatsapp)

[![Ask Us by Email about 2GIS Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%202GIS%20Scraper%20API&body=I%20need%20help%20using%20the%202GIS%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/2gis-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/2gis-scraper)

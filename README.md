# olx-tasker-scraper
# OLX.kz Service Listings Scraper

Production web scraper built for Task-er startup to extract and structure 
service provider data from OLX.kz for ML price estimation modelling.

## What it does
- Scrapes 15+ service categories (plumbing, electrical, tiling, etc.)
- Extracts: listing ID, title, price, city, district, seller type, description
- Outputs structured CSV dataset for machine learning
- Built with Selenium + BeautifulSoup4, CLI arguments, proper logging

## Tech stack
Python, Selenium, BeautifulSoup4, CSV, argparse

## Usage
pip install selenium webdriver-manager beautifulsoup4
python olx_scraper_priced.py --category santehnika --pages 3

## Dataset
Sample dataset included: 1,000+ real listings with price and location data.
Used to train price estimation model for Task-er platform.

# xuanpin — Cross-border Product Research Tool

[简体中文](README.md) | **English**

A product-research decision tool for **Temu full-consignment (全托管) sellers**: spot a potential hit on Pinduoduo / best-seller charts → type in a keyword → the script automatically finds suppliers on 1688, calculates landed cost & profit, and generates an Excel shortlist report.

> Design philosophy: **humans spot trends, scripts do the grunt work.** You know what will sell; the script searches suppliers, scrapes prices, does the math, and flags risks.

## ✨ Features

- 🔍 **Smart 1688 collector** — keyword search that extracts titles, wholesale prices, MOQ, sales signals, and shop info
  - Three-layer parsing strategy (mtop API → generic XHR-JSON → DOM fallback), so it keeps working when the page layout changes
  - GBK keyword encoding (a known 1688 quirk) and precise parsing of multi-line price text on product cards
  - Persistent login: scan the QR code once, stay logged in for good
- 💰 **Cost & profit model** — supply-price ceiling / landed cost (sourcing + packaging + domestic shipping + overweight surcharge) / conservative & optimistic profit
- ⚠️ **Automatic risk flags** — red warnings when a title hits brand-infringement keywords or regulated categories (toys EN71/CPC, electronics CE/FCC, food-contact FDA, etc.)
- 📊 **Excel shortlist** — sorted by conservative profit, negative profit in red, clickable product links, run parameters recorded for review
- 🗄️ **Price history** — every scrape is snapshotted into SQLite; run the same keyword twice to see price trends
- 🐞 **Debug-friendly** — on failure it auto-saves screenshot + HTML + raw API payloads, making page-change breakages quick to fix

## 🔄 Workflow

```
Spot a potential product on Pinduoduo / charts → check its real retail price on Temu
        ↓
python main.py search "keyword" --retail <target price>
        ↓
Script: search 1688 → scrape wholesale price / MOQ / sales → compute cost & profit → flag risks
        ↓
Excel report (sorted by conservative profit) → manual review of links → decide whether to quote
```

## 🚀 Quick Start

Requires Python 3.10+. Works on Windows and macOS.

```bash
git clone https://github.com/77372562/xuanpin.git
cd xuanpin
pip install -r requirements.txt
python -m playwright install chromium

# First time: log in to 1688 (scan QR code once)
python main.py login

# Daily use: find suppliers (--retail is the REAL retail price you saw on Temu)
python main.py search "drain basket" --retail 15.9

# Optional: estimated unit weight (grams) and number of pages
python main.py search "mug" --retail 19.9 --weight 350 --pages 3

# Self-test without a browser (mock data, runs the whole pipeline)
python main.py test
```

### 🖱️ Web UI (no command line, recommended)

Double-click **`选品界面.bat`** — a browser page opens with:

- **Candidate list**: add/edit/remove keywords (keyword + Temu retail price + optional weight) in a table
- **One-click batch workflow** / **quick single-product lookup** without touching the list
- **Live logs**: scraping progress and TOP rankings stream on the page
- **Reports**: download every Excel from the page, or open the reports folder directly
- Login is a button too (scan the QR in the popup browser)

All command-line commands still work (`login` / `search` / `batch` / `test`).

### ⚡ CLI batch workflow

Skip typing commands every day:

1. Open `keywords.txt` in Notepad and list your candidate products (one per line), e.g.:

   ```
   # keyword,TemuRetailPrice,weightGrams(optional)
   沥水篮,15.9
   马克杯,19.9,350
   ```

2. **Double-click `每日选品.bat`** — the script checks login, scrapes & costs every keyword, and produces one independent report per keyword plus a **combined summary workbook** ranking all candidates by conservative profit.

3. Open the summary, scan from the top: positive profit, no risk flags, affordable MOQ.

Keywords without a price will prompt you at runtime. Point Windows Task Scheduler at the .bat file for a fully automated daily report.

## 📈 How profit is calculated

```
supply-price ceiling = Temu retail price × (1 - platform_take)   # platform_take defaults to 0.5
landed cost          = 1688 purchase price + packaging + domestic shipping (+ overweight surcharge)
conservative profit  = ceiling - landed cost (purchase at the HIGH end of wholesale range)
optimistic profit    = same (purchase at the LOW end)
```

All cost parameters live in `config.yaml` — calibrate them with your own freight quotes before trusting the numbers.

## ⚙️ Configuration

| Key | Description | Default |
|---|---|---|
| `costing.packaging_fee` | Re-packaging consumables, CNY/unit | 0.8 |
| `costing.inbound_ship` | Domestic courier to platform warehouse, CNY/unit | 5.0 |
| `costing.free_weight_g` | Weight threshold before overweight fees, grams | 1000 |
| `costing.overweight_rate_per_kg` | Overweight surcharge, CNY/kg | 4.0 |
| `fees.platform_take` | Platform cut (retail → supply-price loss) | 0.5 |
| `search.max_pages` | Pages scraped per run | 2 |
| `risk.brand_words` | Brand-infringement keyword list | Disney, Pokémon, … |
| `risk.cert_rules` | Regulated-category rules | toys / electronics / baby, … |

## 📁 Project layout

```
xuanpin/
├── main.py              # CLI entry (login / search / test)
├── config.yaml          # cost params + risk lexicons (all adjustable)
├── xuanpin/
│   ├── collector1688.py # 1688 collector (3-layer parsing + debug dump)
│   ├── costing.py       # cost & profit model
│   ├── risk.py          # infringement / certification flags
│   ├── report.py        # Excel report builder
│   └── db.py            # SQLite storage (products / suppliers / price history)
├── data/                # runtime data (auto-created, gitignored)
└── reports/             # generated reports (gitignored)
```

## ❓ FAQ

**Zero results?** Re-run `python main.py login` first. If it still fails, the page layout has probably changed — send the maintainer the screenshot/HTML/JSON auto-saved under `data/debug/`.

**Can I trust the profit numbers?** The cost side (purchase price, weight, surcharges) is computed by the script, but the revenue side `--retail` must be looked up by you on Temu — that same look is your chance to gauge competition.

**Will 1688 flag me?** The script uses a persistent login plus randomized page delays to mimic human pacing, but keep the frequency low (a few keywords a day is plenty) and use it only for personal research.

## 🗺️ Roadmap

- [ ] Auto-scrape unit weight & MOQ tiers from product detail pages
- [ ] Auto-compare Temu listing prices (replace manual --retail)
- [x] Batch keyword runs + combined summary sheet (`python main.py batch` / double-click `每日选品.bat`)
- [x] Local web UI (double-click `选品界面.bat`: browser operation + live logs + report downloads)
- [ ] Scheduled runs + new-product / price-change alerts

## ⚠️ Disclaimer

This project is for personal product-research and educational purposes only. Comply with the terms of service of 1688 and target platforms, keep scrape frequency low, and do not resell collected data. Use at your own risk.

## 📄 License

[MIT](LICENSE)

# MTG Proxy Tool CLI

A powerful Python command-line tool for generating high-quality, print-ready PDFs for Magic: The Gathering proxies. It fetches decklists directly from Archidekt or CSV files, downloads the matching high-resolution images from Scryfall, and formats them for easy printing and cutting.

## Features

* **Direct Archidekt Integration:** Paste a deck URL, and it handles the rest.
* **Smart Formatting:** Can automatically separate Single-Faced Cards (standard) and Double-Faced Cards (Transform/MDFC) into separate PDFs to save paper.
* **Batch Processing:** Queue up multiple decks in a text file and let it run in the background.
* **Persistent Caching:** Downloads are saved to a local `card_images` folder. If you print a deck with a Sol Ring today and another one tomorrow, it won't re-download the image.
* **Scryfall Rate Limiting:** Built-in thread-safe rate limiter ensures you don't get banned by the Scryfall API.
* **Precision Cut Lines:** Generates "Tic-Tac-Toe" style cut lines that extend to the page edge. If you add padding, it creates gutters for perfect rotary cutter alignment.
* **Clean Organization:** All outputs are sorted into a structured `Output/` directory.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/WesYarber/mtg_proxy_tool
    cd mtg-proxy-tool
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

### 1. Single Deck Mode
The simplest way to print a single deck.

```bash
python mtg_proxy_tool.py --input https://archidekt.com/decks/1234567/my_deck
```

**Common Examples:**

* **Smart Formatting (Default):** Separates standard cards from double-faced cards.
    ```bash
    python mtg_proxy_tool.py --input <URL> --format smart
    ```
* **Double-Sided Only:** Generates a single PDF with all cards (fronts and backs interleaved) for duplex printing.
    ```bash
    python mtg_proxy_tool.py --input <URL> --format double
    ```
* **Generate Both Formats:** Creates two complete PDFs: one fully single-sided and one fully double-sided.
    ```bash
    python mtg_proxy_tool.py --input <URL> --format both
    ```
* **Add Padding:** Adds 1mm spacing between cards (useful for creating "gutters" for rotary cutters).
    ```bash
    python mtg_proxy_tool.py --input <URL> --padding_mm 1.0
    ```

---

### 2. Batch Mode
Process multiple decks sequentially. This is ideal for printing an entire gauntlet of decks at once.

1.  **Create a batch file (e.g., `decks.txt`):**
    Each line should be a deck URL. You can optionally add a `|` followed by a custom name to rename the deck folder.
    ```text
    https://archidekt.com/decks/111111
    https://archidekt.com/decks/222222 | My Custom Vintage Cube
    https://archidekt.com/decks/333333
    ```

2.  **Run the script:**
    ```bash
    python mtg_proxy_tool.py --batch_file decks.txt
    ```

**Batch "Smart" Feature:**
When running in batch mode with `--format smart` (the default), the script automatically detects **Double-Faced Cards (DFCs)** across *all* listed decks. It will:
1.  Generate a standard single-sided PDF for every deck in its own folder.
2.  Combine all DFCs from all decks into a single `Combined_Double_Sided_Cards.pdf` in the batch root folder.
3.  Generate a `DFC_Manifest.txt` listing which deck each double-sided card belongs to.

---

### 3. Arguments Reference

| Argument | Description | Default |
| :--- | :--- | :--- |
| **`--input`** | The Archidekt URL or path to a CSV file for a single deck. | `None` |
| **`--batch_file`** | Path to a text file containing a list of URLs for batch processing. Overrides `--input`. | `None` |
| **`--deckname`** | Override the deck name (used for folder naming). If omitted, fetches the name from Archidekt. | `None` |
| **`--output_dir`** | The root directory where output folders are created. | `Output` |
| **`--format`** | **`single`**: One PDF, single-sided (fronts only).<br>**`double`**: One PDF, double-sided (backs interleaved).<br>**`both`**: Generates both of the above.<br>**`smart`**: Splits the deck into two PDFs: one for single-faced cards, one for double-faced cards. | `single` |
| **`--padding_mm`** | Amount of whitespace (in millimeters) to add between cards. | `0.0` |
| **`--include_sideboard`** | Flag to include the deck's sideboard in the print. | `False` |
| **`--include_maybeboard`** | Flag to include the deck's maybeboard in the print. | `False` |
| **`--default_back_image`** | Path to a local image file to use as the back for cards that don't have a specific back face (used in `double` or `smart` modes). | `None` |
| **`--purge_new`** | If set, deletes only the card images downloaded during *this specific run* after the PDF is built. Useful for saving disk space while keeping your main cache intact. | `False` |

## Directory Structure

The tool organizes your files cleanly. Here is an example of what your folder will look like after running both a **Single Deck** run (Sticky Spiderman) and a **Batch Run** (Deck A and Deck B).

```
/
├── card_images/              # Persistent cache of card art (shared across all runs)
├── Output/
│   │
│   ├── Sticky_Spiderman/     # [Single, Double, or Both Run Output]
│   │   ├── deck_list.csv                 # CSV export of the deck list
│   │   ├── Sticky_Spiderman_Standard.pdf # Single-faced cards
│   │   └── Sticky_Spiderman_Double.pdf   # Double-faced cards (if 'smart' or 'double' mode)
│   │
│   └── Batch_20231027/       # ["Smart" Batch Run Output]
│       ├── Combined_Double_Sided.pdf     # Master PDF of DFCs from ALL decks in batch
│       ├── DFC_Manifest.txt              # List mapping DFCs to their specific decks
│       ├── Deck_A/
│       │   ├── deck_list.csv             # CSV export for Deck A
│       │   └── Deck_A_Standard.pdf                # Single-sided PDF for Deck A
│       └── Deck_B/
│           ├── deck_list.csv
│           └── Deck_B_Standard.pdf
├── decks.txt
└── mtg_proxy_tool.py
```

## CSV Input Format
If you prefer not to use Archidekt, you can supply a CSV file with the following headers:
`count,name,set_code,collector_number,lang,scryfall_id`

## Legal

This tool is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.
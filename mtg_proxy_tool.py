import argparse
import requests
import csv
import re
import os
import json
import datetime
import time
import threading
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
from reportlab.lib.colors import gray, black
from tqdm import tqdm
from collections import defaultdict
from PIL import Image

# --- THREAD-SAFE RATE LIMITER ---
class RateLimiter:
    def __init__(self, min_interval=0.1):
        self.min_interval = min_interval
        self.last_request_time = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time
            wait_time = self.min_interval - elapsed
            
            if wait_time > 0:
                self.last_request_time = current_time + wait_time
            else:
                self.last_request_time = current_time
                wait_time = 0
                
        if wait_time > 0:
            time.sleep(wait_time)

scryfall_limiter = RateLimiter(min_interval=0.1)

# --- CONFIGURATION ---
PAGE_WIDTH_MM = 215.90
PAGE_HEIGHT_MM = 279.40
TOP_MARGIN_MM = 3.18
BOTTOM_MARGIN_MM = 3.18
LEFT_MARGIN_MM = 6.35  
RIGHT_MARGIN_MM = 6.35 

CARD_WIDTH_MM = 63
CARD_HEIGHT_MM = 88
CORNER_RADIUS_MM = 2

CUT_LINE_THICKNESS_MM = 0.2
GRID_COLS = 3
GRID_ROWS = 3

FOOTER_FONT = "Helvetica"
FOOTER_SIZE = 10
FOOTER_INSET_MM = 1
FOOTER_BELOW_GRID_MM = 3.5

MM_TO_PT = 72 / 25.4
PAGE_SIZE = (PAGE_WIDTH_MM * MM_TO_PT, PAGE_HEIGHT_MM * MM_TO_PT)
TOP_MARGIN = TOP_MARGIN_MM * MM_TO_PT
BOTTOM_MARGIN = BOTTOM_MARGIN_MM * MM_TO_PT
LEFT_MARGIN = LEFT_MARGIN_MM * MM_TO_PT
RIGHT_MARGIN = RIGHT_MARGIN_MM * MM_TO_PT

CARD_WIDTH = CARD_WIDTH_MM * MM_TO_PT
CARD_HEIGHT = CARD_HEIGHT_MM * MM_TO_PT
CORNER_RADIUS = CORNER_RADIUS_MM * MM_TO_PT

CUT_LINE_THICKNESS = CUT_LINE_THICKNESS_MM * MM_TO_PT
FOOTER_INSET = FOOTER_INSET_MM * MM_TO_PT
FOOTER_BELOW_GRID = FOOTER_BELOW_GRID_MM * MM_TO_PT

# Global set to track which files were downloaded IN THIS RUN
downloaded_files_this_run = set()
download_tracker_lock = threading.Lock()

def parse_input(input_str):
    if input_str.startswith('http'):
        match = re.search(r'/decks/(\d+)', input_str)
        if not match:
            raise ValueError(f"Invalid Archidekt URL: {input_str}")
        deck_id = match.group(1)
        return fetch_archidekt_deck(deck_id)
    else:
        return parse_csv(input_str)

def parse_batch_file(file_path):
    decks_to_process = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            
            parts = line.split('|')
            url = parts[0].strip()
            custom_name = parts[1].strip() if len(parts) > 1 else None
            
            if url:
                decks_to_process.append({'url': url, 'custom_name': custom_name})
    return decks_to_process

def fetch_archidekt_deck(deck_id, include_maybeboard=False, include_sideboard=False):
    url = f"https://archidekt.com/api/decks/{deck_id}/"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Error: Failed to fetch deck {deck_id} (Status {response.status_code})")
        return [], {'name': f"Error_Deck_{deck_id}", 'author': 'Unknown'}
        
    data = response.json()
    if 'cards' not in data:
         print(f"Error: Unexpected API response for deck {deck_id}")
         return [], {'name': f"Error_Deck_{deck_id}", 'author': 'Unknown'}

    deck_name = data.get('name', 'Unknown Deck')
    owner_data = data.get('owner', {})
    author = owner_data.get('username', 'Unknown Author')
    
    cards = []
    for entry in data['cards']:
        categories = entry.get('categories', [])
        if 'Maybeboard' in categories and not include_maybeboard:
            continue
        if 'Sideboard' in categories and not include_sideboard:
            continue
        
        quantity = entry.get('quantity', 1)
        card_data = entry.get('card', {})
        oracle = card_data.get('oracleCard', {})
        edition = card_data.get('edition', {})
        
        name = oracle.get('name')
        if not name:
            continue
        
        set_code = edition.get('editioncode', '').lower()
        collector_number = card_data.get('collectorNumber')
        scryfall_id = card_data.get('uid')
        
        lang = oracle.get('lang', 'en')
        
        for _ in range(quantity):
            cards.append({
                'scryfall_id': scryfall_id,
                'lang': lang,
                'name': name,
                'set_code': set_code,
                'collector_number': collector_number
            })
            
    if not cards:
         print(f"Warning: No cards found in deck {deck_id}")

    cards.sort(key=lambda c: c['name'].lower())
    return cards, {'name': deck_name, 'author': author}

def parse_csv(file_path):
    cards = []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            quantity = int(row['count'])
            for _ in range(quantity):
                cards.append({
                    'scryfall_id': row['scryfall_id'],
                    'lang': row['lang'],
                    'name': row['name'].strip('"'),
                    'set_code': row['set_code'],
                    'collector_number': row['collector_number']
                })
    cards.sort(key=lambda c: c['name'].lower())
    return cards, {'name': None, 'author': None}

def get_card_image_url(card, face="front"):
    if card['scryfall_id']:
        base = f"https://api.scryfall.com/cards/{card['scryfall_id']}?format=image&version=png"
        if face == "back":
            base += "&face=back"
        return base
    raise ValueError(f"Cannot get {face} face without scryfall_id for {card['name']}")

def get_clean_filename(card, is_back=False):
    suffix = "_back" if is_back else ""
    safe_name = (
        card['name']
        .replace(' // ', '_')
        .replace(',', '')
        .replace(' ', '_')
        .replace('"', '')
        .lower()
    )
    safe_name = safe_name[:100] 
    return f"{safe_name}_{card['set_code']}_{card['collector_number']}{suffix}.png"

def download_image(url, card=None, image_dir="", is_back=False):
    if image_dir and card:
        filename = get_clean_filename(card, is_back)
        path = os.path.join(image_dir, filename)
        if os.path.exists(path):
            return True 
    
    scryfall_limiter.wait()

    try:
        response = requests.get(url, allow_redirects=True, timeout=10)
        if response.status_code == 422: return None
        if response.status_code == 429:
            print(f"Rate limited (429). Backing off for 5 seconds...")
            time.sleep(5) 
            return download_image(url, card, image_dir, is_back)
        if response.status_code != 200:
            print(f"Failed to download {url} (Status {response.status_code})")
            return None
        
        if image_dir and card:
            with open(path, 'wb') as f:
                f.write(response.content)
            with download_tracker_lock:
                downloaded_files_this_run.add(path)
        return True
    except requests.exceptions.RequestException as e:
         print(f"Error downloading image: {e}")
         return None

def save_card_list_as_csv(cards, csv_path):
    card_dict = defaultdict(int)
    card_info = {}
    for card in cards:
        key = (card['scryfall_id'], card['lang'], card['name'], card['set_code'], card['collector_number'])
        card_dict[key] += 1
        card_info[key] = card
    
    sorted_items = sorted(card_dict.items(), key=lambda item: card_info[item[0]]['name'].lower())
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['scryfall_id', 'count', 'lang', 'name', 'set_code', 'collector_number'])
        for key, count in sorted_items:
            card = card_info[key]
            writer.writerow([card['scryfall_id'] or '', count, card['lang'], card['name'], card['set_code'] or '', card['collector_number'] or ''])

def parallel_download(cards, image_dir, backs_pdf):
    seen_keys = set()
    unique_cards_to_download = []
    
    desc_text = "Checking/Downloading backs" if backs_pdf else "Checking/Downloading fronts"
    
    for card in cards:
        filename = get_clean_filename(card, is_back=backs_pdf)
        if filename not in seen_keys:
             unique_cards_to_download.append(card)
             seen_keys.add(filename)

    def download_task(card):
        url = get_card_image_url(card, face="back" if backs_pdf else "front")
        return download_image(url, card=card, image_dir=image_dir, is_back=backs_pdf)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(download_task, card) for card in unique_cards_to_download]
        for _ in tqdm(as_completed(futures), total=len(unique_cards_to_download), desc=desc_text, unit="img", leave=False):
            pass

def resize_default_back(image_path):
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        img = Image.open(image_path)
        target_size = (750, 1050)
        img_resized = img.resize(target_size, resample=Image.Resampling.LANCZOS)
        buf = BytesIO()
        img_resized.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        print(f"Warning: Could not resize back image ({e}). Using original.")
        with open(image_path, 'rb') as f:
            return f.read()

def draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y):
    c.setLineWidth(CUT_LINE_THICKNESS)
    c.setStrokeColor(black)
    for col in range(GRID_COLS):
        x_left = x_start + col * (CARD_WIDTH + spacing_x)
        x_right = x_left + CARD_WIDTH
        c.line(x_left, 0, x_left, PAGE_SIZE[1])
        c.line(x_right, 0, x_right, PAGE_SIZE[1])

    for row in range(GRID_ROWS):
        y_bottom = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
        y_top = y_bottom + CARD_HEIGHT
        c.line(0, y_bottom, PAGE_SIZE[0], y_bottom)
        c.line(0, y_top, PAGE_SIZE[0], y_top)

def generate_pdf(cards, output_dir, filename_base, footer_text=None, image_dir="", padding=0, double_sided=False, default_back_image_bytes=None):
    if not cards: return None
    from reportlab import rl_config
    rl_config.pageCompression = 1

    output_path = os.path.join(output_dir, f"{filename_base}.pdf")
    final_footer_text = footer_text if footer_text else filename_base.replace('_', ' ')
    
    usable_width = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN
    usable_height = PAGE_SIZE[1] - TOP_MARGIN - BOTTOM_MARGIN
    spacing_x = padding
    spacing_y = padding
    grid_width = GRID_COLS * CARD_WIDTH + (GRID_COLS - 1) * spacing_x
    grid_height = GRID_ROWS * CARD_HEIGHT + (GRID_ROWS - 1) * spacing_y
    x_start = LEFT_MARGIN + (usable_width - grid_width) / 2
    y_start_offset = (usable_height - grid_height) / 2

    footer_y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - grid_height - FOOTER_BELOW_GRID
    left_footer_x = x_start + FOOTER_INSET
    right_footer_x = x_start + grid_width - FOOTER_INSET

    # 1. Ensure Images
    parallel_download(cards, image_dir, backs_pdf=False)
    if double_sided: parallel_download(cards, image_dir, backs_pdf=True)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    print(f"Building PDF: {os.path.basename(output_path)}...")

    if double_sided:
        total_pages = ((len(cards) + GRID_COLS * GRID_ROWS - 1) // (GRID_COLS * GRID_ROWS)) * 2
        item_index = 0
        with tqdm(total=len(cards) * 2, desc="Placing cards", unit="card", leave=False) as pbar:
            while item_index < len(cards):
                page_num_base = (item_index // (GRID_COLS * GRID_ROWS)) + 1
                
                # Front
                draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and item_index < len(cards):
                    row = placed // GRID_COLS
                    col = placed % GRID_COLS
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    
                    card = cards[item_index]
                    filename = get_clean_filename(card, is_back=False)
                    local_path = os.path.join(image_dir, filename)
                    
                    if not os.path.exists(local_path):
                         download_image(get_card_image_url(card, "front"), card, image_dir, False)
                    
                    if os.path.exists(local_path):
                        img_reader = ImageReader(local_path)
                        c.saveState()
                        clip_path = c.beginPath()
                        clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, CORNER_RADIUS)
                        c.clipPath(clip_path, stroke=0, fill=0)
                        c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                        c.restoreState()
                    
                    placed += 1
                    item_index += 1
                    pbar.update(1)

                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2 - 1} / {total_pages}")
                c.showPage()

                # Back
                draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y)
                back_start = item_index - placed
                col_order = range(GRID_COLS - 1, -1, -1)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and back_start + placed < len(cards):
                    row = placed // GRID_COLS
                    col = col_order[placed % GRID_COLS]
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y

                    card = cards[back_start + placed]
                    back_filename = get_clean_filename(card, is_back=True)
                    back_local_path = os.path.join(image_dir, back_filename)

                    if not os.path.exists(back_local_path) and not default_back_image_bytes:
                         download_image(get_card_image_url(card, "back"), card, image_dir, True)

                    img_data = None
                    if os.path.exists(back_local_path): img_data = back_local_path
                    elif default_back_image_bytes: img_data = BytesIO(default_back_image_bytes)

                    if img_data:
                        img_reader = ImageReader(img_data)
                        c.saveState()
                        clip_path = c.beginPath()
                        clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, CORNER_RADIUS)
                        c.clipPath(clip_path, stroke=0, fill=0)
                        c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                        c.restoreState()

                    placed += 1
                    pbar.update(1)

                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text + " (Backs)")
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2} / {total_pages}")
                c.showPage()
    else:
        # Single Sided
        total_items = len(cards)
        total_pages = (total_items + GRID_COLS * GRID_ROWS - 1) // (GRID_COLS * GRID_ROWS)
        item_index = 0
        with tqdm(total=total_items, desc="Placing cards", unit="item", leave=False) as pbar:
            while item_index < total_items:
                page_num = (item_index // (GRID_COLS * GRID_ROWS)) + 1
                draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y)
                
                for row in range(GRID_ROWS):
                    for col in range(GRID_COLS):
                        if item_index >= total_items: break
                        x = x_start + col * (CARD_WIDTH + spacing_x)
                        y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                        
                        card = cards[item_index]
                        filename = get_clean_filename(card, is_back=False)
                        local_path = os.path.join(image_dir, filename)
                        
                        if not os.path.exists(local_path):
                             download_image(get_card_image_url(card, "front"), card, image_dir, False)
                        
                        if os.path.exists(local_path):
                            img_reader = ImageReader(local_path)
                            c.saveState()
                            clip_path = c.beginPath()
                            clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, CORNER_RADIUS)
                            c.clipPath(clip_path, stroke=0, fill=0)
                            c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                            c.restoreState()
                        
                        item_index += 1
                        pbar.update(1)

                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num} / {total_pages}")
                c.showPage()

    c.save()
    with open(output_path, 'wb') as f:
        f.write(buffer.getvalue())
    buffer.close()
    return output_path

def run_single_mode(args):
    output_root = args.output_dir
    os.makedirs(output_root, exist_ok=True)
    central_image_dir = "card_images"
    os.makedirs(central_image_dir, exist_ok=True)
    padding_pt = args.padding_mm * MM_TO_PT
    default_back_bytes = resize_default_back(args.default_back_image) if args.default_back_image else None

    cards, metadata = parse_input(args.input)
    if not cards: return

    resolved_deckname = args.deckname if args.deckname else (metadata['name'] if metadata['name'] else "My_Deck")
    author_name = metadata['author']
    footer_text = f"{resolved_deckname} - {author_name}" if author_name else resolved_deckname
    
    print(f"\nProcessing Deck: {resolved_deckname}")
    print(f"Format: {args.format}")

    deck_folder = os.path.join(output_root, resolved_deckname.replace(' ', '_').replace('/', '_'))
    os.makedirs(deck_folder, exist_ok=True)
    
    save_card_list_as_csv(cards, os.path.join(deck_folder, "deck_list.csv"))
    
    parallel_download(cards, central_image_dir, backs_pdf=False)
    parallel_download(cards, central_image_dir, backs_pdf=True)

    if args.format == 'smart':
        sfc_cards = []
        dfc_cards = []
        for card in cards:
            back_filename = get_clean_filename(card, is_back=True)
            if os.path.exists(os.path.join(central_image_dir, back_filename)): dfc_cards.append(card)
            else: sfc_cards.append(card)
        
        print(f"Single-Faced Cards: {len(sfc_cards)}")
        print(f"Double-Faced Cards: {len(dfc_cards)}")

        if sfc_cards:
            generate_pdf(sfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_Standard", footer_text, central_image_dir, padding_pt, False, None)
        if dfc_cards:
            generate_pdf(dfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_DoubleSided", footer_text + " (DFC)", central_image_dir, padding_pt, True, default_back_bytes)
    else:
        modes_to_run = []
        if args.format == 'single': modes_to_run.append(False)
        elif args.format == 'double': modes_to_run.append(True)
        elif args.format == 'both': modes_to_run.append(False); modes_to_run.append(True)

        for is_double in modes_to_run:
            suffix = "DoubleSided" if is_double else "Standard"
            generate_pdf(cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, padding_pt, is_double, default_back_bytes)

    print(f"\nDone! Files saved to: {deck_folder}")
    if args.purge_new and downloaded_files_this_run:
        print(f"\nPurging {len(downloaded_files_this_run)} newly downloaded files...")
        for file_path in downloaded_files_this_run:
            try: os.remove(file_path)
            except: pass

def run_batch_mode(args):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = args.output_dir
    os.makedirs(output_root, exist_ok=True)
    batch_dir = os.path.join(output_root, f"Batch_{timestamp}")
    os.makedirs(batch_dir, exist_ok=True)
    central_image_dir = "card_images"
    os.makedirs(central_image_dir, exist_ok=True)
    padding_pt = args.padding_mm * MM_TO_PT
    default_back_bytes = resize_default_back(args.default_back_image) if args.default_back_image else None

    decks_to_process = parse_batch_file(args.batch_file)
    print(f"Found {len(decks_to_process)} decks in batch file.")
    
    master_dfc_list = []

    for i, deck_entry in enumerate(decks_to_process):
        url = deck_entry['url']
        custom_name = deck_entry['custom_name']
        print(f"\n--- Processing Deck {i+1}/{len(decks_to_process)} ---")
        try:
            match = re.search(r'/decks/(\d+)', url)
            if not match: continue
            deck_id = match.group(1)
            cards, metadata = fetch_archidekt_deck(deck_id, args.include_maybeboard, args.include_sideboard)
            if not cards: continue

            resolved_deckname = custom_name if custom_name else metadata['name']
            author_name = metadata['author']
            footer_text = f"{resolved_deckname} - {author_name}"
            print(f"Deck: {resolved_deckname}")
            
            deck_subdir = os.path.join(batch_dir, resolved_deckname.replace(" ", "_").replace("/", "_"))
            os.makedirs(deck_subdir, exist_ok=True)
            save_card_list_as_csv(cards, os.path.join(deck_subdir, "deck_list.csv"))

            parallel_download(cards, central_image_dir, backs_pdf=False)
            parallel_download(cards, central_image_dir, backs_pdf=True)

            if args.format == 'smart':
                sfc_cards = []
                for card in cards:
                    back_filename = get_clean_filename(card, is_back=True)
                    if os.path.exists(os.path.join(central_image_dir, back_filename)):
                        card_with_source = card.copy()
                        card_with_source['source_deck_name'] = resolved_deckname
                        master_dfc_list.append(card_with_source)
                    else:
                        sfc_cards.append(card)
                if sfc_cards:
                    generate_pdf(sfc_cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_Standard_Cards", footer_text, central_image_dir, padding_pt, False, None)
            else:
                modes_to_run = []
                if args.format == 'single': modes_to_run.append(False)
                elif args.format == 'double': modes_to_run.append(True)
                elif args.format == 'both': modes_to_run.append(False); modes_to_run.append(True)

                for is_double in modes_to_run:
                    suffix = "DoubleSided" if is_double else "Standard"
                    generate_pdf(cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, padding_pt, is_double, default_back_bytes)

        except Exception as e:
             print(f"Error processing deck {url}: {e}")

    # Process Master DFC List (ONLY IF SMART MODE WAS ACTIVE)
    if args.format == 'smart' and master_dfc_list:
        print("\n--- Generating Combined Double-Sided PDF ---")
        generate_pdf(master_dfc_list, batch_dir, "Combined_Double_Sided_Cards", "Combined Double-Sided Cards (All Decks)", central_image_dir, padding_pt, True, default_back_bytes)
        manifest_path = os.path.join(batch_dir, "DFC_Manifest.txt")
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write("--- Manifest of Double-Faced Cards ---\n\n")
            decks_dfc_map = defaultdict(list)
            for card in master_dfc_list: decks_dfc_map[card['source_deck_name']].append(card)
            for deck_name, cards in decks_dfc_map.items():
                 f.write(f"=== {deck_name} ===\n")
                 cards.sort(key=lambda x: x['name'])
                 for card in cards: f.write(f"- {card['name']}\n")
                 f.write("\n")
                 
    print(f"\nBatch processing complete!")
    if args.purge_new and downloaded_files_this_run:
        print(f"\nPurging {len(downloaded_files_this_run)} newly downloaded files...")
        for file_path in downloaded_files_this_run:
            try: os.remove(file_path)
            except: pass

def main():
    parser = argparse.ArgumentParser(description="MTG Proxy Printer CLI")
    parser.add_argument('--batch_file', help='Path to text file containing list of deck URLs (one per line). Overrides --input.')
    parser.add_argument('--input', help='CSV file or Archidekt deck URL')
    parser.add_argument('--deckname', default=None, help='Deck name. If omitted, fetches from Archidekt.')
    parser.add_argument('--output_dir', default="Output", help='Directory where PDF files and manifests will be saved (default: "Output").')
    parser.add_argument('--format', choices=['single', 'double', 'both', 'smart'], default='single', 
                        help='Output format. "smart" splits the deck into Single-Sided and Double-Sided PDFs.')
    parser.add_argument('--padding_mm', type=float, default=0.0, help='Padding between cards in mm')
    parser.add_argument('--include_maybeboard', action='store_true')
    parser.add_argument('--include_sideboard', action='store_true')
    parser.add_argument('--default_back_image', default=None, help='Path to default back image')
    parser.add_argument('--purge_new', action='store_true', help='Delete only the card images downloaded during this run.')
    args = parser.parse_args()

    if args.batch_file:
        if not os.path.exists(args.batch_file):
             print(f"Error: Batch file not found at {args.batch_file}")
             return
        run_batch_mode(args)
    elif args.input:
        run_single_mode(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
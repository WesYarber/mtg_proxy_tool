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
from reportlab.lib.colors import HexColor
from tqdm import tqdm
from collections import defaultdict
from PIL import Image

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

# Defaults
DEFAULT_CUT_LINE_THICKNESS_MM = 0.2
DEFAULT_CUT_LINE_COLOR = "#000000"

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

FOOTER_INSET = FOOTER_INSET_MM * MM_TO_PT
FOOTER_BELOW_GRID = FOOTER_BELOW_GRID_MM * MM_TO_PT

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

class ProxyEngine:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.downloaded_files_this_run = set()
        self.download_tracker_lock = threading.Lock()

    def log(self, message):
        if self.progress_callback:
            self.progress_callback(message)
        else:
            print(message)

    def parse_input(self, input_str, include_maybeboard=False, include_sideboard=False):
        # Handle multiple URLs (Batching)
        lines = [L.strip() for L in input_str.split('\n') if L.strip()]
        
        results = []
        for line in lines:
            if line.startswith('http'):
                match = re.search(r'/decks/(\d+)', line)
                if match:
                    deck_id = match.group(1)
                    cards, meta = self.fetch_archidekt_deck(deck_id, include_maybeboard, include_sideboard)
                    if cards:
                        results.append((cards, meta))
            elif os.path.exists(line):
                # Handle CSV path
                cards, meta = self.parse_csv_file(line)
                results.append((cards, meta))
        
        return results

    def fetch_archidekt_deck(self, deck_id, include_maybeboard=False, include_sideboard=False):
        self.log(f"Fetching deck {deck_id} from Archidekt...")
        url = f"https://archidekt.com/api/decks/{deck_id}/"
        response = requests.get(url)
        if response.status_code != 200:
            self.log(f"Error: Failed to fetch deck {deck_id} (Status {response.status_code})")
            return [], {'name': f"Error_Deck_{deck_id}", 'author': 'Unknown'}
        data = response.json()
        if 'cards' not in data:
            self.log(f"Error: Unexpected API response for deck {deck_id}")
            return [], {'name': f"Error_Deck_{deck_id}", 'author': 'Unknown'}
        deck_name = data.get('name', 'Unknown Deck')
        owner_data = data.get('owner', {})
        author = owner_data.get('username', 'Unknown Author')
        cards = []
        for entry in data['cards']:
            categories = entry.get('categories') or []
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
            self.log(f"Warning: No cards found in deck {deck_id}")
        cards.sort(key=lambda c: c['name'].lower())
        self.log(f"Found {len(cards)} cards for {deck_name}.")
        return cards, {'name': deck_name, 'author': author}

    def parse_csv_file(self, file_path):
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
        return cards, {'name': 'CSV_Import', 'author': 'Unknown'}

    def get_card_image_url(self, card, face="front", size="png"):
        # size can be 'png' (large high qual) or 'small' (for previews)
        if card['scryfall_id']:
            base = f"https://api.scryfall.com/cards/{card['scryfall_id']}?format=image&version={size}"
            if face == "back":
                base += "&face=back"
            return base
        raise ValueError(f"Cannot get {face} face without scryfall_id for {card['name']}")

    def get_clean_filename(self, card, is_back=False):
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

    def download_image(self, url, card=None, image_dir="", is_back=False):
        if image_dir and card:
            filename = self.get_clean_filename(card, is_back)
            path = os.path.join(image_dir, filename)
            if os.path.exists(path):
                return True
        scryfall_limiter.wait()
        try:
            response = requests.get(url, allow_redirects=True, timeout=10)
            if response.status_code == 422: return None
            if response.status_code == 429:
                self.log(f"Rate limited (429). Backing off for 5 seconds...")
                time.sleep(5)
                return self.download_image(url, card, image_dir, is_back)
            if response.status_code != 200:
                self.log(f"Failed to download {url} (Status {response.status_code})")
                return None
            if image_dir and card:
                with open(path, 'wb') as f:
                    f.write(response.content)
                with self.download_tracker_lock:
                    self.downloaded_files_this_run.add(path)
                return True
        except requests.exceptions.RequestException as e:
            self.log(f"Error downloading image: {e}")
            return None

    def parallel_download(self, cards, image_dir, backs_pdf):
        seen_keys = set()
        unique_cards_to_download = []
        desc_text = "Downloading backs" if backs_pdf else "Downloading fronts"
        for card in cards:
            filename = self.get_clean_filename(card, is_back=backs_pdf)
            if filename not in seen_keys:
                unique_cards_to_download.append(card)
                seen_keys.add(filename)
        
        self.log(f"{desc_text}: Checking {len(unique_cards_to_download)} images...")
        
        total = len(unique_cards_to_download)
        completed = 0
        if total == 0: return

        def download_task(card):
            url = self.get_card_image_url(card, face="back" if backs_pdf else "front")
            return self.download_image(url, card=card, image_dir=image_dir, is_back=backs_pdf)
            
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(download_task, card) for card in unique_cards_to_download]
            for _ in as_completed(futures):
                completed += 1
                if completed % 5 == 0 or completed == total:
                    self.log(f"{desc_text}: {completed}/{total}")

    def resize_default_back(self, image_path):
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
            self.log(f"Warning: Could not resize back image ({e}). Using original.")
            with open(image_path, 'rb') as f:
                return f.read()

    def draw_cut_lines(self, c, x_start, y_start_offset, spacing_x, spacing_y, color, thickness_mm):
        thickness_pt = thickness_mm * MM_TO_PT
        c.setLineWidth(thickness_pt)
        c.setStrokeColor(HexColor(color))
        
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

    def generate_pdf(self, cards, output_dir, filename_base, footer_text=None, image_dir="", padding=0, double_sided=False, default_back_image_bytes=None, cut_line_color="#000000", cut_line_thickness_mm=0.2):
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
        self.parallel_download(cards, image_dir, backs_pdf=False)
        if double_sided: self.parallel_download(cards, image_dir, backs_pdf=True)
        
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
        self.log(f"Building PDF: {os.path.basename(output_path)}...")
        
        if double_sided:
            total_pages = ((len(cards) + GRID_COLS * GRID_ROWS - 1) // (GRID_COLS * GRID_ROWS)) * 2
            item_index = 0
            while item_index < len(cards):
                page_num_base = (item_index // (GRID_COLS * GRID_ROWS)) + 1
                self.log(f"Generating page {page_num_base * 2 - 1}/{total_pages} (Fronts)...")
                # Front
                self.draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, cut_line_color, cut_line_thickness_mm)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and item_index < len(cards):
                    row = placed // GRID_COLS
                    col = placed % GRID_COLS
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    card = cards[item_index]
                    filename = self.get_clean_filename(card, is_back=False)
                    local_path = os.path.join(image_dir, filename)
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
                c.setFillColor(HexColor(cut_line_color))
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2 - 1} / {total_pages}")
                c.showPage()
                
                # Back
                self.log(f"Generating page {page_num_base * 2}/{total_pages} (Backs)...")
                self.draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, cut_line_color, cut_line_thickness_mm)
                back_start = item_index - placed
                col_order = range(GRID_COLS - 1, -1, -1)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and back_start + placed < len(cards):
                    row = placed // GRID_COLS
                    col = col_order[placed % GRID_COLS]
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    card = cards[back_start + placed]
                    back_filename = self.get_clean_filename(card, is_back=True)
                    back_local_path = os.path.join(image_dir, back_filename)
                    if not os.path.exists(back_local_path) and not default_back_image_bytes:
                         pass # Should have been downloaded in parallel_download if exists
                    
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
                c.setFillColor(HexColor(cut_line_color))
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text + " (Backs)")
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2} / {total_pages}")
                c.showPage()
        else:
            # Single Sided
            total_items = len(cards)
            total_pages = (total_items + GRID_COLS * GRID_ROWS - 1) // (GRID_COLS * GRID_ROWS)
            item_index = 0
            while item_index < total_items:
                page_num = (item_index // (GRID_COLS * GRID_ROWS)) + 1
                self.log(f"Generating page {page_num}/{total_pages}...")
                self.draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, cut_line_color, cut_line_thickness_mm)
                for row in range(GRID_ROWS):
                    for col in range(GRID_COLS):
                        if item_index >= total_items: break
                        x = x_start + col * (CARD_WIDTH + spacing_x)
                        y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                        card = cards[item_index]
                        filename = self.get_clean_filename(card, is_back=False)
                        local_path = os.path.join(image_dir, filename)
                        if os.path.exists(local_path):
                            img_reader = ImageReader(local_path)
                            c.saveState()
                            clip_path = c.beginPath()
                            clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, CORNER_RADIUS)
                            c.clipPath(clip_path, stroke=0, fill=0)
                            c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                            c.restoreState()
                        item_index += 1
                c.setFillColor(HexColor(cut_line_color))
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num} / {total_pages}")
                c.showPage()
        c.save()
        with open(output_path, 'wb') as f:
            f.write(buffer.getvalue())
        buffer.close()
        return output_path

    def run_job(self, input_str, output_dir, format_mode, padding_mm=0.0, include_maybeboard=False, include_sideboard=False, default_back_image=None, cut_line_color="#000000", cut_line_thickness=0.2):
        self.log(f"Starting job...")
        central_image_dir = os.path.abspath("card_images") # Central cache
        os.makedirs(central_image_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        
        padding_pt = padding_mm * MM_TO_PT
        default_back_bytes = self.resize_default_back(default_back_image) if default_back_image else None
        
        # Parsed returns list of (cards, metadata)
        decks_to_process = self.parse_input(input_str, include_maybeboard, include_sideboard)
        
        if not decks_to_process:
            self.log("No valid decks found to process.")
            return []
            
        generated_files = []
        
        for i, (cards, metadata) in enumerate(decks_to_process):
            resolved_deckname = metadata['name'] if metadata['name'] else f"Deck_{i+1}"
            author_name = metadata['author']
            footer_text = f"{resolved_deckname} - {author_name}" if author_name else resolved_deckname
            
            self.log(f"Processing deck: {resolved_deckname}")
            deck_folder = os.path.join(output_dir, resolved_deckname.replace(' ', '_').replace('/', '_'))
            os.makedirs(deck_folder, exist_ok=True)
            
            if format_mode == 'smart':
                sfc_cards = []
                dfc_cards = []
                # Pre-check existence for smart split using central_cache info if possible, otherwise treat as SFC until we know
                # For robustness in batch, we check DFC status by downloading backs 
                self.parallel_download(cards, central_image_dir, backs_pdf=True) 
                
                for card in cards:
                    back_filename = self.get_clean_filename(card, is_back=True)
                    if os.path.exists(os.path.join(central_image_dir, back_filename)): dfc_cards.append(card)
                    else: sfc_cards.append(card)
                
                if sfc_cards:
                    path = self.generate_pdf(sfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_Standard", footer_text, central_image_dir, padding_pt, False, None, cut_line_color, cut_line_thickness)
                    if path: generated_files.append(path)
                if dfc_cards:
                    path = self.generate_pdf(dfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_DoubleSided", footer_text + " (DFC)", central_image_dir, padding_pt, True, default_back_bytes, cut_line_color, cut_line_thickness)
                    if path: generated_files.append(path)
                    
            else:
                modes_to_run = []
                if format_mode == 'single': modes_to_run.append(False)
                elif format_mode == 'double': modes_to_run.append(True)
                elif format_mode == 'both': modes_to_run.append(False); modes_to_run.append(True)
                
                for is_double in modes_to_run:
                    suffix = "DoubleSided" if is_double else "Standard"
                    path = self.generate_pdf(cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, padding_pt, is_double, default_back_bytes, cut_line_color, cut_line_thickness)
                    if path: generated_files.append(path)
                    
        self.log(f"Job complete! Generated {len(generated_files)} files.")
        return generated_files

    def get_deck_structure(self, input_str, format_mode="smart", include_maybeboard=False, include_sideboard=False):
        # Preview Generation
        # Does NOT download high-res images. Uses scryfall small APIs.
        decks = self.parse_input(input_str, include_maybeboard, include_sideboard)
        preview_data = []

        central_image_dir = os.path.abspath("card_images")

        for i, (cards, metadata) in enumerate(decks):
            deck_struct = {
                "name": metadata['name'] or f"Deck {i+1}",
                "author": metadata['author'],
                "batches": []
            }
            
            # Divide into batches (SFC vs DFC or Standard/Double)
            sub_jobs = []
            if format_mode == 'smart':
                sfc, dfc = [], []
                # Quick heuristic: if we have back image locally or scryfall ID suggests DFC?
                # For preview speed, we'll assume SFC unless we can cheaply check. 
                # Let's rely on cached 'back' files if they exist, else treat as SFC for preview (or check Scryfall data if we had it cached)
                # To be accurate without downloading everything: We'll just list all as 'Standard' for preview 
                # UNLESS we can check quickly. 
                # IMPROVEMENT: Fetching deck from Archidekt already returned 'uid'. We need to know layout. 
                # Currently we don't store layout from Archidekt. 
                # For V2 Preview: We will just show them as one big list if 'smart' is tricky without data.
                # BUT user wants to see "which mode is selected". 
                # Let's try to detect DFC if we have local files. If not, they show as SFC.
                for card in cards:
                     back_filename = self.get_clean_filename(card, is_back=True)
                     if os.path.exists(os.path.join(central_image_dir, back_filename)): dfc.append(card)
                     else: sfc.append(card)
                
                if sfc: sub_jobs.append(("Standard", sfc, False))
                if dfc: sub_jobs.append(("Double Sided (DFC)", dfc, True))
            elif format_mode == 'double':
                sub_jobs.append(("Double Sided", cards, True))
            else: # single
                sub_jobs.append(("Standard", cards, False))
            
            for label, card_subset, is_double in sub_jobs:
                pages = []
                cards_per_page = GRID_ROWS * GRID_COLS
                
                # Paginate
                for p_idx in range(0, len(card_subset), cards_per_page):
                    chunk = card_subset[p_idx : p_idx + cards_per_page]
                    
                    # Page Front
                    front_grid = [self.get_card_image_url(c, "front", "normal") for c in chunk]
                    pages.append({"type": "front", "cards": front_grid})
                    
                    # Page Back (if double)
                    if is_double:
                        # Backs need to be mirrored column-wise for printing alignment
                        # Chunk slots: 0 1 2 / 3 4 5 ...
                        # Row 0: 0 1 2 -> Backs 2 1 0
                        back_grid = [None] * cards_per_page
                        
                        # Fill back_grid with correct mapping
                        for local_idx, c in enumerate(chunk):
                            row = local_idx // GRID_COLS
                            col = local_idx % GRID_COLS
                            # Mirror col
                            mirror_col = (GRID_COLS - 1) - col
                            mirror_idx = row * GRID_COLS + mirror_col
                            back_grid[mirror_idx] = self.get_card_image_url(c, "back", "normal")
                        
                        # Filter out Nones for the JSON (or keep to maintain grid slots?)
                        # Keep Nones or empty strings to maintain grid structure in frontend
                        clean_back_grid = [u if u else "" for u in back_grid]
                        # Actually, chunk might be smaller than 9. 
                        # We need to handle partial pages. 
                        # If chunk size is 4 (Row 0 full, Row 1 has 1).
                        # Front: [0, 1, 2, 3]
                        # Back:  [2, 1, 0, x, x, 3(mirrored?)] -> 3 is at 1,0 (row 1 col 0). Mirrored is 1,2 (row 1 col 2).
                        # Let's rebuild properly.
                        
                        final_back_grid = [""] * cards_per_page
                        for i, c in enumerate(chunk):
                             r = i // GRID_COLS
                             c_idx = i % GRID_COLS
                             mirror_c = (GRID_COLS - 1) - c_idx
                             dest_idx = r * GRID_COLS + mirror_c
                             final_back_grid[dest_idx] = self.get_card_image_url(c, "back", "normal")
                             
                        pages.append({"type": "back", "cards": final_back_grid})

                deck_struct["batches"].append({
                    "label": label,
                    "pages": pages
                })
            preview_data.append(deck_struct)
            
        return preview_data
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
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.colors import gray, black
from tqdm import tqdm
from collections import defaultdict
from PIL import Image, ImageDraw, ImageChops
import ezdxf
from ezdxf import units
import math

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
PAGE_SIZE_LANDSCAPE = (PAGE_HEIGHT_MM * MM_TO_PT, PAGE_WIDTH_MM * MM_TO_PT)
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

def parse_input(input_str, include_maybeboard=False, include_sideboard=False):
    if input_str.startswith('http'):
        match = re.search(r'/decks/(\d+)', input_str)
        if not match:
            raise ValueError(f"Invalid Archidekt URL: {input_str}")
        deck_id = match.group(1)
        return fetch_archidekt_deck(deck_id, include_maybeboard, include_sideboard)
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

def draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, page_size=PAGE_SIZE, cols=None, rows=None):
    page_w, page_h = page_size
    if cols is None: cols = GRID_COLS
    if rows is None: rows = GRID_ROWS
    c.setLineWidth(CUT_LINE_THICKNESS)
    c.setStrokeColor(black)
    for col in range(cols):
        x_left = x_start + col * (CARD_WIDTH + spacing_x)
        x_right = x_left + CARD_WIDTH
        c.line(x_left, 0, x_left, page_h)
        c.line(x_right, 0, x_right, page_h)
    for row in range(rows):
        y_bottom = page_h - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
        y_top = y_bottom + CARD_HEIGHT
        c.line(0, y_bottom, page_w, y_bottom)
        c.line(0, y_top, page_w, y_top)

def draw_registration_marks_on_page(c, length_mm, thickness_mm, inset_mm, page_size):
    """Draws Silhouette Type 1 Registration Marks"""
    length = length_mm * MM_TO_PT
    thickness = thickness_mm * MM_TO_PT
    inset = inset_mm * MM_TO_PT
    
    # Ensure black fill/stroke for marks
    c.setFillColor(black)
    c.setStrokeColor(black)
    c.setLineWidth(0) # Fill shape

    page_w, page_h = page_size

    # 1. Top-Left: Square
    # Position: Inset from Top & Left
    # Square size: 5mm fixed
    square_size = 5 * mm 
    
    # Top-Left Square
    c.rect(inset, page_h - inset - square_size, square_size, square_size, fill=1, stroke=0)

    # Top-Right Bracket
    # Vertical Line
    tr_x = page_w - inset
    tr_y = page_h - inset
    c.rect(tr_x - thickness, tr_y - length, thickness, length, fill=1, stroke=0)
    # Horizontal Line
    c.rect(tr_x - length, tr_y - thickness, length, thickness, fill=1, stroke=0)

    # Bottom-Left Bracket
    # Vertical Line
    bl_x = inset
    bl_y = inset
    c.rect(bl_x, bl_y, thickness, length, fill=1, stroke=0)
    c.rect(bl_x, bl_y, thickness, length, fill=1, stroke=0)
    # Horizontal Line
    c.rect(bl_x, bl_y, length, thickness, fill=1, stroke=0)

def draw_visible_cut_lines(c, card_positions, radius):
    """Draws visible cut lines (rounded rects) around cards for manual trimming backup."""
    c.setStrokeColor(black)
    c.setLineWidth(0.5) # Thin line
    c.setFillColor(colors.transparent)
    
    for (x, y, w, h) in card_positions:
        c.roundRect(x, y, w, h, radius, stroke=1, fill=0)

def generate_template_based_pdf(cards, output_dir, filename_base, footer_text=None, image_dir="", extend_border="auto"):
    """
    Generates a PDF using the silhouette-card-maker template approach.
    Uses a pre-made registration mark image as a base and overlays cards at fixed positions.
    Includes 'Edge Extension' (print bleed) by smearing the outermost pixels.
    'extend_border' can be an integer (pixels) or 'auto' to fill gutters.
    """
    if not cards:
        return None
    
    # Fixed layout from silhouette-card-maker layouts.json for "standard" cards on "letter" paper
    X_POSITIONS = [140, 899, 1658, 2417]
    Y_POSITIONS = [231, 1280]
    
    CARD_WIDTH_PX = 743
    CARD_HEIGHT_PX = 1038
    PPI = 300
    
    COLS = len(X_POSITIONS)
    ROWS = len(Y_POSITIONS)
    CARDS_PER_PAGE = COLS * ROWS

    # Calculate automatic bleed to fill gutters
    # Gutter X = Space between card columns. Each card gets half of the gutter as bleed.
    # We use math.ceil to ensure we overlap by 1px if the gutter is odd, avoiding white lines.
    gutter_x = (X_POSITIONS[1] - X_POSITIONS[0]) - CARD_WIDTH_PX if COLS > 1 else 10
    gutter_y = (Y_POSITIONS[1] - Y_POSITIONS[0]) - CARD_HEIGHT_PX if ROWS > 1 else 10
    
    OUTER_BLEED = 20 # Extra bleed for the outside of the grid
    
    if extend_border == "auto" or extend_border == -1:
        bleed_x = math.ceil(gutter_x / 2)
        bleed_y = math.ceil(gutter_y / 2)
    else:
        try:
            val = int(extend_border)
            bleed_x = val
            bleed_y = val
        except:
            bleed_x = 5
            bleed_y = 5

    reg_mark_path = os.path.join(os.path.dirname(__file__), 'silhouette_templates', 'letter_registration_3.jpg')
    if not os.path.exists(reg_mark_path):
        print(f"Error: Registration mark image not found at {reg_mark_path}")
        return None

    def apply_edge_extension(base_img, card_img, x, y, b_left, b_top, b_right, b_bottom):
        """Smeares the edge pixels of card_img outward onto base_img, scanning inward for non-transparent pixels."""
        w, h = card_img.size
        pixels = card_img.load()
        MAX_SCAN = 80 # Max pixels to search inward
        
        # Hybrid parameters
        EDGE_OFFSET = 0
        EDGE_THRESHOLD = 50
        CORNER_OFFSET = 2
        CORNER_THRESHOLD = 160
        
        DECOR_RADIUS = int(2 * (PPI / 25.4)) # ~24px corner radius
        # Protection zone extends beyond the radius to catch the start of the curve
        PROTECTION_ZONE = DECOR_RADIUS + 20 

        # 1. Prepare Top Edge Strip (Hybrid Sampling with Protection Zones)
        if b_top > 0:
            top_strip = Image.new('RGBA', (w, 1), (0, 0, 0, 0))
            top_strip_pixels = top_strip.load()
            for cx in range(w):
                # Use corner settings if we're near the left or right edge
                is_near_corner = (cx < PROTECTION_ZONE or cx > (w - 1 - PROTECTION_ZONE))
                threshold = CORNER_THRESHOLD if is_near_corner else EDGE_THRESHOLD
                offset = CORNER_OFFSET if is_near_corner else EDGE_OFFSET
                
                for cy in range(MAX_SCAN):
                    if cy < h and pixels[cx, cy][3] > threshold:
                        sample_y = min(cy + offset, h - 1)
                        top_strip_pixels[cx, 0] = pixels[cx, sample_y]
                        break
            for i in range(1, b_top + 1):
                base_img.paste(top_strip, (x, y - i), top_strip)
            
        # 2. Prepare Bottom Edge Strip (Hybrid Sampling with Protection Zones)
        if b_bottom > 0:
            bottom_strip = Image.new('RGBA', (w, 1), (0, 0, 0, 0))
            bottom_strip_pixels = bottom_strip.load()
            for cx in range(w):
                # Use corner settings if we're near the left or right edge
                is_near_corner = (cx < PROTECTION_ZONE or cx > (w - 1 - PROTECTION_ZONE))
                threshold = CORNER_THRESHOLD if is_near_corner else EDGE_THRESHOLD
                offset = CORNER_OFFSET if is_near_corner else EDGE_OFFSET
                
                for cy in range(MAX_SCAN):
                    curr_y = h - 1 - cy
                    if curr_y >= 0 and pixels[cx, curr_y][3] > threshold:
                        sample_y = max(curr_y - offset, 0)
                        bottom_strip_pixels[cx, 0] = pixels[cx, sample_y]
                        break
            for i in range(1, b_bottom + 1):
                base_img.paste(bottom_strip, (x, y + h + i - 1), bottom_strip)
            
        # 3. Prepare Left Edge Strip (Hybrid Sampling with Protection Zones)
        if b_left > 0:
            left_strip = Image.new('RGBA', (1, h), (0, 0, 0, 0))
            left_strip_pixels = left_strip.load()
            for cy in range(h):
                # Use corner settings if we're near the top or bottom edge
                is_near_corner = (cy < PROTECTION_ZONE or cy > (h - 1 - PROTECTION_ZONE))
                threshold = CORNER_THRESHOLD if is_near_corner else EDGE_THRESHOLD
                offset = CORNER_OFFSET if is_near_corner else EDGE_OFFSET
                
                for cx in range(MAX_SCAN):
                    if cx < w and pixels[cx, cy][3] > threshold:
                        sample_x = min(cx + offset, w - 1)
                        left_strip_pixels[0, cy] = pixels[sample_x, cy]
                        break
            for i in range(1, b_left + 1):
                base_img.paste(left_strip, (x - i, y), left_strip)
            
        # 4. Prepare Right Edge Strip (Hybrid Sampling with Protection Zones)
        if b_right > 0:
            right_strip = Image.new('RGBA', (1, h), (0, 0, 0, 0))
            right_strip_pixels = right_strip.load()
            for cy in range(h):
                # Use corner settings if we're near the top or bottom edge
                is_near_corner = (cy < PROTECTION_ZONE or cy > (h - 1 - PROTECTION_ZONE))
                threshold = CORNER_THRESHOLD if is_near_corner else EDGE_THRESHOLD
                offset = CORNER_OFFSET if is_near_corner else EDGE_OFFSET
                
                for cx in range(MAX_SCAN):
                    curr_x = w - 1 - cx
                    if curr_x >= 0 and pixels[curr_x, cy][3] > threshold:
                        sample_x = max(curr_x - offset, 0)
                        right_strip_pixels[0, cy] = pixels[sample_x, cy]
                        break
            for i in range(1, b_right + 1):
                base_img.paste(right_strip, (x + w + i - 1, y), right_strip)
            
        # 5. Under-Pasting for Corners (Shallow Diagonal Sampling)
        def get_corner_px(start_x, start_y, dx, dy):
            for i in range(MAX_SCAN):
                cx, cy = start_x + (i * dx), start_y + (i * dy)
                if 0 <= cx < w and 0 <= cy < h:
                    if pixels[cx, cy][3] > CORNER_THRESHOLD:
                        # For corners, we go deeper to avoid fuzzy pixels
                        scx, scy = cx + (CORNER_OFFSET * dx), cy + (CORNER_OFFSET * dy)
                        scx, scy = max(0, min(scx, w-1)), max(0, min(scy, h-1))
                        return pixels[scx, scy]
            return (0, 0, 0, 0)

        tl_pixel = get_corner_px(0, 0, 1, 1)
        tr_pixel = get_corner_px(w-1, 0, -1, 1)
        bl_pixel = get_corner_px(0, h-1, 1, -1)
        br_pixel = get_corner_px(w-1, h-1, -1, -1)
        
        # Fill corner areas including UNDER where the rounded corners sit
        # Square sizes depend on the specific side bleeds
        if b_left > 0 or b_top > 0:
            tl_box = Image.new('RGBA', (b_left + DECOR_RADIUS, b_top + DECOR_RADIUS), tl_pixel)
            base_img.paste(tl_box, (x - b_left, y - b_top), tl_box)

        if b_right > 0 or b_top > 0:
            tr_box = Image.new('RGBA', (b_right + DECOR_RADIUS, b_top + DECOR_RADIUS), tr_pixel)
            base_img.paste(tr_box, (x + w - DECOR_RADIUS, y - b_top), tr_box)

        if b_left > 0 or b_bottom > 0:
            bl_box = Image.new('RGBA', (b_left + DECOR_RADIUS, b_bottom + DECOR_RADIUS), bl_pixel)
            base_img.paste(bl_box, (x - b_left, y + h - DECOR_RADIUS), bl_box)

        if b_right > 0 or b_bottom > 0:
            br_box = Image.new('RGBA', (b_right + DECOR_RADIUS, b_bottom + DECOR_RADIUS), br_pixel)
            base_img.paste(br_box, (x + w - DECOR_RADIUS, y + h - DECOR_RADIUS), br_box)


    pages = []
    total_cards = len(cards)
    total_pages = (total_cards + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE
    
    print(f"Building template-based PDF with Edge Extension ({bleed_x}x{bleed_y}px): {filename_base}.pdf...")
    
    with Image.open(reg_mark_path) as reg_im:
        with tqdm(total=total_cards, desc="Placing cards", unit="card") as pbar:
            for page_num in range(total_pages):
                page_img = reg_im.copy()
                if page_img.mode != 'RGBA':
                    page_img = page_img.convert('RGBA')
                    
                start_idx = page_num * CARDS_PER_PAGE
                end_idx = min(start_idx + CARDS_PER_PAGE, total_cards)
                page_cards = cards[start_idx:end_idx]
                
                card_idx = 0
                for row in range(ROWS):
                    for col in range(COLS):
                        if card_idx >= len(page_cards):
                            break
                        
                        card = page_cards[card_idx]
                        filename = get_clean_filename(card, is_back=False)
                        local_path = os.path.join(image_dir, filename)
                        
                        if not os.path.exists(local_path):
                            download_image(get_card_image_url(card, "front"), card, image_dir, False)
                        
                        if os.path.exists(local_path):
                            with Image.open(local_path) as card_img:
                                card_img_resized = card_img.resize((CARD_WIDTH_PX, CARD_HEIGHT_PX), Image.Resampling.LANCZOS).convert('RGBA')
                                
                                # Paste onto page at fixed position
                                x_pos = X_POSITIONS[col]
                                y_pos = Y_POSITIONS[row]
                                
                                # 1. Apply Edge Extension (smear outward)
                                if bleed_x > 0 or bleed_y > 0:
                                    # Calculate dynamic bleed for this card's position in the grid
                                    b_left = OUTER_BLEED if col == 0 else bleed_x
                                    b_top = OUTER_BLEED if row == 0 else bleed_y
                                    b_right = OUTER_BLEED if col == (COLS - 1) else bleed_x
                                    b_bottom = OUTER_BLEED if row == (ROWS - 1) else bleed_y
                                    
                                    apply_edge_extension(page_img, card_img_resized, x_pos, y_pos, 
                                                         b_left, b_top, b_right, b_bottom)
                                
                                # 2. Prepare card with rounded corners
                                # Create a smooth anti-aliased mask by drawing at 4x scale
                                mask_4x = Image.new('L', (CARD_WIDTH_PX * 4, CARD_HEIGHT_PX * 4), 0)
                                draw_4x = ImageDraw.Draw(mask_4x)
                                draw_4x.rounded_rectangle([(0, 0), (CARD_WIDTH_PX * 4, CARD_HEIGHT_PX * 4)], 
                                                          radius=int(2 * (PPI / 25.4)) * 4, fill=255)
                                mask = mask_4x.resize((CARD_WIDTH_PX, CARD_HEIGHT_PX), Image.Resampling.LANCZOS)
                                
                                # Combine mask with card's native alpha to ensure smooth blending
                                if card_img_resized.mode == 'RGBA':
                                    card_alpha = card_img_resized.getchannel('A')
                                    mask = ImageChops.multiply(card_alpha, mask)
                                
                                card_final = card_img_resized.copy()
                                card_final.putalpha(mask)
                                
                                # 3. Paste card onto page (properly blends semi-transparent fuzzy edges)
                                page_img.paste(card_final, (x_pos, y_pos), card_final)
                        
                        card_idx += 1
                        pbar.update(1)
                    if card_idx >= len(page_cards):
                        break
                
                # Convert back to RGB
                rgb_img = Image.new('RGB', page_img.size, (255, 255, 255))
                rgb_img.paste(page_img, mask=page_img.split()[3])
                pages.append(rgb_img)
    
    # Save as PDF


    output_path = os.path.join(output_dir, f"{filename_base}.pdf")
    if pages:
        pages[0].save(output_path, format='PDF', save_all=True, append_images=pages[1:], 
                     resolution=PPI, quality=95)
        print(f"Done! PDF saved: {output_path}")
        print(f"  → Use with: silhouette_templates/letter_standard_v4.studio3")
        return output_path
    
    return None


def generate_dxf_cut_file(output_path, cards, safe_rect, card_positions, reg_marks_cfg, page_size):
    """Generates a DXF file for Silhouette Studio."""
    # Convert points to mm for DXF (Silhouette works in mm)
    PT_TO_MM = 0.352778
    
    w_pt, h_pt = page_size
    w_mm = w_pt * PT_TO_MM
    h_mm = h_pt * PT_TO_MM
    
    # Create a new DXF document
    doc = ezdxf.new('R2010')
    doc.units = units.MM
    msp = doc.modelspace()
    
    # Helper function to convert PDF Y coordinate (bottom-up) to DXF Y coordinate (bottom-up, same)
    def pt_to_mm_coord(x_pt, y_pt):
        return (x_pt * PT_TO_MM, y_pt * PT_TO_MM)
    
    # 1. Registration Marks (Black, Filled rectangles)
    l_mm = reg_marks_cfg['length']
    t_mm = reg_marks_cfg['thickness']
    i_mm = reg_marks_cfg['inset']
    square_size_mm = 5
    
    # TL Square
    tl_x, tl_y = pt_to_mm_coord(reg_marks_cfg['inset'] * mm, h_pt - (reg_marks_cfg['inset'] + square_size_mm) * mm)
    msp.add_lwpolyline([
        (i_mm, h_mm - i_mm - square_size_mm),
        (i_mm + square_size_mm, h_mm - i_mm - square_size_mm),
        (i_mm + square_size_mm, h_mm - i_mm),
        (i_mm, h_mm - i_mm),
    ], close=True, dxfattribs={'layer': 'Registration', 'color': 0})  # Black
    
    # TR Bracket - Vertical line
    msp.add_lwpolyline([
        (w_mm - i_mm - t_mm, h_mm - i_mm - l_mm),
        (w_mm - i_mm, h_mm - i_mm - l_mm),
        (w_mm - i_mm, h_mm - i_mm),
        (w_mm - i_mm - t_mm, h_mm - i_mm),
    ], close=True, dxfattribs={'layer': 'Registration', 'color': 0})
    
    # TR Bracket - Horizontal line
    msp.add_lwpolyline([
        (w_mm - i_mm - l_mm, h_mm - i_mm - t_mm),
        (w_mm - i_mm, h_mm - i_mm - t_mm),
        (w_mm - i_mm, h_mm - i_mm),
        (w_mm - i_mm - l_mm, h_mm - i_mm),
    ], close=True, dxfattribs={'layer': 'Registration', 'color': 0})
    
    # BL Bracket - Vertical line
    msp.add_lwpolyline([
        (i_mm, i_mm),
        (i_mm + t_mm, i_mm),
        (i_mm + t_mm, i_mm + l_mm),
        (i_mm, i_mm + l_mm),
    ], close=True, dxfattribs={'layer': 'Registration', 'color': 0})
    
    # BL Bracket - Horizontal line  
    msp.add_lwpolyline([
        (i_mm, i_mm),
        (i_mm + l_mm, i_mm),
        (i_mm + l_mm, i_mm + t_mm),
        (i_mm, i_mm + t_mm),
    ], close=True, dxfattribs={'layer': 'Registration', 'color': 0})
    
    # 2. Cut Lines (Red, rounded rectangles for cards)
    radius_mm = 2  # 2mm radius for rounded corners
    
    for (cx_pt, cy_pt, cw_pt, ch_pt) in card_positions:
        cx_mm, cy_mm = pt_to_mm_coord(cx_pt, cy_pt)
        cw_mm = cw_pt * PT_TO_MM
        ch_mm = ch_pt * PT_TO_MM
        
        # Create rounded rectangle using polyline with arcs
        # We'll approximate with a polyline with many segments for the curves
        points = []
        segments = 16  # Number of segments per corner arc
        
        # Bottom-left corner arc
        for i in range(segments + 1):
            angle = math.pi + i * (math.pi / 2) / segments
            px = cx_mm + radius_mm + radius_mm * math.cos(angle)
            py = cy_mm + radius_mm + radius_mm * math.sin(angle)
            points.append((px, py))
        
        # Bottom-right corner arc
        for i in range(segments + 1):
            angle = 3 * math.pi / 2 + i * (math.pi / 2) / segments
            px = cx_mm + cw_mm - radius_mm + radius_mm * math.cos(angle)
            py = cy_mm + radius_mm + radius_mm * math.sin(angle)
            points.append((px, py))
        
        # Top-right corner arc
        for i in range(segments + 1):
            angle = i * (math.pi / 2) / segments
            px = cx_mm + cw_mm - radius_mm + radius_mm * math.cos(angle)
            py = cy_mm + ch_mm - radius_mm + radius_mm * math.sin(angle)
            points.append((px, py))
        
        # Top-left corner arc
        for i in range(segments + 1):
            angle = math.pi / 2 + i * (math.pi / 2) / segments
            px = cx_mm + radius_mm + radius_mm * math.cos(angle)
            py = cy_mm + ch_mm - radius_mm + radius_mm * math.sin(angle)
            points.append((px, py))
        
        msp.add_lwpolyline(points, close=True, dxfattribs={'layer': 'CutLines', 'color': 1})  # Red
    
    # Save the DXF file
    doc.saveas(output_path)

def generate_pdf(cards, output_dir, filename_base, footer_text=None, image_dir="", padding=0, double_sided=False, default_back_image_bytes=None, silhouette_cfg=None, orientation='portrait'):
    if not cards: return None
    from reportlab import rl_config
    rl_config.pageCompression = 1
    output_path = os.path.join(output_dir, f"{filename_base}.pdf")
    final_footer_text = footer_text if footer_text else filename_base.replace('_', ' ')
    
    current_page_size = PAGE_SIZE_LANDSCAPE if orientation == 'landscape' else PAGE_SIZE
    page_w, page_h = current_page_size

    # Adjust usable area if Silhouette marks are on
    eff_top_margin = TOP_MARGIN
    eff_bottom_margin = BOTTOM_MARGIN
    eff_left_margin = LEFT_MARGIN
    eff_right_margin = RIGHT_MARGIN
    
    if silhouette_cfg:
        # User requested relaxed margins. We will enforce 'inset' as the hard margin,
        # but NOT automatically add 'length' to the margin.
        safe_inset = silhouette_cfg['inset'] * MM_TO_PT
        eff_top_margin = max(TOP_MARGIN, safe_inset)
        eff_bottom_margin = max(BOTTOM_MARGIN, safe_inset)
        eff_left_margin = max(LEFT_MARGIN, safe_inset)
        eff_right_margin = max(RIGHT_MARGIN, safe_inset)

    usable_width = page_w - eff_left_margin - eff_right_margin
    usable_height = page_h - eff_top_margin - eff_bottom_margin
    
    # Recalculate Grid based on new usable area
    # Max cols
    spacing_x = padding
    spacing_y = padding
    
    # Start high and reduce to fit
    current_cols = 5
    current_rows = 5
    
    while current_cols > 0:
        req_w = current_cols * CARD_WIDTH + (current_cols - 1) * spacing_x
        if req_w <= usable_width: break
        current_cols -= 1
        
    while current_rows > 0:
        req_h = current_rows * CARD_HEIGHT + (current_rows - 1) * spacing_y
        if req_h <= usable_height: break
        current_rows -= 1
        
    if current_cols == 0 or current_rows == 0:
        print("Error: Margins too large, cannot fit any cards!")
        return None
        
    grid_width = current_cols * CARD_WIDTH + (current_cols - 1) * spacing_x
    grid_height = current_rows * CARD_HEIGHT + (current_rows - 1) * spacing_y
    x_start = eff_left_margin + (usable_width - grid_width) / 2
    y_start_offset = (usable_height - grid_height) / 2
    
    # Footer pos
    # If Silhouette, move to Bottom-Right corner (safe zone from marks)
    # Standard Type 1 Marks: TL (Square), TR (Bracket), BL (Bracket). BR is empty.
    if silhouette_cfg:
        # Align Right, bottom corner buffer
        # User said it was too low. Lifting it up.
        # Safe inset is around 12-15mm.
        footer_y = 20 # Lifted from 10
        # Right align
        right_footer_x = page_w - (silhouette_cfg['inset'] * MM_TO_PT)
        left_footer_x = -999 # Hide left footer
    else:
        footer_y = page_h - eff_top_margin - y_start_offset - grid_height - FOOTER_BELOW_GRID
        left_footer_x = x_start + FOOTER_INSET
        right_footer_x = x_start + grid_width - FOOTER_INSET

    # 1. Ensure Images
    parallel_download(cards, image_dir, backs_pdf=False)
    if double_sided: parallel_download(cards, image_dir, backs_pdf=True)
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=current_page_size)
    print(f"Building PDF: {os.path.basename(output_path)} (Grid: {current_cols}x{current_rows})...")
    
    cards_per_page = current_cols * current_rows
    
    if double_sided:
        total_pages = ((len(cards) + cards_per_page - 1) // cards_per_page) * 2
        item_index = 0
        page_idx = 0
        with tqdm(total=len(cards) * 2, desc="Placing cards", unit="card", leave=False) as pbar:
            while item_index < len(cards):
                page_num_base = (item_index // cards_per_page) + 1
                page_card_positions = [] # For SVG

                # Front
                if silhouette_cfg:
                    draw_registration_marks_on_page(c, silhouette_cfg['length'], silhouette_cfg['thickness'], silhouette_cfg['inset'], current_page_size)
                    # Also draw visible cut lines (rounded) for manual trimming backup
                    draw_visible_cut_lines(c, page_card_positions, 2*mm)
                else:
                    draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, current_page_size)
                
                placed = 0
                start_index_for_page = item_index
                while placed < cards_per_page and item_index < len(cards):
                    row = placed // current_cols
                    col = placed % current_cols
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = page_h - eff_top_margin - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    page_card_positions.append((x, y, CARD_WIDTH, CARD_HEIGHT))
                    
                    card = cards[item_index]
                    filename = get_clean_filename(card, is_back=False)
                    local_path = os.path.join(image_dir, filename)
                    if not os.path.exists(local_path):
                        download_image(get_card_image_url(card, "front"), card, image_dir, False)
                    if os.path.exists(local_path):
                        c.saveState()
                        clip_path = c.beginPath()
                        radius = 2 * mm if silhouette_cfg else CORNER_RADIUS
                        clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, radius)
                        c.clipPath(clip_path, stroke=0, fill=0)
                        c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                        c.restoreState()
                    placed += 1
                    item_index += 1
                    pbar.update(1)
                
                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                if not silhouette_cfg:
                    c.drawString(left_footer_x, footer_y, final_footer_text)
                    c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2 - 1} / {total_pages}")
                else:
                    # Combined footer at Bottom Right
                    c.drawRightString(right_footer_x, footer_y, f"{final_footer_text} [{page_num_base * 2 - 1} / {total_pages}]")
                c.showPage()
                
                # DXF Generation for this page (Fronts imply the cut lines)
                if silhouette_cfg:
                    dxf_path = os.path.join(output_dir, f"{filename_base}_Page{page_num_base}.dxf")
                    generate_dxf_cut_file(dxf_path, cards[start_index_for_page:item_index], (x_start, y_start_offset, grid_width, grid_height), page_card_positions, silhouette_cfg, current_page_size)

                # Back
                if silhouette_cfg:
                    draw_registration_marks_on_page(c, silhouette_cfg['length'], silhouette_cfg['thickness'], silhouette_cfg['inset'], current_page_size)
                else:
                    draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, current_page_size)

                # Backs need to be mirrored column-wise for duplex printing alignment
                # If we have [1, 2, 3] on front (L->R), back should be [3, 2, 1] (L->R) so 1 is behind 1.
                # 'placed' is how many cards were on the front page.
                
                # Re-calculate positions for backs to ensure they align
                # The 'col' logic needs to be inverted.
                
                placed_back = 0
                for i in range(placed):
                    # We process cards in same order as front (card 1, card 2...)
                    # BUT their position on page must switch col index.
                    
                    # Original pos index (0 to placed-1)
                    # We want card at index 0 to be at Last Col
                    
                    row = i // current_cols
                    orig_col = i % current_cols
                    # Mirror column
                    col = (current_cols - 1) - orig_col
                    
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = page_h - eff_top_margin - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    
                    card_idx_in_batch = start_index_for_page + i
                    card = cards[card_idx_in_batch]
                    
                    back_filename = get_clean_filename(card, is_back=True)
                    back_local_path = os.path.join(image_dir, back_filename)
                    if not os.path.exists(back_local_path) and not default_back_image_bytes:
                        download_image(get_card_image_url(card, "back"), card, image_dir, True)
                    img_data = None
                    if os.path.exists(back_local_path): img_data = back_local_path
                    elif default_back_image_bytes: img_data = BytesIO(default_back_image_bytes)
                    if img_data:
                        c.saveState()
                        clip_path = c.beginPath()
                        radius = 2 * mm if silhouette_cfg else CORNER_RADIUS
                        clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, radius)
                        c.clipPath(clip_path, stroke=0, fill=0)
                        c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                        c.restoreState()
                    pbar.update(1)

                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                c.drawString(left_footer_x, footer_y, final_footer_text + " (Backs)")
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2} / {total_pages}")
                c.showPage()
    else:
        # Single Sided
        total_items = len(cards)
        total_pages = (total_items + cards_per_page - 1) // cards_per_page
        item_index = 0
        with tqdm(total=total_items, desc="Placing cards", unit="item", leave=False) as pbar:
            while item_index < total_items:
                page_num = (item_index // cards_per_page) + 1
                page_card_positions = []
                
                if silhouette_cfg:
                    c.saveState()
                    draw_registration_marks_on_page(c, silhouette_cfg['length'], silhouette_cfg['thickness'], silhouette_cfg['inset'], current_page_size)
                    c.restoreState()
                else:
                    draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y, current_page_size)
                
                start_index_for_page = item_index
                for row in range(current_rows):
                    for col in range(current_cols):
                        if item_index >= total_items: break
                        x = x_start + col * (CARD_WIDTH + spacing_x)
                        y = page_h - eff_top_margin - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                        page_card_positions.append((x, y, CARD_WIDTH, CARD_HEIGHT))
                        
                        card = cards[item_index]
                        filename = get_clean_filename(card, is_back=False)
                        local_path = os.path.join(image_dir, filename)
                        if not os.path.exists(local_path):
                            download_image(get_card_image_url(card, "front"), card, image_dir, False)
                        if os.path.exists(local_path):
                            img_reader = ImageReader(local_path)
                            c.saveState()
                            clip_path = c.beginPath()
                            radius = 2 * mm if silhouette_cfg else CORNER_RADIUS
                            clip_path.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, radius)
                            c.clipPath(clip_path, stroke=0, fill=0)
                            c.drawImage(img_reader, x, y, width=CARD_WIDTH, height=CARD_HEIGHT, preserveAspectRatio=True, mask='auto')
                            c.restoreState()
                        item_index += 1
                        pbar.update(1)
                
                c.setFillColor(gray)
                c.setFont(FOOTER_FONT, FOOTER_SIZE)
                if not silhouette_cfg:
                    c.drawString(left_footer_x, footer_y, final_footer_text)
                    c.drawRightString(right_footer_x, footer_y, f"{page_num} / {total_pages}")
                if silhouette_cfg:
                    c.drawRightString(right_footer_x, footer_y, f"{final_footer_text} [{page_num} / {total_pages}]")
                c.showPage()
                
    # Generate ONE Single Cut File at the end if Silhouette is enabled
    if silhouette_cfg:
        dxf_path = os.path.join(output_dir, f"{filename_base}_CutFile.dxf")
        # We need a sample set of cards to generate the grid. 
        # Since the grid is constant, we can just use the first 'cards_per_page' cards
        # or dummy data, but generate_dxf_cut_file uses card count to determining grid?
        # No, it uses 'cards' list and positions.
        
        # We need to regenerate the positions for a FULL page.
        # We can simulate a full page.
        dummy_cards = cards[:cards_per_page]
        dummy_positions = []
        for row in range(current_rows):
            for col in range(current_cols):
                 if len(dummy_positions) >= len(dummy_cards): break
                 x = x_start + col * (CARD_WIDTH + spacing_x)
                 y = page_h - eff_top_margin - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                 dummy_positions.append((x, y, CARD_WIDTH, CARD_HEIGHT))
        
        generate_dxf_cut_file(dxf_path, dummy_cards, (x_start, y_start_offset, grid_width, grid_height), dummy_positions, silhouette_cfg, current_page_size)

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
    cards, metadata = parse_input(args.input, args.include_maybeboard, args.include_sideboard)
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
            if args.silhouette:
                generate_template_based_pdf(sfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_Standard", footer_text, central_image_dir, extend_border=args.extend_border)
            else:
                generate_pdf(sfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_Standard", footer_text, central_image_dir, padding_pt, False, None, args.silhouette_cfg, args.orientation)
        if dfc_cards:
            if args.silhouette:
                # Note: Currently template approach is optimized for single-sided. 
                # For DFC we might still want to use the old method or a separate template.
                # For now, we'll use the template method for fronts.
                generate_template_based_pdf(dfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_DoubleSided", footer_text + " (DFC)", central_image_dir, extend_border=args.extend_border)
            else:
                generate_pdf(dfc_cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_DoubleSided", footer_text + " (DFC)", central_image_dir, padding_pt, True, default_back_bytes, args.silhouette_cfg, args.orientation)
    else:
        modes_to_run = []
        if args.format == 'single': modes_to_run.append(False)
        elif args.format == 'double': modes_to_run.append(True)
        elif args.format == 'both': modes_to_run.append(False); modes_to_run.append(True)
        for is_double in modes_to_run:
            suffix = "DoubleSided" if is_double else "Standard"
            if args.silhouette:
                generate_template_based_pdf(cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, extend_border=args.extend_border)
            else:
                generate_pdf(cards, deck_folder, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, padding_pt, is_double, default_back_bytes, args.silhouette_cfg, args.orientation)
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
                    if args.silhouette:
                        generate_template_based_pdf(sfc_cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_Standard_Cards", footer_text, central_image_dir, extend_border=args.extend_border)
                    else:
                        generate_pdf(sfc_cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_Standard_Cards", footer_text, central_image_dir, padding_pt, False, None, args.silhouette_cfg, args.orientation)
            else:
                modes_to_run = []
                if args.format == 'single': modes_to_run.append(False)
                elif args.format == 'double': modes_to_run.append(True)
                elif args.format == 'both': modes_to_run.append(False); modes_to_run.append(True)
                for is_double in modes_to_run:
                    suffix = "DoubleSided" if is_double else "Standard"
                    if args.silhouette:
                        generate_template_based_pdf(cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, extend_border=args.extend_border)
                    else:
                        generate_pdf(cards, deck_subdir, f"{resolved_deckname.replace(' ', '_')}_{suffix}", footer_text, central_image_dir, padding_pt, is_double, default_back_bytes, args.silhouette_cfg, args.orientation)
        except Exception as e:
            print(f"Error processing deck {url}: {e}")
    # Process Master DFC List (ONLY IF SMART MODE WAS ACTIVE)
    if args.format == 'smart' and master_dfc_list:
        print("\n--- Generating Combined Double-Sided PDF ---")
        if args.silhouette:
            generate_template_based_pdf(master_dfc_list, batch_dir, "Combined_Double_Sided_Cards", "Combined Double-Sided Cards (All Decks)", central_image_dir, extend_border=args.extend_border)
        else:
            generate_pdf(master_dfc_list, batch_dir, "Combined_Double_Sided_Cards", "Combined Double-Sided Cards (All Decks)", central_image_dir, padding_pt, True, default_back_bytes, args.silhouette_cfg, args.orientation)
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
    
    # Silhouette Arguments
    parser.add_argument('--silhouette', action='store_true', help='Enable Silhouette Cameo registration marks and cut file generation.')
    parser.add_argument('--reg_length', type=float, default=20.0, help='Registration mark length in mm (default: 20.0)')
    parser.add_argument('--reg_thickness', type=float, default=0.5, help='Registration mark thickness in mm (default: 0.5)')
    parser.add_argument('--reg_inset', type=float, default=15.0, help='Registration mark inset from edge in mm (default: 15.0)')
    parser.add_argument('--orientation', choices=['portrait', 'landscape'], default='portrait', help='Page orientation (default: portrait). Use landscape for more cards per page.')
    parser.add_argument('--extend_border', default='auto', help='Amount of pixels to extend the card border for print bleed (default: "auto" to fill gutters)')

    args = parser.parse_args()
    
    # Pack silhouette config
    args.silhouette_cfg = None
    if args.silhouette:
        # User requested optimized defaults for Silhouette
        # Defaults: 2x4 Grid (Landscape), 0.8mm padding, 12mm inset, Rounded Corners (handled in logic)
        if args.padding_mm == 0.0: args.padding_mm = 0.0
        
        # Auto-set landscape and 12mm inset if not specified by user to ensure 2x4 fit
        # We only override if they are at defaults.
        # But args.reg_inset default is 15.0. If it's 15.0, we change to 12.0?
        # That might be annoying if they WANT 15.0. 
        # But user asked for "Make the default 2x4 layout".
        # 2x4 requires Landscape + ~12mm inset.
        if args.orientation == 'portrait': # Default was portrait
             args.orientation = 'landscape'
        
        if args.reg_inset == 15.0: # Default check
            args.reg_inset = 12.0
            
        args.silhouette_cfg = {
            'length': args.reg_length,
            'thickness': args.reg_thickness,
            'inset': args.reg_inset
        }

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
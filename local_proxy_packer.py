import os
import argparse
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import gray, black
from tqdm import tqdm

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

def generate_local_pdf(card_pairs, output_path, footer_text, padding_pt=0, double_sided=False):
    if not card_pairs: return
    from reportlab import rl_config
    rl_config.pageCompression = 1

    usable_width = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN
    usable_height = PAGE_SIZE[1] - TOP_MARGIN - BOTTOM_MARGIN
    spacing_x = padding_pt
    spacing_y = padding_pt
    grid_width = GRID_COLS * CARD_WIDTH + (GRID_COLS - 1) * spacing_x
    grid_height = GRID_ROWS * CARD_HEIGHT + (GRID_ROWS - 1) * spacing_y
    x_start = LEFT_MARGIN + (usable_width - grid_width) / 2
    y_start_offset = (usable_height - grid_height) / 2
    footer_y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - grid_height - FOOTER_BELOW_GRID
    left_footer_x = x_start + FOOTER_INSET
    right_footer_x = x_start + grid_width - FOOTER_INSET

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    print(f"Building PDF: {os.path.basename(output_path)}...")

    if double_sided:
        total_pages = ((len(card_pairs) + GRID_COLS * GRID_ROWS - 1) // (GRID_COLS * GRID_ROWS)) * 2
        item_index = 0
        with tqdm(total=len(card_pairs) * 2, desc="Placing cards", unit="card", leave=False) as pbar:
            while item_index < len(card_pairs):
                page_num_base = (item_index // (GRID_COLS * GRID_ROWS)) + 1
                
                # Front
                draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and item_index < len(card_pairs):
                    row = placed // GRID_COLS
                    col = placed % GRID_COLS
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    
                    front_path = card_pairs[item_index][0]
                    if os.path.exists(front_path):
                        img_reader = ImageReader(front_path)
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
                c.drawString(left_footer_x, footer_y, footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2 - 1} / {total_pages}")
                c.showPage()
                
                # Back
                draw_cut_lines(c, x_start, y_start_offset, spacing_x, spacing_y)
                back_start = item_index - placed
                col_order = range(GRID_COLS - 1, -1, -1)
                placed = 0
                while placed < GRID_ROWS * GRID_COLS and back_start + placed < len(card_pairs):
                    row = placed // GRID_COLS
                    col = col_order[placed % GRID_COLS]
                    x = x_start + col * (CARD_WIDTH + spacing_x)
                    y = PAGE_SIZE[1] - TOP_MARGIN - y_start_offset - (row + 1) * CARD_HEIGHT - row * spacing_y
                    
                    back_path = card_pairs[back_start + placed][1]
                    if os.path.exists(back_path):
                        img_reader = ImageReader(back_path)
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
                c.drawString(left_footer_x, footer_y, footer_text + " (Backs)")
                c.drawRightString(right_footer_x, footer_y, f"{page_num_base * 2} / {total_pages}")
                c.showPage()
    else:
        # Single Sided
        total_items = len(card_pairs)
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
                        
                        front_path = card_pairs[item_index][0]
                        if os.path.exists(front_path):
                            img_reader = ImageReader(front_path)
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
                c.drawString(left_footer_x, footer_y, footer_text)
                c.drawRightString(right_footer_x, footer_y, f"{page_num} / {total_pages}")
                c.showPage()

    c.save()
    with open(output_path, 'wb') as f:
        f.write(buffer.getvalue())
    buffer.close()
    return output_path

def main():
    parser = argparse.ArgumentParser(description="Local Folder MTG Proxy Packer")
    parser.add_argument('--input_dir', required=True, help='Path to directory with card images')
    parser.add_argument('--output_dir', default="Output", help='Output directory for PDFs')
    parser.add_argument('--deckname', default="My_Local_Deck", help='Name of the deck/PDF')
    parser.add_argument('--padding_mm', type=float, default=0.0, help='Padding between cards in mm')
    args = parser.parse_args()

    input_dir = args.input_dir
    if not os.path.isdir(input_dir):
        print(f"Error: {input_dir} is not a valid directory.")
        return

    os.makedirs(args.output_dir, exist_ok=True)

    sfc_cards = []
    dfc_cards = []
    
    # Track DFCs to pair them up
    dfc_dict = {}

    valid_extensions = {".png", ".jpg", ".jpeg"}

    for filename in os.listdir(input_dir):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in valid_extensions:
            continue
        
        filepath = os.path.join(input_dir, filename)
        
        if filename.startswith("DFC "):
            # Example: DFC Name 1.png
            # Needs to extract the name and the face (1 or 2)
            name_part = os.path.splitext(filename)[0]
            if name_part.endswith(" 1"):
                base_name = name_part[:-2]
                if base_name not in dfc_dict: dfc_dict[base_name] = [None, None]
                dfc_dict[base_name][0] = filepath
            elif name_part.endswith(" 2"):
                base_name = name_part[:-2]
                if base_name not in dfc_dict: dfc_dict[base_name] = [None, None]
                dfc_dict[base_name][1] = filepath
            else:
                # If it doesn't end with 1 or 2, treat as SFC
                sfc_cards.append((filepath, None))
        else:
            sfc_cards.append((filepath, None))

    for base_name, (front, back) in dfc_dict.items():
        if front and back:
            dfc_cards.append((front, back))
        elif front:
            print(f"Warning: DFC front '{front}' found but no matching back. Treating as SFC.")
            sfc_cards.append((front, None))
        elif back:
            print(f"Warning: DFC back '{back}' found but no matching front. Ignoring.")

    # Sort cards by filename for consistent output
    sfc_cards.sort(key=lambda x: os.path.basename(x[0]))
    dfc_cards.sort(key=lambda x: os.path.basename(x[0]))

    print(f"Found {len(sfc_cards)} Single-Faced Cards.")
    print(f"Found {len(dfc_cards)} Double-Faced Cards.")

    padding_pt = args.padding_mm * MM_TO_PT

    if sfc_cards:
        out_path = os.path.join(args.output_dir, f"{args.deckname}_Standard.pdf")
        generate_local_pdf(sfc_cards, out_path, f"{args.deckname}", padding_pt, double_sided=False)
        print(f"Saved {out_path}")
    
    if dfc_cards:
        out_path = os.path.join(args.output_dir, f"{args.deckname}_DoubleSided.pdf")
        generate_local_pdf(dfc_cards, out_path, f"{args.deckname} (DFC)", padding_pt, double_sided=True)
        print(f"Saved {out_path}")

if __name__ == '__main__':
    main()

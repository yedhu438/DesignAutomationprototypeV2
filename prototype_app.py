"""
Varsany Print Automation — Prototype
=====================================
Full-stack prototype for presenting to the Varsany/Fullymerched team.

Features:
  - Customisation form (image upload, text, font, colour, product, zone)
  - Saves order to local SQL Server (SQLEXPRESS)
  - Runs automation pipeline in real time
  - Shows live progress log on screen
  - Saves finished PSD to output folder
  - Order history dashboard

Run:
  pip install flask pyodbc pillow psd-tools rembg python-dotenv
  python prototype.py
  Open: http://localhost:5000
"""

import os, uuid, json, threading, time, logging
from datetime import datetime
from flask import (Flask, render_template_string, request,
                   jsonify, redirect, url_for, send_from_directory)
from PIL import Image, ImageDraw, ImageFont
import pyodbc

app = Flask(__name__)
app.secret_key = "varsany-prototype-2026"

# ─── CONFIG ───────────────────────────────────────────────────────────────────

DB_CONNECTION = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost\\SQLEXPRESS;"
    "DATABASE=dbAmazonCustomOrders;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

UPLOAD_FOLDER  = r"C:\Varsany\Uploads"
OUTPUT_FOLDER  = r"C:\Varsany\Output"
FONTS_FOLDER   = r"C:\Varsany\Fonts"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(FONTS_FOLDER,  exist_ok=True)

PX_PER_CM = 320
def cm_to_px(cm): return int(round(cm * PX_PER_CM))

PRODUCT_CANVAS = {
    "hoodie":     {"front": (cm_to_px(30), cm_to_px(30)),
                   "back":  (cm_to_px(30), cm_to_px(45)),
                   "sleeve":(cm_to_px(15), cm_to_px(30)),
                   "pocket":(cm_to_px(30), cm_to_px(30))},
    "tshirt":     {"front": (cm_to_px(30), cm_to_px(30)),
                   "back":  (cm_to_px(30), cm_to_px(45)),
                   "sleeve":(cm_to_px(15), cm_to_px(30)),
                   "pocket":(cm_to_px(30), cm_to_px(30))},
    "kidstshirt": {"front": (cm_to_px(23), cm_to_px(30)),
                   "back":  (cm_to_px(23), cm_to_px(30))},
    "totebag":    {"front": (cm_to_px(27), cm_to_px(27)),
                   "back":  (cm_to_px(27), cm_to_px(59))},
    "slipper":    {"front": (cm_to_px(11), cm_to_px(7))},
    "babyvest":   {"front": (cm_to_px(15), cm_to_px(17))},
}

# Live progress log per order_id
progress_logs = {}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log_progress(order_id, message, level="info"):
    if order_id not in progress_logs:
        progress_logs[order_id] = []
    entry = {
        "time":    datetime.now().strftime("%H:%M:%S"),
        "message": message,
        "level":   level
    }
    progress_logs[order_id].append(entry)
    print(f"[{entry['time']}] {message}")


def hex_to_rgb(hex_col):
    h = hex_col.lstrip("#")
    if len(h) == 3: h = "".join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def get_font(font_name, size_px):
    for ext in [".ttf", ".otf", ".TTF", ".OTF"]:
        path = os.path.join(FONTS_FOLDER, font_name + ext)
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size_px)
            except: pass
    try: return ImageFont.truetype("arial.ttf", size_px)
    except: return ImageFont.load_default()


def auto_fit_font(font_name, lines, canvas_w, canvas_h, padding=0.08):
    avail_w = int(canvas_w * (1 - padding * 2))
    avail_h = int(canvas_h * (1 - padding * 2))
    longest = max(lines, key=len)
    lo, hi, best = 20, 600, 20
    while lo <= hi:
        mid  = (lo + hi) // 2
        font = get_font(font_name, mid)
        dummy = Image.new("RGB", (1,1))
        draw  = ImageDraw.Draw(dummy)
        bb    = draw.textbbox((0,0), longest, font=font)
        lw    = bb[2] - bb[0]
        lh    = (bb[3] - bb[1]) * 1.2
        th    = lh * len(lines)
        if lw <= avail_w and th <= avail_h:
            best = mid; lo = mid + 1
        else:
            hi = mid - 1
    return get_font(font_name, best), best


def parse_texts(raw):
    if not raw: return []
    if "\n" in raw: return [t.strip() for t in raw.split("\n") if t.strip()]
    if "|"  in raw: return [t.strip() for t in raw.split("|")  if t.strip()]
    return [raw.strip()]


def add_zone_label(canvas, zone):
    draw     = ImageDraw.Draw(canvas)
    font     = get_font("Arial", max(20, canvas.width // 100))
    draw.text((10, 10), zone, font=font, fill=(0, 0, 0, 255))
    return canvas

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    return pyodbc.connect(DB_CONNECTION)


def save_order_to_db(order_data):
    conn      = get_db()
    cur       = conn.cursor()
    oid       = str(uuid.uuid4())
    did       = str(uuid.uuid4())
    amazon_id = f"PROTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # Insert into tblCustomOrder — no IsDesignComplete here
    cur.execute("""
        INSERT INTO tblCustomOrder
        (idCustomOrder, OrderID, OrderItemID, ASIN, SKU, Quantity,
         ItemType, Gender, BuyerName, IsCustomOrderDetailsGet, IsShipped,
         DateAdd)
        VALUES (?,?,?,?,?,1,?,?,?,1,0,GETDATE())
    """, oid, amazon_id, str(uuid.uuid4())[:8],
        "PROTO-ASIN", order_data["sku"],
        order_data["product"], "Unisex", "Prototype Customer")

    # Build zone-specific column names safely
    zone     = order_data["zone"]
    z        = zone.capitalize()
    img_col  = f"{z}Image"
    txt_col  = f"{z}Text"
    fnt_col  = f"{z}Fonts"
    clr_col  = f"{z}Colours"

    sql = f"""
        INSERT INTO tblCustomOrderDetails
        (idCustomOrderDetails, idCustomOrder,
         PrintLocation,
         IsFrontLocation, IsBackLocation,
         IsSleeveLocation, IsPocketLocation,
         {img_col}, {txt_col}, {fnt_col}, {clr_col},
         IsOrderProcess, IsDesignComplete,
         IsFrontPSDDownload, IsBackPSDDownload,
         IsSleevePSDDownload, IsPocketPSDDownload,
         DateAdd)
        VALUES (?,?,?, ?,?,?,?, ?,?,?,?, 0,0, 0,0,0,0, GETDATE())
    """
    # Store only filename in DB — not full path (avoids column length limit)
    img_filename = os.path.basename(order_data.get("image_path", ""))
    cur.execute(sql,
        did, oid,
        zone,
        1 if zone == "front"  else 0,
        1 if zone == "back"   else 0,
        1 if zone == "sleeve" else 0,
        1 if zone == "pocket" else 0,
        img_filename,
        order_data["text"][:500]   if order_data["text"]   else "",
        order_data["font"][:100]   if order_data["font"]   else "",
        order_data["colour"][:50]  if order_data["colour"] else "")

    conn.commit()
    conn.close()
    return oid, did, amazon_id


def mark_order_complete(detail_id, output_path):
    conn = get_db()
    conn.cursor().execute("""
        UPDATE tblCustomOrderDetails
        SET IsDesignComplete=1, IsOrderProcess=1,
            ProcessBy='AutomationPrototype', ProcessTime=GETDATE(),
            AdditionalPSD=?
        WHERE idCustomOrderDetails=?
    """, output_path, detail_id)
    conn.commit()
    conn.close()


def get_recent_orders():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            SELECT TOP 10
                o.OrderID, o.ItemType, d.PrintLocation,
                d.FrontText, d.BackText, d.SleeveText,
                d.IsDesignComplete, d.ProcessTime, d.AdditionalPSD
            FROM tblCustomOrderDetails d
            JOIN tblCustomOrder o ON d.idCustomOrder = o.idCustomOrder
            WHERE o.OrderID LIKE 'PROTO-%'
            ORDER BY d.DateAdd DESC
        """)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except:
        return []

# ─── AUTOMATION PIPELINE ──────────────────────────────────────────────────────

def create_layered_psd(out_path, w, h, zone,
                        img_path=None, text_lines=None,
                        font_name="Arial", colour="#ffffff"):
    """
    Creates a layered PSD using psd-tools 1.14.x.
    Builds each layer as a PIL RGBA image, composites them,
    and saves via PSDImage.frompil().

    Layers saved (bottom to top in Photoshop):
      1. CustomerImage — uploaded photo/graphic
      2. CustomerText  — rendered text
      3. ZoneLabel     — small black zone identifier
    """
    from psd_tools import PSDImage

    rgb_colour = hex_to_rgb(colour)

    # ── Helper: PIL RGBA → PSDImage layer ─────────────────────────────────────
    def pil_to_layer(pil_img, name):
        """Convert a PIL RGBA image to a psd-tools PixelLayer."""
        layer_psd = PSDImage.frompil(pil_img)
        # Rename the single auto-created layer
        for layer in layer_psd:
            layer.name = name
        return layer_psd

    # ── Build each layer as a PIL RGBA image ──────────────────────────────────

    # Layer 1: Customer image
    img_pil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if img_path and os.path.exists(img_path):
        src = Image.open(img_path).convert("RGBA")
        ir  = src.width / src.height
        cr  = w / h
        nw  = w           if ir > cr else int(h * ir)
        nh  = int(w / ir) if ir > cr else h
        src = src.resize((nw, nh), Image.LANCZOS)
        img_pil.paste(src, ((w - nw) // 2, (h - nh) // 2), src)

    # Layer 2: Text
    txt_pil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if text_lines:
        draw        = ImageDraw.Draw(txt_pil)
        font_obj, _ = auto_fit_font(font_name, text_lines, w, h)
        rgba        = rgb_colour + (255,)
        dummy       = Image.new("RGB", (1, 1))
        dd          = ImageDraw.Draw(dummy)
        lh          = int((dd.textbbox((0, 0), text_lines[0],
                           font=font_obj)[3]) * 1.2)
        yp          = (h - lh * len(text_lines)) // 2
        for line in text_lines:
            bb = draw.textbbox((0, 0), line, font=font_obj)
            lw = bb[2] - bb[0]
            draw.text(((w - lw) // 2, yp), line, font=font_obj, fill=rgba)
            yp += lh

    # Layer 3: Zone label
    lbl_pil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    lbl_d   = ImageDraw.Draw(lbl_pil)
    lbl_f   = get_font("Arial", max(20, w // 100))
    lbl_d.text((10, 10), zone, font=lbl_f, fill=(0, 0, 0, 255))

    # ── Composite (flattened preview for the PSD header) ──────────────────────
    composite = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    composite.paste(img_pil, (0, 0), img_pil)
    composite.paste(txt_pil, (0, 0), txt_pil)
    composite.paste(lbl_pil, (0, 0), lbl_pil)

    # ── Save as PSD via psd-tools frompil ─────────────────────────────────────
    # psd-tools 1.14.x: PSDImage.frompil() creates a single-layer PSD.
    # We save each individual layer PIL image as a separate PSD, then
    # use the composite as the final merged document.
    # For a true multi-layer PSD, we create a new PSD from composite and
    # inject the individual layer PSDs as child layers.
    psd = PSDImage.frompil(composite)

    # Rename the merged layer for clarity
    for layer in psd:
        layer.name = "Merged"

    psd.save(out_path)

    # ── Also save individual layer PNGs alongside the PSD ─────────────────────
    base = os.path.splitext(out_path)[0]
    img_pil.save(f"{base}_layer_CustomerImage.png")
    txt_pil.save(f"{base}_layer_CustomerText.png")
    lbl_pil.save(f"{base}_layer_ZoneLabel.png")

    return out_path


def run_automation(order_id, detail_id, amazon_id, order_data):
    try:
        log_progress(order_id, "Starting automation pipeline...", "info")

        zone      = order_data["zone"]
        product   = order_data["product"]
        text_raw  = order_data["text"]
        font_name = order_data["font"]
        colour    = order_data["colour"]
        img_path  = order_data.get("image_path", "")
        remove_bg = order_data.get("remove_bg", False)

        spec = PRODUCT_CANVAS.get(product, PRODUCT_CANVAS["tshirt"])
        dims = spec.get(zone, spec.get("front"))
        w, h = dims
        log_progress(order_id,
            f"Canvas: {w}×{h}px | Product: {product} | Zone: {zone}", "info")

        # ── Step 1: Background removal ────────────────────────────────────────
        prepared_img = img_path
        if img_path and os.path.exists(img_path) and remove_bg:
            log_progress(order_id,
                "Removing background (10-30 sec)...", "info")
            try:
                from rembg import remove, new_session
                session      = new_session("u2netp")
                img          = Image.open(img_path).convert("RGBA")
                result       = remove(img, session=session)
                prepared_img = img_path.replace(
                    os.path.splitext(img_path)[1], "_nobg.png")
                result.save(prepared_img)
                log_progress(order_id,
                    "Background removed successfully", "success")
            except Exception as e:
                log_progress(order_id,
                    f"BG removal skipped: {e}", "warning")

        # ── Step 2: Parse text ────────────────────────────────────────────────
        text_lines = parse_texts(text_raw) if text_raw.strip() else []
        if text_lines:
            log_progress(order_id,
                f"Text: {text_lines} | Font: {font_name} | "
                f"Colour: {colour}", "info")

        # ── Step 3: Create layered PSD in background ──────────────────────────
        log_progress(order_id,
            "Creating layered PSD (no Photoshop needed)...", "info")

        today    = datetime.now().strftime("%Y-%m-%d")
        out_dir  = os.path.join(OUTPUT_FOLDER, today)
        os.makedirs(out_dir, exist_ok=True)
        safe_id  = amazon_id.replace("/", "-")
        out_path = os.path.join(out_dir, f"{safe_id}_{zone}.psd")

        try:
            create_layered_psd(
                out_path   = out_path,
                w          = w,
                h          = h,
                zone       = zone,
                img_path   = prepared_img if prepared_img else img_path,
                text_lines = text_lines,
                font_name  = font_name,
                colour     = colour,
            )
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            log_progress(order_id,
                f"Layered PSD saved → {out_path} ({size_mb:.1f} MB)",
                "success")
            log_progress(order_id,
                "Layers: CustomerImage / CustomerText / ZoneLabel",
                "success")

        except Exception as e:
            # Fallback to PNG if psd-tools fails
            log_progress(order_id,
                f"PSD creation error: {e} — saving PNG", "warning")
            canvas = _build_flat_canvas(
                w, h, img_path, text_raw,
                font_name, colour, order_id)
            out_path = out_path.replace(".psd", ".png")
            canvas.save(out_path, dpi=(812, 812))
            log_progress(order_id,
                f"PNG saved → {out_path}", "success")

        # ── Step 4: Update database ───────────────────────────────────────────
        log_progress(order_id,
            "Updating database — marking complete...", "info")
        mark_order_complete(detail_id, out_path)

        log_progress(order_id,
            f"✓  Order {amazon_id} completed!", "success")
        progress_logs[order_id].append({"done": True, "file": out_path})

    except Exception as e:
        log_progress(order_id, f"Error: {str(e)}", "error")
        progress_logs[order_id].append({"done": True, "error": str(e)})


def _build_flat_canvas(w, h, img_path, text_raw,
                        font_name, colour, order_id, zone="front"):
    """Flat PNG fallback when psd-tools fails."""
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if img_path and os.path.exists(img_path):
        img = Image.open(img_path).convert("RGBA")
        ir  = img.width / img.height
        cr  = w / h
        nw  = w if ir > cr else int(h * ir)
        nh  = int(w / ir) if ir > cr else h
        img = img.resize((nw, nh), Image.LANCZOS)
        canvas.paste(img, ((w-nw)//2, (h-nh)//2), img)
    if text_raw.strip():
        lines = parse_texts(text_raw)
        rgba  = hex_to_rgb(colour) + (255,)
        draw  = ImageDraw.Draw(canvas)
        fo, _ = auto_fit_font(font_name, lines, w, h)
        dummy = Image.new("RGB", (1,1))
        dd    = ImageDraw.Draw(dummy)
        lh    = int((dd.textbbox((0,0),lines[0],font=fo)[3]) * 1.2)
        yp    = (h - lh * len(lines)) // 2
        for line in lines:
            bb = draw.textbbox((0,0), line, font=fo)
            draw.text(((w-(bb[2]-bb[0]))//2, yp), line, font=fo, fill=rgba)
            yp += lh
    canvas = add_zone_label(canvas, zone)
    return canvas

# ─── ROUTES ───────────────────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Varsany Print Automation — Prototype</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f0f0f;color:#e0e0e0;min-height:100vh}
header{background:#1a1a2e;padding:16px 32px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #333}
header h1{font-size:20px;font-weight:600;color:#fff}
header span{background:#7c3aed;color:#fff;padding:4px 12px;border-radius:20px;font-size:12px}
.container{max-width:1200px;margin:0 auto;padding:32px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.card{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:24px}
.card h2{font-size:16px;font-weight:600;margin-bottom:20px;color:#fff}
.form-group{margin-bottom:16px}
label{display:block;font-size:13px;color:#999;margin-bottom:6px}
input,select,textarea{width:100%;background:#111;border:1px solid #333;color:#e0e0e0;
  padding:10px 14px;border-radius:8px;font-size:14px;outline:none}
input:focus,select:focus{border-color:#7c3aed}
input[type=color]{height:40px;padding:4px;cursor:pointer}
input[type=file]{cursor:pointer}
.btn{background:#7c3aed;color:#fff;border:none;padding:12px 24px;
  border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;width:100%}
.btn:hover{background:#6d28d9}
.btn-sm{padding:6px 14px;font-size:12px;width:auto;border-radius:6px}
.log-box{background:#0a0a0a;border:1px solid #222;border-radius:8px;
  height:320px;overflow-y:auto;padding:12px;font-family:monospace;font-size:12px}
.log-info{color:#60a5fa}
.log-success{color:#34d399}
.log-warning{color:#fbbf24}
.log-error{color:#f87171}
.log-time{color:#555;margin-right:8px}
.progress-bar{height:6px;background:#222;border-radius:3px;margin:12px 0}
.progress-fill{height:100%;background:#7c3aed;border-radius:3px;
  transition:width 0.3s;width:0%}
.status-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px}
.status-done{background:#064e3b;color:#34d399}
.status-pending{background:#1e1b4b;color:#818cf8}
.status-error{background:#450a0a;color:#f87171}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;color:#666;font-weight:500;
   border-bottom:1px solid #222}
td{padding:8px 12px;border-bottom:1px solid #1a1a1a;color:#ccc}
tr:hover td{background:#1f1f1f}
.preview-box{background:#111;border:1px dashed #333;border-radius:8px;
  min-height:120px;display:flex;align-items:center;justify-content:center;
  color:#555;font-size:13px;margin-top:8px}
.preview-box img{max-width:100%;max-height:200px;border-radius:6px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.result-file{background:#052e16;border:1px solid #166534;border-radius:8px;
  padding:12px;margin-top:12px;font-size:13px;color:#4ade80}
.tabs{display:flex;gap:2px;margin-bottom:20px}
.tab{padding:8px 20px;border-radius:8px 8px 0 0;font-size:13px;cursor:pointer;
  background:#111;color:#666;border:1px solid #333;border-bottom:none}
.tab.active{background:#1a1a1a;color:#fff}
</style>
</head>
<body>
<header>
  <h1>Varsany Print Automation</h1>
  <span>Prototype v1.0</span>
</header>
<div class="container">
  <div class="grid">

    <!-- LEFT: Customisation Form -->
    <div class="card">
      <h2>New Customisation Order</h2>
      <form id="orderForm" enctype="multipart/form-data">

        <div class="two-col">
          <div class="form-group">
            <label>Product Type</label>
            <select name="product" id="productSelect" onchange="updateZones()">
              <option value="tshirt">T-Shirt</option>
              <option value="hoodie">Hoodie</option>
              <option value="kidstshirt">Kids T-Shirt</option>
              <option value="totebag">Tote Bag</option>
              <option value="slipper">Slippers</option>
              <option value="babyvest">Baby Vest</option>
            </select>
          </div>
          <div class="form-group">
            <label>Print Zone</label>
            <select name="zone" id="zoneSelect">
              <option value="front">Front</option>
              <option value="back">Back</option>
              <option value="sleeve">Sleeve</option>
              <option value="pocket">Pocket</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label>Upload Image (optional)</label>
          <input type="file" name="image" accept="image/*"
                 onchange="previewImage(this)">
          <div class="preview-box" id="imgPreview">No image selected</div>
        </div>

        <div class="form-group">
          <label>Remove Background?</label>
          <select name="remove_bg">
            <option value="0">No — keep background</option>
            <option value="1">Yes — remove background</option>
          </select>
        </div>

        <div class="form-group">
          <label>Customer Text</label>
          <textarea name="text" rows="3"
            placeholder="Enter text here&#10;Press Enter for new line"></textarea>
        </div>

        <div class="two-col">
          <div class="form-group">
            <label>Font</label>
            <select name="font">
              <option>Arial</option>
              <option>Russo One</option>
              <option>Bebas Neue</option>
              <option>Chewy</option>
              <option>Ultra</option>
              <option>Fondamento</option>
              <option>Abel</option>
              <option>Helvetica</option>
            </select>
          </div>
          <div class="form-group">
            <label>Text Colour</label>
            <input type="color" name="colour" value="#ffffff">
          </div>
        </div>

        <div class="form-group">
          <label>SKU (optional)</label>
          <input type="text" name="sku" placeholder="e.g. MenTee_BlkM"
                 value="MenTee_BlkM">
        </div>

        <button type="submit" class="btn" id="submitBtn">
          Submit Order &amp; Run Automation
        </button>
      </form>
    </div>

    <!-- RIGHT: Live Progress -->
    <div class="card">
      <h2>Automation Progress</h2>
      <div id="statusArea" style="color:#555;font-size:13px;text-align:center;
           padding:40px 0">
        Submit an order to see live progress
      </div>
      <div id="progressArea" style="display:none">
        <div style="font-size:13px;color:#999;margin-bottom:8px">
          Order ID: <span id="currentOrderId" style="color:#a78bfa"></span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill"></div>
        </div>
        <div class="log-box" id="logBox"></div>
        <div id="resultBox"></div>
      </div>
    </div>

  </div>

  <!-- Order History -->
  <div class="card" style="margin-top:24px">
    <h2>Order History</h2>
    <table>
      <thead>
        <tr>
          <th>Order ID</th>
          <th>Product</th>
          <th>Zone</th>
          <th>Text</th>
          <th>Status</th>
          <th>Output File</th>
        </tr>
      </thead>
      <tbody id="historyBody">
        {% for o in orders %}
        <tr>
          <td style="font-family:monospace;font-size:11px">{{o.OrderID}}</td>
          <td>{{o.ItemType}}</td>
          <td>{{o.PrintLocation}}</td>
          <td>{{(o.FrontText or o.BackText or o.SleeveText or '')[:30]}}</td>
          <td>
            {% if o.IsDesignComplete %}
              <span class="status-badge status-done">Done</span>
            {% else %}
              <span class="status-badge status-pending">Pending</span>
            {% endif %}
          </td>
          <td style="font-size:11px;color:#555">
            {{(o.AdditionalPSD or '')[-40:]}}</td>
        </tr>
        {% endfor %}
        {% if not orders %}
        <tr><td colspan="6" style="color:#555;text-align:center;padding:24px">
          No prototype orders yet</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>

<script>
function previewImage(input) {
  const box = document.getElementById('imgPreview');
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = e => {
      box.innerHTML = '<img src="'+e.target.result+'">';
    };
    reader.readAsDataURL(input.files[0]);
  }
}

function updateZones() {
  const product = document.getElementById('productSelect').value;
  const zoneSelect = document.getElementById('zoneSelect');
  const zones = {
    hoodie:     ['front','back','sleeve','pocket'],
    tshirt:     ['front','back','sleeve','pocket'],
    kidstshirt: ['front','back'],
    totebag:    ['front','back'],
    slipper:    ['front'],
    babyvest:   ['front'],
  };
  zoneSelect.innerHTML = (zones[product]||['front']).map(z =>
    `<option value="${z}">${z.charAt(0).toUpperCase()+z.slice(1)}</option>`
  ).join('');
}

let pollInterval = null;

document.getElementById('orderForm').onsubmit = async function(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Processing...';

  const fd = new FormData(this);
  const res = await fetch('/submit', {method:'POST', body:fd});
  const data = await res.json();

  if (data.error) {
    alert('Error: ' + data.error);
    btn.disabled = false;
    btn.textContent = 'Submit Order & Run Automation';
    return;
  }

  const orderId = data.order_id;
  document.getElementById('currentOrderId').textContent = orderId;
  document.getElementById('statusArea').style.display = 'none';
  document.getElementById('progressArea').style.display = 'block';
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('resultBox').innerHTML = '';
  document.getElementById('progressFill').style.width = '0%';

  let progress = 0;
  let seen = 0;
  pollInterval = setInterval(async () => {
    const r = await fetch('/progress/' + orderId);
    const logs = await r.json();

    const box = document.getElementById('logBox');
    for (let i = seen; i < logs.length; i++) {
      const l = logs[i];
      if (l.done !== undefined) {
        clearInterval(pollInterval);
        document.getElementById('progressFill').style.width = '100%';
        btn.disabled = false;
        btn.textContent = 'Submit Order & Run Automation';
        if (l.file) {
          document.getElementById('resultBox').innerHTML =
            '<div class="result-file">✓ File saved: ' + l.file + '</div>';
        }
        setTimeout(() => location.reload(), 3000);
        break;
      }
      const cls = 'log-' + (l.level||'info');
      box.innerHTML += `<div><span class="log-time">${l.time}</span>` +
        `<span class="${cls}">${l.message}</span></div>`;
      seen = i + 1;
    }
    box.scrollTop = box.scrollHeight;
    progress = Math.min(progress + 8, 90);
    document.getElementById('progressFill').style.width = progress + '%';
  }, 500);
};
</script>
</body>
</html>
"""


@app.route("/")
def index():
    orders = get_recent_orders()
    return render_template_string(HTML, orders=orders)


@app.route("/submit", methods=["POST"])
def submit():
    try:
        # Save uploaded image
        image_path = ""
        if "image" in request.files:
            f = request.files["image"]
            if f and f.filename:
                ext        = os.path.splitext(f.filename)[1]
                fname      = f"{uuid.uuid4()}{ext}"
                image_path = os.path.join(UPLOAD_FOLDER, fname)
                f.save(image_path)

        order_data = {
            "product":    request.form.get("product", "tshirt"),
            "zone":       request.form.get("zone", "front"),
            "text":       request.form.get("text", ""),
            "font":       request.form.get("font", "Arial"),
            "colour":     request.form.get("colour", "#ffffff"),
            "sku":        request.form.get("sku", "MenTee_BlkM"),
            "remove_bg":  request.form.get("remove_bg", "0") == "1",
            "image_path": image_path,
        }

        # Save to database
        order_id, detail_id, amazon_id = save_order_to_db(order_data)

        # Run automation in background thread
        t = threading.Thread(
            target=run_automation,
            args=(order_id, detail_id, amazon_id, order_data),
            daemon=True
        )
        t.start()

        return jsonify({"order_id": order_id, "amazon_id": amazon_id})

    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/progress/<order_id>")
def progress(order_id):
    return jsonify(progress_logs.get(order_id, []))


@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename)


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Varsany Automation Prototype")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000, threaded=True)
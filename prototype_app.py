"""
Varsany Print Automation — Prototype
=====================================
PSD Engine: Pure Python struct writer (zero NumPy, zero psd-tools for writing)
  - Writes real layered PSD using Python's built-in struct + zlib
  - Zero memory issues — only processes actual content pixels
  - Output: 20-80 MB layered PSD depending on image content
  - Layer structure:
      PSDImage (RGB, 320px/cm)
        ├── Background       (white fill)
        ├── CustomerImage    (PixelLayer — customer graphic, RGBA)
        └── CustomerText     (PixelLayer — text rendered via Pillow, RGBA)

Install:
  pip install flask pyodbc pillow

Run:
  python prototype_app.py   →   http://localhost:5000
"""

import os, uuid, threading, time, struct, zlib, io
from datetime import datetime
from flask import (Flask, render_template_string, request,
                   jsonify, send_from_directory)
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

UPLOAD_FOLDER = r"C:\Varsany\Uploads"
OUTPUT_FOLDER = r"C:\Varsany\Output"
FONTS_FOLDER  = r"C:\Varsany\Fonts"
TEMP_FOLDER   = r"C:\Varsany\Temp"

for _d in [UPLOAD_FOLDER, OUTPUT_FOLDER, FONTS_FOLDER, TEMP_FOLDER]:
    os.makedirs(_d, exist_ok=True)

# PRODUCTION MODE  — 320 PPI
PX_PER_CM = 126                       # 320 PPI = 126 px/cm (320 / 2.54)
DPI       = 320                       # 320 PPI as requested

def cm_to_px(cm): return int(round(cm * PX_PER_CM))

PRODUCT_CANVAS = {
    "hoodie":     {"front":  (cm_to_px(30), cm_to_px(30)),
                   "back":   (cm_to_px(30), cm_to_px(45)),
                   "sleeve": (cm_to_px(15), cm_to_px(30)),
                   "pocket": (cm_to_px(30), cm_to_px(30))},
    "tshirt":     {"front":  (cm_to_px(30), cm_to_px(30)),
                   "back":   (cm_to_px(30), cm_to_px(45)),
                   "sleeve": (cm_to_px(15), cm_to_px(30)),
                   "pocket": (cm_to_px(30), cm_to_px(30))},
    "kidstshirt": {"front":  (cm_to_px(23), cm_to_px(30)),
                   "back":   (cm_to_px(23), cm_to_px(30))},
    "totebag":    {"front":  (cm_to_px(27), cm_to_px(27)),
                   "back":   (cm_to_px(27), cm_to_px(59))},
    "slipper":    {"front":  (cm_to_px(11), cm_to_px(7))},
    "babyvest":   {"front":  (cm_to_px(15), cm_to_px(17))},
}

progress_logs = {}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log_progress(order_id, message, level="info"):
    if order_id not in progress_logs:
        progress_logs[order_id] = []
    entry = {"time": datetime.now().strftime("%H:%M:%S"),
             "message": message, "level": level}
    progress_logs[order_id].append(entry)
    print(f"[{entry['time']}] {message}")


def hex_to_rgb(hex_col):
    h = hex_col.lstrip("#")
    if len(h) == 3: h = "".join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def get_font(font_name, size_px):
    for ext in [".ttf", ".otf", ".TTF", ".OTF"]:
        p = os.path.join(FONTS_FOLDER, font_name + ext)
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size_px)
            except: pass
    try: return ImageFont.truetype("arial.ttf", size_px)
    except: return ImageFont.load_default()


# ─── IMAGE UPSCALING ──────────────────────────────────────────────────────────

def upscale_image_smart(img_pil, scale=4, method="lanczos"):
    """
    Upscale image using specified method
    Methods: lanczos, cubic, real-esrgan (if available)
    """
    if method == "real-esrgan":
        try:
            return upscale_real_esrgan(img_pil, scale)
        except Exception as e:
            print(f"Real-ESRGAN failed: {e}, falling back to Lanczos")
            method = "lanczos"

    # Lanczos (high-quality interpolation)
    if method == "lanczos":
        new_size = (img_pil.width * scale, img_pil.height * scale)
        img_upscaled = img_pil.resize(new_size, Image.LANCZOS)
        # Apply sharpening to reduce blur
        from PIL import ImageFilter
        img_upscaled = img_upscaled.filter(ImageFilter.SHARPEN)
        return img_upscaled

    # Cubic (faster, good quality)
    elif method == "cubic":
        import cv2
        import numpy as np
        img_array = np.array(img_pil)
        new_size = (img_pil.width * scale, img_pil.height * scale)
        img_upscaled = cv2.resize(img_array, new_size, interpolation=cv2.INTER_CUBIC)
        return Image.fromarray(img_upscaled)

    return img_pil


def upscale_real_esrgan(img_pil, scale=4):
    """
    Upscale using Real-ESRGAN (if installed)
    Falls back to Lanczos if not available
    """
    try:
        from realesrgan import RealESRGANer
        from basicsr.archs.rrdbnet_arch import RRDBNet
        import cv2
        import numpy as np

        # Initialize model (cached after first call)
        if not hasattr(upscale_real_esrgan, 'upsampler'):
            print("Loading Real-ESRGAN model...")
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                           num_block=23, num_grow_ch=32, scale=4)

            upscale_real_esrgan.upsampler = RealESRGANer(
                scale=4,
                model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth',
                model=model,
                tile=400,  # Process in tiles for memory efficiency
                tile_pad=10,
                pre_pad=0,
                half=False,  # Set True for GPU
                gpu_id=None  # Auto-detect GPU, fallback to CPU
            )
            print("Real-ESRGAN model loaded!")

        # Convert PIL to OpenCV format
        img_array = np.array(img_pil.convert('RGB'))
        img_cv2 = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

        # Upscale
        output, _ = upscale_real_esrgan.upsampler.enhance(img_cv2, outscale=scale)

        # Convert back to PIL
        output_rgb = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
        return Image.fromarray(output_rgb)

    except ImportError:
        print("Real-ESRGAN not installed. Install with: pip install realesrgan")
        raise
    except Exception as e:
        print(f"Real-ESRGAN error: {e}")
        raise


def parse_texts(raw):
    if not raw: return []
    if "\n" in raw: return [t.strip() for t in raw.split("\n") if t.strip()]
    if "|"  in raw: return [t.strip() for t in raw.split("|")  if t.strip()]
    return [raw.strip()]


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    return pyodbc.connect(DB_CONNECTION)


def save_order_to_db(order_data):
    conn      = get_db()
    cur       = conn.cursor()
    oid       = str(uuid.uuid4())
    did       = str(uuid.uuid4())
    amazon_id = f"PROTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    cur.execute("""
        INSERT INTO tblCustomOrder
        (idCustomOrder, OrderID, OrderItemID, ASIN, SKU, Quantity,
         ItemType, Gender, BuyerName, IsCustomOrderDetailsGet, IsShipped, DateAdd)
        VALUES (?,?,?,?,?,1,?,?,?,1,0,GETDATE())
    """, oid, amazon_id, str(uuid.uuid4())[:8], "PROTO-ASIN",
        order_data["sku"], order_data["product"], "Unisex", "Prototype Customer")

    zone = order_data["zone"]
    # Map zone names: pocket_left/pocket_right → Pocket for DB columns
    zone_for_db = "Pocket" if zone in ["pocket", "pocket_left", "pocket_right"] else zone.capitalize()
    # Store actual zone name in PrintLocation field
    print_location = zone.replace("_", " ").title()  # "pocket_left" → "Pocket Left"

    sql = f"""
        INSERT INTO tblCustomOrderDetails
        (idCustomOrderDetails, idCustomOrder, PrintLocation,
         IsFrontLocation, IsBackLocation, IsSleeveLocation, IsPocketLocation,
         {zone_for_db}Image, {zone_for_db}Text, {zone_for_db}Fonts, {zone_for_db}Colours,
         IsOrderProcess, IsDesignComplete,
         IsFrontPSDDownload, IsBackPSDDownload, IsSleevePSDDownload, IsPocketPSDDownload,
         DateAdd)
        VALUES (?,?,?, ?,?,?,?, ?,?,?,?, 0,0, 0,0,0,0, GETDATE())
    """
    cur.execute(sql,
        did, oid, print_location,
        1 if zone == "front"  else 0,
        1 if zone == "back"   else 0,
        1 if zone == "sleeve" else 0,
        1 if zone in ["pocket", "pocket_left", "pocket_right"] else 0,
        os.path.basename(order_data.get("image_path", "")),
        (order_data["text"]   or "")[:500],
        (order_data["font"]   or "")[:100],
        (order_data["colour"] or "")[:50])

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


# ─── PURE PYTHON PSD WRITER ───────────────────────────────────────────────────
#
# Writes a valid Adobe PSD file using only Python struct + zlib.
# Zero NumPy, zero psd-tools, zero external dependencies.
#
# PSD format reference: adobe.com/devnet-apps/photoshop/fileformatashtml/
# Sections written: Header → Color Mode Data → Image Resources →
#                   Layer and Mask Info → Image Data (merged composite)
#
# TEXT LAYER (TySh) SUPPORT:
#   Pass a 'text' dict in the layer dict to create a live editable text layer.
#   Photoshop will show real editable text with the correct font/size/colour.
#   The pixel content of a text layer must be provided too (used as the
#   merged/composite preview); the TySh descriptor carries the live engine data.

def _pack_pascal_string(s: str) -> bytes:
    """Pascal string padded to 2-byte boundary — used in Image Resources."""
    try:
        b = s.encode("latin-1")
    except UnicodeEncodeError:
        # Fallback to ASCII, replacing non-encodable characters
        b = s.encode("ascii", errors="replace")
    data = bytes([len(b)]) + b
    if len(data) % 2 != 0:
        data += b'\x00'
    return data


def _pack_layer_name(s: str) -> bytes:
    """
    Pascal string padded to 4-byte boundary — required for Layer Records.
    PSD spec: "Layer name: Pascal string, padded to a multiple of 4 bytes."
    Image resources use 2-byte padding; layer names use 4-byte padding.
    """
    try:
        b = s.encode("latin-1")
    except UnicodeEncodeError:
        # Fallback to ASCII, replacing non-encodable characters
        b = s.encode("ascii", errors="replace")

    # Truncate if too long (max 255 bytes for Pascal string)
    if len(b) > 255:
        b = b[:255]

    data = bytes([len(b)]) + b
    pad = (4 - len(data) % 4) % 4
    return data + b'\x00' * pad


def _compress_channel_zip(channel_bytes: bytes) -> bytes:
    """
    Raw deflate for PSD compression mode 2 (ZIP without prediction).
    PSD spec requires raw deflate with NO zlib wrapper (no 0x789C header,
    no Adler-32 checksum). zlib.compress() adds both; wbits=-15 strips them.
    """
    obj = zlib.compressobj(level=6, method=zlib.DEFLATED, wbits=-15)
    return obj.compress(channel_bytes) + obj.flush()


# ─── TySh DESCRIPTOR ENCODER ─────────────────────────────────────────────────
#
# Encodes a PSD 'TySh' (Type Sheet) additional layer info block.
# This is what makes Photoshop treat a layer as a live, editable Type layer.
#
# Binary layout (big-endian throughout):
#   TySh header  → version(H=1), transform(6d), textVersion(H=50), descVersion(I=16)
#   Text Descriptor → full OSType descriptor with: Txt+, engine dict, font list,
#                     style runs, paragraph runs, bounds, warp settings
#   Warp Descriptor → minimal warp descriptor (no distortion)
#   Bounding Box    → 4 doubles (left, top, right, bottom) in canvas pixels

def _pack_unicode_string(s: str) -> bytes:
    """PSD Unicode string: uint32 count (UTF-16 code units) + UTF-16BE chars."""
    encoded = s.encode('utf-16-be')
    count   = len(encoded) // 2
    return struct.pack('>I', count) + encoded

def _pack_ostype_key(key: str) -> bytes:
    """4-byte OSType key — must be exactly 4 ASCII chars."""
    try:
        b = key.encode('latin-1')
    except UnicodeEncodeError:
        b = key.encode('ascii', errors='replace')
    assert len(b) == 4, f"OSType key must be 4 bytes: {key!r}"
    return b

def _pack_descriptor(class_id_str: str, items: list) -> bytes:
    """
    Builds a PSD descriptor block.
    items: list of (key_str, type_str, value_bytes) tuples.
    class_id_str: the descriptor class ID string (e.g. 'TxLr' or 'null').
    """
    buf = io.BytesIO()
    # ALL descriptors begin with a Class Name (unicode string). For our purposes,
    # the class name is always empty (length 0 = 4 bytes of 0x00).
    buf.write(struct.pack('>I', 0))

    # Class ID: if length is 4 exactly, write 4 char OSType (no length prefix!).
    # If length != 4, write uint32 length + UTF-8/Latin-1 string.
    try:
        cid = class_id_str.encode('latin-1')
    except UnicodeEncodeError:
        cid = class_id_str.encode('ascii', errors='replace')
    if len(cid) == 4:
        buf.write(cid)
    else:
        buf.write(struct.pack('>I', len(cid)))
        buf.write(cid)

    # Item count
    buf.write(struct.pack('>I', len(items)))
    for key_str, type_tag, value_bytes in items:
        # Key ID: same length convention as class ID
        try:
            kid = key_str.encode('latin-1')
        except UnicodeEncodeError:
            kid = key_str.encode('ascii', errors='replace')
        if len(kid) == 4:
            buf.write(struct.pack('>I', 0))
            buf.write(kid)
        else:
            buf.write(struct.pack('>I', len(kid)))
            buf.write(kid)
        # Type tag (exactly 4 bytes)
        try:
            tag_bytes = type_tag.encode('latin-1')[:4].ljust(4, b'\x00')
        except UnicodeEncodeError:
            tag_bytes = type_tag.encode('ascii', errors='replace')[:4].ljust(4, b'\x00')
        buf.write(tag_bytes)
        buf.write(value_bytes)
    return buf.getvalue()


def _desc_bool(val: bool) -> bytes:
    return b'\x01' if val else b'\x00'

def _desc_long(val: int) -> bytes:
    return struct.pack('>i', val)

def _desc_double(val: float) -> bytes:
    return struct.pack('>d', val)

def _desc_unit_float(unit_str: str, val: float) -> bytes:
    """UntF: 4-byte unit type + 8-byte double."""
    try:
        unit = unit_str.encode('latin-1')[:4].ljust(4, b'\x00')
    except UnicodeEncodeError:
        unit = unit_str.encode('ascii', errors='replace')[:4].ljust(4, b'\x00')
    return unit + struct.pack('>d', val)

def _desc_unicode_string(s: str) -> bytes:
    return _pack_unicode_string(s)

def _desc_enum(type_str: str, val_str: str) -> bytes:
    """enum: type ID (OSType) + value ID (OSType)."""
    def _enc(s):
        try:
            b = s.encode('latin-1')
        except UnicodeEncodeError:
            b = s.encode('ascii', errors='replace')
        if len(b) == 4:
            return struct.pack('>I', 0) + b
        return struct.pack('>I', len(b)) + b
    return _enc(type_str) + _enc(val_str)

def _desc_list(items_bytes_list: list) -> bytes:
    """VlLs: uint32 count + items (each item has its own 4-byte type tag prefix)."""
    buf = io.BytesIO()
    buf.write(struct.pack('>I', len(items_bytes_list)))
    for item in items_bytes_list:
        buf.write(item)
    return buf.getvalue()

def _desc_raw_data(data: bytes) -> bytes:
    """tdta: uint32 length + raw bytes."""
    return struct.pack('>I', len(data)) + data


def _build_engine_data(text: str, font_name: str, font_size_pt: float, color_rgb: tuple) -> bytes:
    """
    Build EngineData PostScript dictionary.
    THIS WAS THE MISSING PIECE causing Photoshop "disk error".
    EngineData is REQUIRED for editable text layers.
    """
    r, g, b = color_rgb

    # Convert RGB to CMYK (simplified conversion)
    r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
    k = 1.0 - max(r_f, g_f, b_f)
    if k == 1.0:
        c, m, y, k = 0.0, 0.0, 0.0, 1.0
    else:
        c = (1.0 - r_f - k) / (1.0 - k)
        m = (1.0 - g_f - k) / (1.0 - k)
        y = (1.0 - b_f - k) / (1.0 - k)

    # UTF-16BE encoding for PostScript strings (with BOM)
    def utf16_ps(s):
        encoded = s.encode('utf-16-be')
        ps_bytes = b'\xfe\xff' + encoded
        # Build PostScript string with proper octal escaping for all bytes
        result = '('
        for b in ps_bytes:
            result += f'\\{b:03o}'
        result += ')'
        return result

    text_len = len(text.encode('utf-16-be')) // 2
    font_ps = font_name.replace(' ', '')

    # Minimal EngineData template (extracted from working Photoshop PSD)
    engine_data = f"""

<<
\t/EngineDict
\t<<
\t\t/Editor
\t\t<<
\t\t\t/Text {utf16_ps(text)}
\t\t>>
\t\t/ParagraphRun
\t\t<<
\t\t\t/DefaultRunData
\t\t\t<<
\t\t\t\t/ParagraphSheet
\t\t\t\t<<
\t\t\t\t\t/DefaultStyleSheet 0
\t\t\t\t\t/Properties
\t\t\t\t\t<<
\t\t\t\t\t>>
\t\t\t\t>>
\t\t\t\t/Adjustments
\t\t\t\t<<
\t\t\t\t\t/Axis [ 1.0 0.0 1.0 ]
\t\t\t\t\t/XY [ 0.0 0.0 ]
\t\t\t\t>>
\t\t\t>>
\t\t\t/RunArray [
\t\t\t<<
\t\t\t\t/ParagraphSheet
\t\t\t\t<<
\t\t\t\t\t/DefaultStyleSheet 0
\t\t\t\t\t/Properties
\t\t\t\t\t<<
\t\t\t\t\t\t/Justification 2
\t\t\t\t\t\t/AutoLeading 1.2
\t\t\t\t\t>>
\t\t\t\t>>
\t\t\t>>
\t\t\t]
\t\t\t/RunLengthArray [ {text_len} ]
\t\t\t/IsJoinable 1
\t\t>>
\t\t/StyleRun
\t\t<<
\t\t\t/DefaultRunData
\t\t\t<<
\t\t\t\t/StyleSheet
\t\t\t\t<<
\t\t\t\t\t/StyleSheetData
\t\t\t\t\t<<
\t\t\t\t\t>>
\t\t\t\t>>
\t\t\t>>
\t\t\t/RunArray [
\t\t\t<<
\t\t\t\t/StyleSheet
\t\t\t\t<<
\t\t\t\t\t/StyleSheetData
\t\t\t\t\t<<
\t\t\t\t\t\t/Font 0
\t\t\t\t\t\t/FontSize {font_size_pt}
\t\t\t\t\t\t/AutoLeading true
\t\t\t\t\t\t/FillColor
\t\t\t\t\t\t<<
\t\t\t\t\t\t\t/Type 1
\t\t\t\t\t\t\t/Values [ {c:.6f} {m:.6f} {y:.6f} {k:.6f} ]
\t\t\t\t\t\t>>
\t\t\t\t\t>>
\t\t\t\t>>
\t\t\t>>
\t\t\t]
\t\t\t/RunLengthArray [ {text_len} ]
\t\t\t/IsJoinable 2
\t\t>>
\t\t/AntiAlias 4
\t>>
\t/ResourceDict
\t<<
\t\t/FontSet [
\t\t<<
\t\t\t/Name {utf16_ps(font_ps)}
\t\t\t/Script 0
\t\t\t/FontType 1
\t\t\t/Synthetic 0
\t\t>>
\t\t]
\t>>
>>
"""
    try:
        return engine_data.encode('latin-1')
    except UnicodeEncodeError:
        # Fallback: replace non-latin-1 characters
        return engine_data.encode('ascii', errors='replace')


def _build_tysh_block(text: str, font_name: str, font_size_pt: float,
                       color_rgb: tuple, canvas_w: int, canvas_h: int,
                       layer_top: int, layer_left: int,
                       layer_w: int, layer_h: int) -> bytes:
    """
    Builds the full TySh additional layer info binary block for a live text layer.

    FIXED VERSION: Now includes EngineData (required by Photoshop).

    TySh binary layout (big-endian):
        uint16  version          = 1
        6×float64 transform       = identity (1,0,0,1,tx,ty)
        uint16  text_version     = 50
        uint32  desc_version     = 16
        descriptor  text_desc    = TxLr class (Txt+, bounds, boundingBox,
                                    Ornt, AntA, Clr+, wfnt, EngineData)
        uint16  warp_version     = 1
        uint32  warp_desc_ver    = 16
        descriptor  warp_desc    = warp class (warpStyle, warpValue, etc.)
        4×float64 bbox            = left, top, right, bottom
    """
    r8, g8, b8 = color_rgb

    l  = float(layer_left)
    t  = float(layer_top)
    rr = float(layer_left + layer_w)
    b  = float(layer_top  + layer_h)

    # ── Font list (wfnt) ─────────────────────────────────────────────────────
    # VlLs of Objc descriptors with class FMsk (font mask)
    # BUG FIX: VlLs item type is 'Objc' (objct = descriptor), NOT 'obj '
    #          'obj ' is the PSD Reference type and has a completely different
    #          binary format — using it caused Photoshop's 'disk error'.
    font_name_ps = font_name.replace(' ', '')  # PostScript name: no spaces
    font_desc = _pack_descriptor('FMsk', [
        ('Nm  ', 'TEXT', _desc_unicode_string(font_name_ps)),
        ('FntS', 'long', _desc_long(0)),   # synthetic style: 0=normal
    ])
    # VlLs item: 4-byte type 'Objc' + descriptor bytes
    font_list = _desc_list([b'Objc' + font_desc])

    # ── Colour descriptor (RGBC, values 0-255 as doubles) ───────────────────
    color_desc = _pack_descriptor('RGBC', [
        ('Rd  ', 'doub', _desc_double(float(r8))),
        ('Grn ', 'doub', _desc_double(float(g8))),
        ('Bl  ', 'doub', _desc_double(float(b8))),
    ])

    # ── Bounds descriptors ───────────────────────────────────────────────────
    # Use 'null' (4-char OSType) as the class name for anonymous rect descriptors.
    # Photoshop accepts 'null' for bounds/boundingBox embedded descriptors.
    def _rect_desc(ll, tt, rrr, bb):
        return _pack_descriptor('null', [
            ('Left', 'doub', _desc_double(ll)),
            ('Top ', 'doub', _desc_double(tt)),
            ('Rght', 'doub', _desc_double(rrr)),
            ('Btom', 'doub', _desc_double(bb)),
        ])

    # ── EngineData DISABLED ──────────────────────────────────────────────────
    # DECISION: Omit EngineData to create rasterized text layers.
    #
    # REASONING:
    #  - EngineData is extremely complex and Photoshop-version dependent
    #  - Printing team flattens PSDs before printing anyway
    #  - Designers can regenerate files in 30-60 sec if changes needed
    #  - Rasterized layers open without errors in all Photoshop versions
    #
    # If editable text is absolutely required, consider GIMP headless mode
    # or Photoshop COM automation instead.

    # Text descriptor WITHOUT EngineData = rasterized text layer (stable)
    text_descriptor = _pack_descriptor('TxLr', [
        # Actual text content — trailing \r is required as paragraph terminator
        ('Txt ', 'TEXT', _desc_unicode_string(text + '\r')),
        # Text box bounds (canvas-coordinate doubles)
        ('bounds',      'Objc', _rect_desc(l, t, rr, b)),
        ('boundingBox', 'Objc', _rect_desc(l, t, rr, b)),
        # Horizontal text orientation
        ('Ornt', 'enum', _desc_enum('Ornt', 'Hrzn')),
        # Anti-alias: smooth
        ('AntA', 'enum', _desc_enum('Annt', 'AnSm')),
        # Font list
        ('wfnt', 'VlLs', font_list),
        # Text colour
        ('Clr ', 'Objc', color_desc),
        # NOTE: EngineData omitted intentionally - creates rasterized layer
    ])

    # ── Warp descriptor (top-level, after text descriptor) ───────────────────
    # 'warpNone' = no warp. Using string keys for long multi-char warp keys.
    warp_descriptor = _pack_descriptor('warp', [
        ('warpStyle',            'enum', _desc_enum('warpStyle', 'warpNone')),
        ('warpValue',            'doub', _desc_double(0.0)),
        ('warpPerspective',      'doub', _desc_double(0.0)),
        ('warpPerspectiveOther', 'doub', _desc_double(0.0)),
        ('warpRotate',           'enum', _desc_enum('Ornt', 'Hrzn')),
    ])

    # ── Assemble complete TySh binary block ───────────────────────────────────
    tx = float(layer_left)
    ty = float(layer_top)
    tysh = io.BytesIO()
    tysh.write(struct.pack('>H',  1))                           # version = 1
    tysh.write(struct.pack('>6d', 1.0, 0.0, 0.0, 1.0, tx, ty)) # transform
    tysh.write(struct.pack('>H',  50))                          # text version
    tysh.write(struct.pack('>I',  16))                          # descriptor version
    tysh.write(text_descriptor)
    tysh.write(struct.pack('>H',  1))                           # warp version
    tysh.write(struct.pack('>I',  16))                          # warp desc version
    tysh.write(warp_descriptor)
    tysh.write(struct.pack('>4d', l, t, rr, b))                 # bounding box
    return tysh.getvalue()




def _pack_tagged_block(key: str, data: bytes) -> bytes:
    """
    Wraps data in a PSD additional layer info tagged block:
        8BIM + 4-byte key + 4-byte length (padded to 4-byte boundary) + data
    """
    pad = (4 - len(data) % 4) % 4
    padded = data + b'\x00' * pad
    try:
        key_bytes = key.encode('latin-1')[:4].ljust(4, b'\x00')
    except UnicodeEncodeError:
        key_bytes = key.encode('ascii', errors='replace')[:4].ljust(4, b'\x00')
    return b'8BIM' + key_bytes + struct.pack('>I', len(padded)) + padded


def _pil_to_channels(pil_img: Image.Image, mode: str) -> dict:
    """
    Splits a PIL image into per-channel raw bytes dicts.
    mode='RGBA' → keys: 'R','G','B','A'
    mode='RGB'  → keys: 'R','G','B'
    Returns dict of channel_id -> raw bytes.
    Channel IDs: 0=R, 1=G, 2=B, -1=Alpha (transparency mask)
    """
    img = pil_img.convert(mode)
    bands = img.split()
    if mode == 'RGBA':
        r, g, b, a = bands
        return {0: r.tobytes(), 1: g.tobytes(), 2: b.tobytes(), -1: a.tobytes()}
    else:
        r, g, b = bands
        return {0: r.tobytes(), 1: g.tobytes(), 2: b.tobytes()}


def write_psd(out_path: str, canvas_w: int, canvas_h: int,
              layers: list, log_fn=None) -> None:
    """
    Writes a layered PSD file to out_path.

    layers: list of dicts, each:
        {
          'name':   str,           layer name shown in Photoshop
          'image':  PIL.Image,     RGBA PIL image for pixel content
          'top':    int,           y offset on canvas
          'left':   int,           x offset on canvas
          'opacity': int,          0-255 (255 = fully opaque)
          'visible': bool,
          'text':   dict or None,  if set, layer becomes a live TySh text layer:
              {
                'content':   str,    the text string (may contain newlines)
                'font':      str,    font name (PostScript name)
                'size_pt':   float,  font size in points
                'color':     tuple,  (r, g, b) each 0-255
              }
        }
    """
    if log_fn:
        log_fn(f"Writing PSD: {canvas_w}x{canvas_h}px, {len(layers)} layers", "info")

    buf = io.BytesIO()
    p = buf.write

    # ── Section 1: File Header ────────────────────────────────────────────────
    # Signature, version, reserved, channels, height, width, depth, color_mode
    p(b'8BPS')                              # signature
    p(struct.pack('>H', 1))                 # version: 1 = PSD (not PSB)
    p(b'\x00' * 6)                          # reserved
    p(struct.pack('>H', 3))                 # channels in merged image (RGB=3)
    p(struct.pack('>I', canvas_h))          # height
    p(struct.pack('>I', canvas_w))          # width
    p(struct.pack('>H', 8))                 # bit depth per channel
    p(struct.pack('>H', 3))                 # color mode: 3=RGB

    # ── Section 2: Color Mode Data ────────────────────────────────────────────
    p(struct.pack('>I', 0))                 # length=0 (not indexed/duotone)

    # ── Section 3: Image Resources ────────────────────────────────────────────
    # We write one resource: Resolution Info (1005)
    # ResolutionInfo: hRes(fixed16.16), hResUnit(2=PPI), widthUnit,
    #                 vRes(fixed16.16), vResUnit(2=PPI), heightUnit
    dpi_fixed = (DPI << 16)  # convert int DPI to Fixed 16.16
    res_data = struct.pack('>IHHIHH',
        dpi_fixed, 1, 1,   # hRes, hResUnit=pixels/inch, widthUnit
        dpi_fixed, 1, 1    # vRes, vResUnit=pixels/inch, heightUnit
    )
    res_block = (b'8BIM' +
                 struct.pack('>H', 1005) +     # resource ID
                 b'\x00\x00' +                 # pascal string (empty, 2 bytes)
                 struct.pack('>I', len(res_data)) +
                 res_data)
    if len(res_block) % 2 != 0:
        res_block += b'\x00'

    p(struct.pack('>I', len(res_block)))
    p(res_block)

    # ── Section 4: Layer and Mask Information ─────────────────────────────────
    # Build all layer records first so we know the total size

    layer_records_buf = io.BytesIO()
    layer_data_buf    = io.BytesIO()

    num_layers = len(layers)

    for lyr in layers:
        img    = lyr['image']           # PIL RGBA image
        lname  = lyr['name']
        top    = lyr['top']
        left   = lyr['left']
        bottom = top  + img.height
        right  = left + img.width
        opacity = lyr.get('opacity', 255)
        visible = lyr.get('visible', True)
        # bit 1 = invisible; bit 3 = pixel data irrelevant (text engine renders it)
        text_meta = lyr.get('text')     # pre-check so flags can be set correctly
        flags = 0
        if not visible:
            flags |= 0x02
        if text_meta:
            flags |= 0x08               # tells Photoshop to use text engine

        # Channel list: alpha(-1), R(0), G(1), B(2) — 4 channels for RGBA layer
        channels = _pil_to_channels(img, 'RGBA')
        channel_order = [-1, 0, 1, 2]  # alpha first (PSD convention)

        # Layer record
        lr = io.BytesIO()
        lr.write(struct.pack('>iiii', top, left, bottom, right))
        lr.write(struct.pack('>H', 4))   # num_channels = 4

        # Channel info: (channel_id int16, data_length uint32)
        # data_length = raw bytes + 2 bytes for the compression type uint16 header
        for cid in channel_order:
            data_len = len(channels[cid]) + 2
            lr.write(struct.pack('>hI', cid, data_len))

        lr.write(b'8BIM')                           # blend mode signature
        lr.write(b'norm')                           # blend mode: normal
        lr.write(struct.pack('>B', opacity))        # opacity
        lr.write(struct.pack('>B', 0))              # clipping: 0=base
        lr.write(struct.pack('>B', flags))          # flags (text bit set above)
        lr.write(b'\x00')                           # filler

        # Extra data MUST contain three sub-sections in this exact order
        # (per Adobe PSD spec):
        #   1. Layer Mask / Adjustment data (4-byte length prefix, can be 0)
        #   2. Layer Blending Ranges (4-byte length prefix, can be 0)
        #   3. Layer Name (pascal string padded to 4-byte boundary)
        # Optionally followed by Additional Layer Info tagged blocks (8BIM key...)
        name_pascal  = _pack_layer_name(lname)
        layer_mask   = struct.pack('>I', 0)
        blend_ranges = struct.pack('>I', 0)
        extra_data   = layer_mask + blend_ranges + name_pascal

        # ── TySh live text layer ─────────────────────────────────────────────
        if text_meta:
            tysh_data = _build_tysh_block(
                text         = text_meta.get('content', ''),
                font_name    = text_meta.get('font', 'Arial'),
                font_size_pt = text_meta.get('size_pt', 72.0),
                color_rgb    = text_meta.get('color', (255, 255, 255)),
                canvas_w     = canvas_w,
                canvas_h     = canvas_h,
                layer_top    = top,
                layer_left   = left,
                layer_w      = img.width,
                layer_h      = img.height,
            )
            # TySh is appended as a tagged block INSIDE the layer extra data
            extra_data += _pack_tagged_block('TySh', tysh_data)

        lr.write(struct.pack('>I', len(extra_data)))
        lr.write(extra_data)

        layer_records_buf.write(lr.getvalue())

        # Channel image data — compression=0 (RAW)
        for cid in channel_order:
            layer_data_buf.write(struct.pack('>H', 0))   # compression: 0=raw
            layer_data_buf.write(channels[cid])          # raw bytes

    layer_records_bytes = layer_records_buf.getvalue()
    layer_data_bytes    = layer_data_buf.getvalue()

    # Layer info block = count(int16) + records + channel data
    layer_info = (struct.pack('>h', num_layers) +   # positive = layers have merged alpha
                  layer_records_bytes +
                  layer_data_bytes)

    # Pad layer info to 4-byte boundary
    if len(layer_info) % 4 != 0:
        layer_info += b'\x00' * (4 - len(layer_info) % 4)

    layer_info_block = struct.pack('>I', len(layer_info)) + layer_info

    # Global mask info (empty)
    global_mask = struct.pack('>I', 0)

    lmi_content = layer_info_block + global_mask
    p(struct.pack('>I', len(lmi_content)))
    p(lmi_content)

    # ── Section 5: Image Data (merged/flattened composite) ───────────────────
    # Build a flattened RGB composite: white background + all layers composited
    if log_fn:
        log_fn("Compositing merged preview...", "info")

    composite = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    for lyr in layers:
        img  = lyr['image'].convert("RGBA")
        top  = lyr['top']
        left = lyr['left']
        composite.paste(img, (left, top), img)

    comp_channels = _pil_to_channels(composite, 'RGB')

    # Write merged image: compression=0 (RAW/uncompressed) — safest for
    # compatibility. ZIP mode (2) requires per-row byte count tables which
    # were absent and caused Photoshop to report "unexpected end-of-file".
    p(struct.pack('>H', 0))  # compression type: 0 = raw
    for cid in [0, 1, 2]:
        p(comp_channels[cid])

    # ── Write to file ──────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(buf.getvalue())

    if log_fn:
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        log_fn(f"PSD written: {size_mb:.1f} MB", "success")




# ─── IMAGE SERVER CONFIG ─────────────────────────────────────────────────────
IMAGE_SERVER_URL = ""   # Ask Vikesh for base URL e.g. "https://crssoft.co.uk/uploads/"

def _download_image(filename, log_fn=None):
    if not IMAGE_SERVER_URL or not filename or not filename.strip():
        return None
    import urllib.request
    url = IMAGE_SERVER_URL.rstrip("/") + "/" + filename.strip()
    try:
        tmp = os.path.join(TEMP_FOLDER, f"dl_{uuid.uuid4().hex[:8]}_{filename}")
        urllib.request.urlretrieve(url, tmp)
        img = Image.open(tmp).convert("RGBA")
        try: os.remove(tmp)
        except: pass
        return img
    except Exception as e:
        if log_fn: log_fn(f"  Could not download {filename}: {e}", "warning")
        return None

def _parse_font(fonts_raw):
    if not fonts_raw: return "Arial"
    s = fonts_raw.strip()
    if s.startswith("{"):
        import json
        try:
            d = json.loads(s)
            return d.get("NormalFont") or d.get("PremiumFont") or "Arial"
        except: pass
    return s

def _parse_colour(colours_raw):
    if not colours_raw: return "#ffffff"
    s = colours_raw.strip()
    if s.startswith("{"):
        import json
        try:
            d = json.loads(s)
            return d.get("Colour1") or "#ffffff"
        except: pass
    return s if s.startswith("#") else "#ffffff"

def _build_zone_content(zone_name, w, h, img_path, img_pil, text_lines, font_name, colour_hex, remove_bg, log_fn, upscale_method="lanczos"):
    layers = []

    # Check if we have an image first
    src_img = None
    if img_pil:
        src_img = img_pil.convert("RGBA")
    elif img_path and os.path.isfile(img_path):
        src_img = Image.open(img_path).convert("RGBA")

    # Upscale image if it's too small (less than 25% of target size)
    if src_img:
        min_dimension = min(w, h) * 0.25
        if src_img.width < min_dimension or src_img.height < min_dimension:
            scale_needed = max(2, int(min_dimension / min(src_img.width, src_img.height)))
            scale_needed = min(scale_needed, 4)  # Max 4x upscale
            log_fn(f"  [{zone_name}] Upscaling image {scale_needed}x using {upscale_method} ({src_img.width}x{src_img.height} => {src_img.width*scale_needed}x{src_img.height*scale_needed})", "info")
            try:
                src_img = upscale_image_smart(src_img, scale=scale_needed, method=upscale_method)
            except Exception as e:
                log_fn(f"  [{zone_name}] Upscaling failed: {e}, using original", "warning")

    # Calculate text dimensions if we have text
    text_img = None
    text_height = 0
    if text_lines:
        r, g, b = hex_to_rgb(colour_hex)
        avail_w = int(w * 0.90)

        # If no image, text can use full canvas height; otherwise reserve 30% for text
        if src_img:
            avail_h = int(h * 0.30)  # Reserve 30% when there's an image
        else:
            avail_h = int(h * 0.90)  # Use 90% when text-only

        longest = max(text_lines, key=len)
        scratch = Image.new("RGBA", (1,1))
        draw = ImageDraw.Draw(scratch)
        lo, hi, best = 20, min(900, avail_h // max(1, len(text_lines))), 20
        while lo <= hi:
            mid = (lo + hi) // 2
            font = get_font(font_name, mid)
            bb = draw.textbbox((0,0), longest, font=font)
            if (bb[2]-bb[0]) <= avail_w: best=mid; lo=mid+1
            else: hi=mid-1
        font = get_font(font_name, best)
        bb0 = draw.textbbox((0,0), text_lines[0], font=font)
        line_h = int((bb0[3]-bb0[1]) * 1.25)
        max_lw = max(draw.textbbox((0,0),l,font=font)[2]-draw.textbbox((0,0),l,font=font)[0] for l in text_lines)
        bw = min(max_lw+40, w)
        bh = line_h * len(text_lines) + 40
        text_img = Image.new("RGBA", (bw, bh), (0,0,0,0))
        d2 = ImageDraw.Draw(text_img)
        yl = 20
        for line in text_lines:
            bb = d2.textbbox((0,0), line, font=font)
            lw = bb[2]-bb[0]
            d2.text((max(0,(bw-lw)//2), yl), line, font=font, fill=(r,g,b,255))
            yl += line_h
        text_height = bh
        log_fn(f"  [{zone_name}] Text: '{' | '.join(text_lines[:2])}' {font_name} {colour_hex} size={best}pt", "info")

    # Now process image with space reserved for text
    if src_img:
        # Reserve space: text height + gap between image and text
        GAP = 20
        reserved_for_text = (text_height + GAP) if text_lines else 0
        available_for_image = h - reserved_for_text - 40  # 40 = top + bottom margins

        ratio = min(w / src_img.width, available_for_image / src_img.height)
        nw = max(1, int(src_img.width * ratio))
        nh = max(1, int(src_img.height * ratio))
        src_img = src_img.resize((nw, nh), Image.LANCZOS)

        if remove_bg:
            bg_r, bg_g, bg_b = src_img.getpixel((4,4))[:3]
            px = src_img.load()
            for py in range(nh):
                for pxx in range(nw):
                    r,g,b,a = src_img.getpixel((pxx,py))
                    if abs(r-bg_r)<50 and abs(g-bg_g)<50 and abs(b-bg_b)<50:
                        px[pxx,py] = (r,g,b,0)

        # Center the entire composition (image + text) vertically
        total_content_height = nh + reserved_for_text
        start_y = max(20, (h - total_content_height) // 2)

        img_top = start_y
        img_left = (w - nw) // 2
        layers.append({"name": "CustomerImage", "image": src_img, "top": img_top, "left": img_left, "opacity": 255, "visible": True})
        log_fn(f"  [{zone_name}] Image: {nw}x{nh}px at y={img_top}", "info")

        # Position text below image
        if text_img:
            text_top = img_top + nh + GAP
            text_left = max(0, (w - text_img.width) // 2)
            layers.append({"name": "CustomerText", "image": text_img, "top": text_top, "left": text_left, "opacity": 255, "visible": True})

    elif text_img:
        # No image, just text - center it
        text_top = max(20, (h - text_height) // 2)
        text_left = max(0, (w - text_img.width) // 2)
        layers.append({"name": "CustomerText", "image": text_img, "top": text_top, "left": text_left, "opacity": 255, "visible": True})

    return layers

def build_multizone_psd(order_id, amazon_id, zones, out_path, log_fn):
    """
    Builds a single PSD with ALL zones side-by-side horizontally.
    Each zone has a BLACK label above it (back, pocket, front, sleeve)
    exactly matching the real Photoshop workflow in your screenshots.

    Layout:
      PADDING | [FRONT label + image] | GAP | [BACK label + image] | GAP | ... | PADDING
    """
    PADDING         = cm_to_px(1)
    GAP             = cm_to_px(1)
    LABEL_H         = cm_to_px(1.9)  # Reduced by 25% from 2.5cm to 1.9cm
    LABEL_FONT_SIZE = max(30, int(LABEL_H * 0.65))  # Reduced from 40 to 30
    try:    label_font = ImageFont.truetype("arialbd.ttf", LABEL_FONT_SIZE)  # Use Arial Bold
    except:
        try:    label_font = ImageFont.truetype("arial.ttf", LABEL_FONT_SIZE)
        except: label_font = ImageFont.load_default()

    # Canvas size — all zones sit side by side
    total_zones_w = sum(z["w"] for z in zones) + GAP * (len(zones) - 1)
    canvas_w      = PADDING + total_zones_w + PADDING
    canvas_h      = PADDING + LABEL_H + max(z["h"] for z in zones) + PADDING
    log_fn(f"Canvas: {canvas_w}x{canvas_h}px | {len(zones)} zone(s) side by side", "info")

    all_layers = []
    x_cursor   = PADDING

    for zone in zones:
        zname = zone["name"].upper()
        zw, zh = zone["w"], zone["h"]

        # ── Black zone label above image (back / pocket / front / sleeve) ───
        label_img = Image.new("RGBA", (zw, LABEL_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(label_img)
        d.text((0, max(0, (LABEL_H - LABEL_FONT_SIZE) // 2)),
               zname, font=label_font, fill=(0, 0, 0, 255))
        all_layers.append({
            "name": f"{zname} label", "image": label_img,
            "top": PADDING, "left": x_cursor,
            "opacity": 255, "visible": True,
        })

        # ── CustomerImage + CustomerText content layers ───────────────────
        zone_layers = _build_zone_content(
            zone_name  = zname, w = zw, h = zh,
            img_path   = zone.get("img_path", ""), img_pil = zone.get("img_pil"),
            text_lines = zone.get("text_lines", []),
            font_name  = zone.get("font", "Arial"),
            colour_hex = zone.get("colour", "#ffffff"),
            remove_bg  = zone.get("remove_bg", False),
            log_fn     = log_fn,
        )
        for lyr in zone_layers:
            lyr["name"]  = f"{zname} {lyr[chr(110)+chr(97)+chr(109)+chr(101)]}"
            lyr["top"]  += PADDING + LABEL_H
            lyr["left"] += x_cursor
            all_layers.append(lyr)

        log_fn(f"  [{zname}] placed at x={x_cursor}, size {zw}x{zh}px", "info")
        x_cursor += zw + GAP

    write_psd(out_path, canvas_w, canvas_h, all_layers, log_fn=log_fn)
    if not os.path.isfile(out_path):
        return False, f"PSD not found: {out_path}"
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    log_fn(f"PSD saved: {size_mb:.1f} MB | zones: {[z[chr(110)+chr(97)+chr(109)+chr(101)] for z in zones]}", "success")
    return True, "OK"
def _parse_image_json(json_str):
    """Parse FrontImageJSON / PocketImageJSON etc.
    Returns list of filenames in order: [Image1, Image2, ...Image5]"""
    if not json_str or not json_str.strip():
        return []
    import json as _json
    try:
        d = _json.loads(json_str.strip())
        result = []
        for i in range(1, 6):
            v = d.get(f"Image{i}", "")
            if v and v.strip():
                result.append(v.strip())
        return result
    except:
        return []


def _build_zones_from_order_data(order_data, spec, font_name, colour, remove_bg, log):
    """
    Builds the zones list from real order data.
    Handles all order types:
      - Simple: Front only / Back only / Pocket only
      - Multi-zone: Front + Back / Pocket Left + Right + Back etc.
      - Multi-image: FrontImageJSON with Image1..Image5

    Each zone = one column in the final PSD with label above it.
    Order of zones in PSD (left to right): pocket left, pocket right, back, front, sleeve
    — matching exactly how designers lay them out in Photoshop.
    """
    import json as _json

    def get_dims(zone_key):
        return spec.get(zone_key, spec.get("front"))

    def make_zone(label, zone_key, img_pil=None, img_path="", text_lines=None, font=font_name, colour_hex=colour):
        w, h = get_dims(zone_key)
        return {
            "name": label, "w": w, "h": h,
            "img_path": img_path, "img_pil": img_pil,
            "text_lines": text_lines or [],
            "font": font, "colour": colour_hex,
            "remove_bg": remove_bg,
        }

    zones = []

    # ── Parse all zone data from order ────────────────────────────────────────
    # For single-zone form submissions, "text" and "image_path" are generic —
    # assign them only to the zone the user actually selected, not always "front".
    _selected_zone    = order_data.get("zone", "front")
    _generic_text     = order_data.get("text", "") or ""
    _generic_img_path = order_data.get("image_path", "") or ""

    def _zone_text(zone_key):
        """Return generic text only if it belongs to this zone."""
        match = (zone_key == _selected_zone) or (
            zone_key == "pocket" and _selected_zone in ["pocket_left", "pocket_right"])
        return _generic_text if match else ""

    def _zone_img(zone_key):
        """Return generic image path only if it belongs to this zone."""
        match = (zone_key == _selected_zone) or (
            zone_key == "pocket" and _selected_zone in ["pocket_left", "pocket_right"])
        return _generic_img_path if match else ""

    front_text    = parse_texts(order_data.get("front_text", "") or _zone_text("front"))
    back_text     = parse_texts(order_data.get("back_text", "") or _zone_text("back"))
    pocket_text   = parse_texts(order_data.get("pocket_text", "") or _zone_text("pocket"))
    sleeve_text   = parse_texts(order_data.get("sleeve_text", "") or _zone_text("sleeve"))

    front_imgs    = _parse_image_json(order_data.get("front_image_json", ""))
    back_imgs     = _parse_image_json(order_data.get("back_image_json", ""))
    pocket_imgs   = _parse_image_json(order_data.get("pocket_image_json", ""))
    sleeve_imgs   = _parse_image_json(order_data.get("sleeve_image_json", ""))

    # Also check direct image paths (from prototype form upload)
    front_path    = order_data.get("image_path", "") if _selected_zone == "front" else ""
    back_path     = order_data.get("back_image_path", "") or _zone_img("back")
    pocket_path   = order_data.get("pocket_image_path", "") or _zone_img("pocket")

    def download(fname):
        if not fname:
            return None
        return _download_image(fname, log)

    # ── POCKET LEFT + POCKET RIGHT ────────────────────────────────────────────
    # If pocket has 2 images → pocket left and pocket right as separate columns
    if len(pocket_imgs) >= 2:
        log(f"Pocket: 2 images → pocket left + pocket right", "info")
        zones.append(make_zone("pocket left",  "pocket", img_pil=download(pocket_imgs[0])))
        zones.append(make_zone("pocket right", "pocket", img_pil=download(pocket_imgs[1])))
    elif len(pocket_imgs) == 1:
        log(f"Pocket: 1 image → single pocket", "info")
        zones.append(make_zone("pocket", "pocket", img_pil=download(pocket_imgs[0]), text_lines=pocket_text))
    elif pocket_path:
        zones.append(make_zone("pocket", "pocket", img_path=pocket_path, text_lines=pocket_text))
    elif pocket_text:
        zones.append(make_zone("pocket", "pocket", text_lines=pocket_text))

    # ── BACK ──────────────────────────────────────────────────────────────────
    if len(back_imgs) >= 1:
        log(f"Back: image from JSON → {back_imgs[0]}", "info")
        zones.append(make_zone("back", "back", img_pil=download(back_imgs[0]), text_lines=back_text))
    elif back_path:
        zones.append(make_zone("back", "back", img_path=back_path, text_lines=back_text))
    elif back_text:
        zones.append(make_zone("back", "back", text_lines=back_text))

    # ── FRONT ─────────────────────────────────────────────────────────────────
    if len(front_imgs) >= 1:
        log(f"Front: {len(front_imgs)} image(s) from JSON", "info")
        for i, fname in enumerate(front_imgs):
            label = "front" if len(front_imgs) == 1 else f"front {i+1}"
            zones.append(make_zone(label, "front", img_pil=download(fname), text_lines=front_text if i == 0 else []))
    elif front_path:
        zones.append(make_zone("front", "front", img_path=front_path, text_lines=front_text))
    elif front_text:
        zones.append(make_zone("front", "front", text_lines=front_text))

    # ── SLEEVE ────────────────────────────────────────────────────────────────
    if len(sleeve_imgs) >= 1:
        zones.append(make_zone("sleeve", "sleeve", img_pil=download(sleeve_imgs[0]), text_lines=sleeve_text))
    elif sleeve_text:
        zones.append(make_zone("sleeve", "sleeve", text_lines=sleeve_text))

    # ── FALLBACK: prototype form (single zone) ────────────────────────────────
    # Always create all three standard zones: FRONT, BACK, POCKET
    # Only the selected zone will have content, others will be empty placeholders
    if not zones:
        zone_key = order_data.get("zone", "front")
        text_lines = parse_texts(order_data.get("text", "") or "")
        img_path = order_data.get("image_path", "") or ""

        log(f"Single-zone order: creating all zones with content in [{zone_key.upper()}]", "info")

        # Create all three zones in order: POCKET, BACK, FRONT
        # Map pocket_left/pocket_right to just "pocket" for display
        selected_zone = zone_key
        if zone_key in ["pocket_left", "pocket_right"]:
            selected_zone = "pocket"
            display_label = zone_key.replace("_", " ").upper()
        else:
            display_label = zone_key.upper()

        # Add zones in print order: POCKET, BACK, FRONT
        for zone_name in ["pocket", "back", "front"]:
            if zone_name == selected_zone:
                # This is the zone with content
                zones.append(make_zone(display_label, zone_name, img_path=img_path, text_lines=text_lines))
            else:
                # Empty placeholder zone
                zones.append(make_zone(zone_name.upper(), zone_name, img_path="", text_lines=[]))

    return zones


def run_automation(order_id, detail_id, amazon_id, order_data):
    def log(msg, level="info"):
        log_progress(order_id, msg, level)
    try:
        log("Starting automation pipeline...", "info")
        product   = order_data.get("product", "tshirt")
        font_name = _parse_font(order_data.get("font", "Arial"))
        colour    = _parse_colour(order_data.get("colour", "#ffffff"))
        remove_bg = order_data.get("remove_bg", False)
        spec      = PRODUCT_CANVAS.get(product, PRODUCT_CANVAS["tshirt"])

        log(f"Product: {product} | Font: {font_name} | Colour: {colour}", "info")

        # Build zones — handles all order types including pocket left/right
        zones = _build_zones_from_order_data(
            order_data, spec, font_name, colour, remove_bg, log)

        log(f"Zones to render: {[z['name'] for z in zones]}", "info")

        today    = datetime.now().strftime("%Y-%m-%d")
        out_dir  = os.path.join(OUTPUT_FOLDER, today)
        os.makedirs(out_dir, exist_ok=True)
        safe_id  = amazon_id.replace("/", "-")
        out_path = os.path.join(out_dir, f"{safe_id}.psd")

        success, message = build_multizone_psd(
            order_id=order_id, amazon_id=amazon_id,
            zones=zones, out_path=out_path, log_fn=log)

        if not success:
            log(f"PSD failed: {message}", "error")
            flat = out_path.replace(".psd", "_FLAT.png")
            z0 = zones[0]
            _save_flat_png(z0["w"], z0["h"], z0.get("img_path",""),
                           z0["text_lines"], z0["font"], z0["colour"], flat)
            out_path = flat
            log(f"Flat PNG fallback: {flat}", "warning")
        else:
            size_mb = os.path.getsize(out_path) / (1024*1024)
            log(f"PSD saved: {out_path} ({size_mb:.1f} MB)", "success")
            log(f"Zones: {[z['name'] for z in zones]}", "success")
            log(f"Open in Photoshop — each zone has its label above it", "success")

        log("Updating database...", "info")
        mark_order_complete(detail_id, out_path)
        log(f"Order {amazon_id} complete!", "success")
        progress_logs[order_id].append({"done": True, "file": out_path})

    except Exception as e:
        import traceback
        log_progress(order_id, f"Pipeline error: {str(e)}\n{traceback.format_exc()[-400:]}", "error")
        progress_logs[order_id].append({"done": True, "error": str(e)})
        import traceback
        log_progress(order_id, f"Pipeline error: {str(e)}", "error")
        progress_logs[order_id].append({"done": True, "error": str(e)})

def _save_flat_png(w, h, img_path, text_lines, font_name, colour_hex, out_path):
    canvas = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    if img_path and os.path.exists(img_path):
        src   = Image.open(img_path).convert("RGBA")
        ratio = min(w / src.width, h / src.height)
        nw, nh = int(src.width * ratio), int(src.height * ratio)
        src   = src.resize((nw, nh), Image.LANCZOS)
        canvas.paste(src, ((w - nw) // 2, (h - nh) // 2), src)
    if text_lines:
        draw = ImageDraw.Draw(canvas)
        rgb  = hex_to_rgb(colour_hex)
        fo   = get_font(font_name, max(40, h // 12))
        y    = int(h * 0.70)
        for line in text_lines:
            bb = draw.textbbox((0, 0), line, font=fo)
            lw = bb[2] - bb[0]
            draw.text(((w - lw) // 2, y), line, font=fo, fill=rgb + (255,))
            y += int((bb[3] - bb[1]) * 1.25)
    canvas.save(out_path, dpi=(DPI, DPI))


# ─── ROUTES ───────────────────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Varsany Print Automation</title>
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
.btn{background:#7c3aed;color:#fff;border:none;padding:12px 24px;border-radius:8px;
  font-size:14px;font-weight:600;cursor:pointer;width:100%}
.btn:hover{background:#6d28d9}
.log-box{background:#0a0a0a;border:1px solid #222;border-radius:8px;
  height:340px;overflow-y:auto;padding:12px;font-family:monospace;font-size:12px}
.log-info{color:#60a5fa}.log-success{color:#34d399}
.log-warning{color:#fbbf24}.log-error{color:#f87171}
.log-time{color:#555;margin-right:8px}
.progress-bar{height:6px;background:#222;border-radius:3px;margin:12px 0}
.progress-fill{height:100%;background:#7c3aed;border-radius:3px;transition:width 0.3s;width:0%}
.status-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px}
.status-done{background:#064e3b;color:#34d399}
.status-pending{background:#1e1b4b;color:#818cf8}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;color:#666;font-weight:500;border-bottom:1px solid #222}
td{padding:8px 12px;border-bottom:1px solid #1a1a1a;color:#ccc}
tr:hover td{background:#1f1f1f}
.preview-box{background:#111;border:1px dashed #333;border-radius:8px;
  min-height:100px;display:flex;align-items:center;justify-content:center;
  color:#555;font-size:13px;margin-top:8px}
.preview-box img{max-width:100%;max-height:180px;border-radius:6px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.result-file{background:#052e16;border:1px solid #166534;border-radius:8px;
  padding:12px;margin-top:12px;font-size:13px;color:#4ade80;word-break:break-all}
.engine-note{background:#1c1917;border:1px solid #44403c;border-radius:8px;
  padding:10px 14px;margin-bottom:20px;font-size:12px;color:#a8a29e;line-height:1.6}
.engine-note b{color:#a78bfa}
</style>
</head>
<body>
<header>
  <h1>Varsany Print Automation</h1>
  <span>Pure Python PSD Writer</span>
</header>
<div class="container">
  <div class="grid">

    <div class="card">
      <div class="engine-note">
        <b>Engine:</b> Pure Python struct + zlib — zero NumPy, zero external tools.
        ⚠️ <b>LOW-RES MODE</b> (~72 DPI). Raise PX_PER_CM to 320 for production.
        Designers open in Photoshop: CustomerImage + CustomerText layers.
      </div>
      <h2>New Customisation Order</h2>

      <div style="margin-bottom:20px">
        <button type="button" class="btn" onclick="toggleMultiZone()"
          style="background:#1e293b;width:auto;padding:8px 16px;font-size:12px">
          🔀 Switch to Multi-Zone Mode
        </button>
      </div>

      <form id="orderForm" enctype="multipart/form-data" style="display:block">
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
              <option value="pocket_left">Pocket Left</option>
              <option value="pocket_right">Pocket Right</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>Upload Image (optional)</label>
          <input type="file" name="image" accept="image/*" onchange="previewImage(this)">
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
            placeholder="Enter text&#10;Use Enter for new line"></textarea>
        </div>
        <div class="two-col">
          <div class="form-group">
            <label>Font (place .ttf in C:\\Varsany\\Fonts\\)</label>
            <select name="font">
              <option>Arial</option>
              <option>Arial Bold</option>
              <option>Impact</option>
              <option>Times New Roman</option>
              <option>Courier New</option>
              <option>Russo One</option>
              <option>Bebas Neue</option>
              <option>Chewy</option>
            </select>
          </div>
          <div class="form-group">
            <label>Text Colour</label>
            <input type="color" name="colour" value="#ffffff">
          </div>
        </div>
        <div class="form-group">
          <label>SKU</label>
          <input type="text" name="sku" value="MenTee_BlkM">
        </div>
        <button type="submit" class="btn" id="submitBtn">
          Submit Order &amp; Generate Layered PSD
        </button>
      </form>

      <!-- Multi-Zone Form -->
      <form id="multiZoneForm" enctype="multipart/form-data" style="display:none">
        <div class="form-group">
          <label>Product Type</label>
          <select name="product" id="productSelectMulti">
            <option value="hoodie">Hoodie</option>
            <option value="tshirt">T-Shirt</option>
          </select>
        </div>

        <h3 style="color:#a78bfa;font-size:14px;margin:20px 0 10px">FRONT Zone</h3>
        <div class="form-group">
          <label>Front Image</label>
          <input type="file" name="front_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Front Text</label>
          <textarea name="front_text" rows="2" placeholder="Front text (optional)"></textarea>
        </div>

        <h3 style="color:#34d399;font-size:14px;margin:20px 0 10px">BACK Zone</h3>
        <div class="form-group">
          <label>Back Image</label>
          <input type="file" name="back_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Back Text</label>
          <textarea name="back_text" rows="2" placeholder="Back text (optional)"></textarea>
        </div>

        <h3 style="color:#fbbf24;font-size:14px;margin:20px 0 10px">POCKET Zone</h3>
        <div class="form-group">
          <label>Pocket Image</label>
          <input type="file" name="pocket_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Pocket Text</label>
          <textarea name="pocket_text" rows="2" placeholder="Pocket text (optional)"></textarea>
        </div>

        <h3 style="color:#fbbf24;font-size:14px;margin:20px 0 10px">POCKET LEFT Zone</h3>
        <div class="form-group">
          <label>Pocket Left Image</label>
          <input type="file" name="pocket_left_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Pocket Left Text</label>
          <textarea name="pocket_left_text" rows="2" placeholder="Pocket left text (optional)"></textarea>
        </div>

        <h3 style="color:#fbbf24;font-size:14px;margin:20px 0 10px">POCKET RIGHT Zone</h3>
        <div class="form-group">
          <label>Pocket Right Image</label>
          <input type="file" name="pocket_right_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Pocket Right Text</label>
          <textarea name="pocket_right_text" rows="2" placeholder="Pocket right text (optional)"></textarea>
        </div>

        <h3 style="color:#f472b6;font-size:14px;margin:20px 0 10px">SLEEVE Zone</h3>
        <div class="form-group">
          <label>Sleeve Image</label>
          <input type="file" name="sleeve_image" accept="image/*">
        </div>
        <div class="form-group">
          <label>Sleeve Text</label>
          <textarea name="sleeve_text" rows="2" placeholder="Sleeve text (optional)"></textarea>
        </div>

        <div class="two-col">
          <div class="form-group">
            <label>Font</label>
            <select name="font">
              <option>Arial Bold</option>
              <option>Arial</option>
              <option>Impact</option>
            </select>
          </div>
          <div class="form-group">
            <label>Text Colour</label>
            <input type="color" name="colour" value="#ffffff">
          </div>
        </div>

        <button type="submit" class="btn" id="submitMultiBtn">
          Generate Multi-Zone PSD (All Zones in One File)
        </button>
      </form>

    </div>

    <div class="card">
      <h2>Automation Progress</h2>
      <div id="statusArea" style="color:#555;font-size:13px;text-align:center;padding:40px 0">
        Submit an order to see live progress
      </div>
      <div id="progressArea" style="display:none">
        <div style="font-size:13px;color:#999;margin-bottom:8px">
          Order: <span id="currentOrderId" style="color:#a78bfa"></span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill"></div>
        </div>
        <div class="log-box" id="logBox"></div>
        <div id="resultBox"></div>
      </div>
    </div>

  </div>

  <div class="card" style="margin-top:24px">
    <h2>Order History</h2>
    <table>
      <thead>
        <tr><th>Order ID</th><th>Product</th><th>Zone</th>
            <th>Text</th><th>Status</th><th>Output File</th></tr>
      </thead>
      <tbody>
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
          <td style="font-size:11px;color:#555">{{(o.AdditionalPSD or '')[-50:]}}</td>
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
function toggleMultiZone() {
  const single = document.getElementById('orderForm');
  const multi = document.getElementById('multiZoneForm');
  const btn = event.target;

  if (single.style.display === 'none') {
    single.style.display = 'block';
    multi.style.display = 'none';
    btn.textContent = '🔀 Switch to Multi-Zone Mode';
  } else {
    single.style.display = 'none';
    multi.style.display = 'block';
    btn.textContent = '🔙 Switch to Single-Zone Mode';
  }
}

function previewImage(input) {
  const box = document.getElementById('imgPreview');
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = e => { box.innerHTML = '<img src="'+e.target.result+'">'; };
    reader.readAsDataURL(input.files[0]);
  }
}
function updateZones() {
  const zones = {
    hoodie:['front','back','sleeve','pocket','pocket_left','pocket_right'],
    tshirt:['front','back','sleeve','pocket','pocket_left','pocket_right'],
    kidstshirt:['front','back'],
    totebag:['front','back'],
    slipper:['front'],
    babyvest:['front']
  };
  const z = zones[document.getElementById('productSelect').value] || ['front'];
  document.getElementById('zoneSelect').innerHTML = z.map(v =>
    `<option value="${v}">${v.charAt(0).toUpperCase()+v.slice(1)}</option>`).join('');
}
let pollInterval = null;
document.getElementById('orderForm').onsubmit = async function(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Generating PSD...';
  const fd  = new FormData(this);
  const res = await fetch('/submit', {method:'POST', body:fd});
  const data = await res.json();
  if (data.error) {
    alert('Error: ' + data.error);
    btn.disabled = false;
    btn.textContent = 'Submit Order & Generate Layered PSD';
    return;
  }
  const orderId = data.order_id;
  document.getElementById('currentOrderId').textContent = orderId;
  document.getElementById('statusArea').style.display   = 'none';
  document.getElementById('progressArea').style.display = 'block';
  document.getElementById('logBox').innerHTML   = '';
  document.getElementById('resultBox').innerHTML = '';
  document.getElementById('progressFill').style.width = '5%';
  let progress = 5, seen = 0;
  pollInterval = setInterval(async () => {
    const r    = await fetch('/progress/' + orderId);
    const logs = await r.json();
    const box  = document.getElementById('logBox');
    for (let i = seen; i < logs.length; i++) {
      const l = logs[i];
      if (l.done !== undefined) {
        clearInterval(pollInterval);
        document.getElementById('progressFill').style.width = '100%';
        btn.disabled = false;
        btn.textContent = 'Submit Order & Generate Layered PSD';
        if (l.file) {
          document.getElementById('resultBox').innerHTML =
            '<div class="result-file">Layered PSD saved:<br>' + l.file + '</div>';
        }
        if (l.error) {
          document.getElementById('resultBox').innerHTML =
            '<div style="background:#450a0a;border:1px solid #7f1d1d;border-radius:8px;'
            +'padding:12px;margin-top:12px;font-size:13px;color:#f87171">Error: '+l.error+'</div>';
        }
        setTimeout(() => location.reload(), 4000);
        break;
      }
      const cls = 'log-'+(l.level||'info');
      box.innerHTML += `<div><span class="log-time">${l.time}</span>`
                     + `<span class="${cls}">${l.message}</span></div>`;
      seen = i + 1;
    }
    box.scrollTop = box.scrollHeight;
    progress = Math.min(progress + 7, 90);
    document.getElementById('progressFill').style.width = progress + '%';
  }, 800);
};

// Multi-Zone Form Handler
document.getElementById('multiZoneForm').onsubmit = async function(e) {
  e.preventDefault();
  const btn = document.getElementById('submitMultiBtn');
  btn.disabled = true;
  btn.textContent = 'Generating Multi-Zone PSD...';

  const fd = new FormData(this);
  const res = await fetch('/submit-multizone', {method:'POST', body:fd});
  const data = await res.json();

  if (data.error) {
    alert('Error: ' + data.error);
    btn.disabled = false;
    btn.textContent = 'Generate Multi-Zone PSD (All Zones in One File)';
    return;
  }

  const orderId = data.order_id;
  document.getElementById('currentOrderId').textContent = orderId;
  document.getElementById('statusArea').style.display = 'none';
  document.getElementById('progressArea').style.display = 'block';
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('resultBox').innerHTML = '';
  document.getElementById('progressFill').style.width = '5%';

  let progress = 5, seen = 0;
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
        btn.textContent = 'Generate Multi-Zone PSD (All Zones in One File)';
        if (l.file) {
          document.getElementById('resultBox').innerHTML =
            '<div class="result-file">Multi-Zone PSD saved:<br>' + l.file + '</div>';
        }
        if (l.error) {
          document.getElementById('resultBox').innerHTML =
            '<div style="background:#450a0a;border:1px solid #7f1d1d;border-radius:8px;'
            +'padding:12px;margin-top:12px;font-size:13px;color:#f87171">Error: '+l.error+'</div>';
        }
        setTimeout(() => location.reload(), 4000);
        break;
      }
      const cls = 'log-'+(l.level||'info');
      box.innerHTML += `<div><span class="log-time">${l.time}</span>`
                     + `<span class="${cls}">${l.message}</span></div>`;
      seen = i + 1;
    }
    box.scrollTop = box.scrollHeight;
    progress = Math.min(progress + 7, 90);
    document.getElementById('progressFill').style.width = progress + '%';
  }, 800);
};
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML, orders=get_recent_orders())


@app.route("/submit", methods=["POST"])
def submit():
    try:
        image_path = ""
        if "image" in request.files:
            f = request.files["image"]
            if f and f.filename:
                ext        = os.path.splitext(f.filename)[1]
                image_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}{ext}")
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

        order_id, detail_id, amazon_id = save_order_to_db(order_data)

        threading.Thread(
            target=run_automation,
            args=(order_id, detail_id, amazon_id, order_data),
            daemon=True
        ).start()

        return jsonify({"order_id": order_id, "amazon_id": amazon_id})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/submit-multizone", methods=["POST"])
def submit_multizone():
    """Handle multi-zone form submission - creates one PSD with all zones side-by-side"""
    try:
        order_id = str(uuid.uuid4())
        amazon_id = f"MULTI-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        product = request.form.get("product", "hoodie")
        font_name = request.form.get("font", "Arial Bold")
        colour_hex = request.form.get("colour", "#ffffff")

        def log(msg, level="info"):
            log_progress(order_id, msg, level)

        log(f"Multi-zone order received: {product}", "info")

        # Save uploaded images and build zones
        spec = PRODUCT_CANVAS.get(product, PRODUCT_CANVAS["tshirt"])
        zones = []

        # Order zones left to right: pocket_left, pocket_right, pocket, back, front, sleeve
        zone_names = ["pocket_left", "pocket_right", "pocket", "back", "front", "sleeve"]
        for zone_name in zone_names:
            # Check if this zone has content
            img_file = request.files.get(f"{zone_name}_image")
            text = request.form.get(f"{zone_name}_text", "").strip()

            if not img_file or not img_file.filename:
                img_file = None
            if not text:
                text = None

            # Skip zones with no content
            if not img_file and not text:
                continue

            # Save image if uploaded
            img_path = ""
            if img_file and img_file.filename:
                ext = os.path.splitext(img_file.filename)[1]
                img_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}{ext}")
                img_file.save(img_path)
                log(f"Uploaded {zone_name} image: {os.path.basename(img_path)}", "info")

            # Get zone dimensions - map pocket_left/pocket_right to pocket
            zone_key = "pocket" if zone_name in ["pocket_left", "pocket_right"] else zone_name
            zone_dims = spec.get(zone_key, spec.get("front"))

            # Display name with proper formatting
            display_name = zone_name.replace("_", " ").upper()

            zones.append({
                "name": display_name,
                "w": zone_dims[0],
                "h": zone_dims[1],
                "img_path": img_path,
                "img_pil": None,
                "text_lines": parse_texts(text) if text else [],
                "font": font_name,
                "colour": colour_hex,
                "remove_bg": False,
            })

        if not zones:
            return jsonify({"error": "Please add at least one zone (image or text)"})

        log(f"Building PSD with {len(zones)} zones: {[z['name'] for z in zones]}", "info")

        # Build the multi-zone PSD in background thread
        def build_async():
            today = datetime.now().strftime("%Y-%m-%d")
            out_dir = os.path.join(OUTPUT_FOLDER, today)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{amazon_id}.psd")

            success, message = build_multizone_psd(
                order_id=order_id,
                amazon_id=amazon_id,
                zones=zones,
                out_path=out_path,
                log_fn=log
            )

            if success:
                size_mb = os.path.getsize(out_path) / (1024*1024)
                log(f"Multi-zone PSD complete: {size_mb:.1f} MB", "success")
                progress_logs[order_id].append({"done": True, "file": out_path})
            else:
                log(f"Build failed: {message}", "error")
                progress_logs[order_id].append({"done": True, "error": message})

        threading.Thread(target=build_async, daemon=True).start()

        return jsonify({"order_id": order_id, "amazon_id": amazon_id})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()})


@app.route("/progress/<order_id>")
def progress(order_id):
    return jsonify(progress_logs.get(order_id, []))


@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename)


@app.route("/demo-multizone")
def demo_multizone():
    """
    Demo endpoint for presentation: Creates a multi-zone PSD with
    front + back + pocket + sleeve all side-by-side in one file.
    """
    try:
        order_id = str(uuid.uuid4())
        amazon_id = f"DEMO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        def log(msg, level="info"):
            log_progress(order_id, msg, level)

        log("Creating multi-zone demo PSD...", "info")

        # Define demo zones with sample data
        product = "hoodie"
        spec = PRODUCT_CANVAS.get(product, PRODUCT_CANVAS["tshirt"])

        zones = [
            {
                "name": "front",
                "w": spec["front"][0],
                "h": spec["front"][1],
                "img_path": "",  # No image
                "img_pil": None,
                "text_lines": ["FRONT", "ZONE"],
                "font": "Arial Bold",
                "colour": "#ffffff",
                "remove_bg": False,
            },
            {
                "name": "back",
                "w": spec["back"][0],
                "h": spec["back"][1],
                "img_path": "",
                "img_pil": None,
                "text_lines": ["BACK", "ZONE"],
                "font": "Arial Bold",
                "colour": "#00ff00",
                "remove_bg": False,
            },
            {
                "name": "pocket",
                "w": spec["pocket"][0],
                "h": spec["pocket"][1],
                "img_path": "",
                "img_pil": None,
                "text_lines": ["POCKET"],
                "font": "Arial Bold",
                "colour": "#ffff00",
                "remove_bg": False,
            },
            {
                "name": "sleeve",
                "w": spec["sleeve"][0],
                "h": spec["sleeve"][1],
                "img_path": "",
                "img_pil": None,
                "text_lines": ["SLEEVE"],
                "font": "Arial Bold",
                "colour": "#ff00ff",
                "remove_bg": False,
            },
        ]

        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = os.path.join(OUTPUT_FOLDER, today)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{amazon_id}_MULTIZONE_DEMO.psd")

        success, message = build_multizone_psd(
            order_id=order_id,
            amazon_id=amazon_id,
            zones=zones,
            out_path=out_path,
            log_fn=log
        )

        if success:
            size_mb = os.path.getsize(out_path) / (1024*1024)
            log(f"Demo PSD created: {size_mb:.1f} MB", "success")
            progress_logs[order_id].append({"done": True, "file": out_path})
            return jsonify({
                "success": True,
                "file": out_path,
                "size_mb": round(size_mb, 1),
                "zones": [z["name"] for z in zones],
                "message": "Multi-zone demo PSD created successfully!"
            })
        else:
            log(f"Demo failed: {message}", "error")
            return jsonify({"success": False, "error": message})

    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


if __name__ == "__main__":
    print(f"\n{'='*55}")
    print(f"  Varsany Print Automation — Pure Python PSD Writer")
    print(f"  Engine: struct + zlib (zero NumPy)")
    print(f"  Resolution: LOW-RES MODE ({PX_PER_CM} px/cm / {DPI} DPI)")
    print(f"  Expected file size: 1-5 MB per order (raise PX_PER_CM=320 for prod)")
    print(f"  Open: http://localhost:5000")
    print(f"{'='*55}\n")
    app.run(debug=True, port=5000, threaded=True)

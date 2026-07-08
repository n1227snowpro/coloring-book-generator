"""Assemble generated line-art designs into a KDP-ready interior PDF.

Layout matches the reference book: each design sits framed on its own page,
followed by a blank page (single-sided coloring format, no bleed-through).
No title, "belongs to", copyright, or bonus pages are added.
"""

from PIL import Image, ImageDraw

# Standard KDP trim sizes in inches (width, height).
PAGE_SIZES = {
    "8.25x11": (8.25, 11.0),
    "8.5x11": (8.5, 11.0),
    "6x9": (6.0, 9.0),
    "5.5x8.5": (5.5, 8.5),
}

DPI = 300
OUTSIDE_MARGIN_IN = 0.5  # top / bottom / outside edge — exceeds KDP's 0.25" minimum
BORDER_WIDTH_PX = 3

# KDP's required inside (gutter) margin grows with total page count, since more
# pages means more of the inner margin disappears into the binding.
GUTTER_TABLE_IN = [
    (150, 0.375),
    (300, 0.5),
    (500, 0.625),
    (700, 0.75),
    (828, 0.875),
]


def gutter_margin_in(total_pages):
    for max_pages, margin in GUTTER_TABLE_IN:
        if total_pages <= max_pages:
            return margin
    return GUTTER_TABLE_IN[-1][1]


# Aspect ratios the Gemini image API accepts.
SUPPORTED_ASPECT_RATIOS = ["1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]


def closest_aspect_ratio(page_size_key):
    """Pick the supported image aspect ratio closest to the page's own ratio,
    so generated art fills the page instead of leaving big blank margins."""
    w_in, h_in = PAGE_SIZES.get(page_size_key, PAGE_SIZES["8.5x11"])
    target = w_in / h_in
    best = min(
        SUPPORTED_ASPECT_RATIOS,
        key=lambda ar: abs((int(ar.split(":")[0]) / int(ar.split(":")[1])) - target),
    )
    return best


def build_interior_pdf(design_images, page_size_key, output_path):
    """design_images: list of PIL.Image (one per unique design).
    Writes a PDF with [design, blank] pairs for every design, no front/back matter.
    """
    w_in, h_in = PAGE_SIZES.get(page_size_key, PAGE_SIZES["8.5x11"])
    page_w = round(w_in * DPI)
    page_h = round(h_in * DPI)

    outside_px = round(OUTSIDE_MARGIN_IN * DPI)
    total_pages = len(design_images) * 2
    # The gutter must be >= the outside margin (KDP requirement); our outside
    # margin is set above KDP's own 0.25" floor, so re-max against the table.
    gutter_px = round(max(gutter_margin_in(total_pages), OUTSIDE_MARGIN_IN) * DPI)

    # Every design lands on an odd (recto/right-hand) page, so the gutter —
    # the larger margin that disappears into the binding — is always on the
    # left edge; top, right, and bottom stay at the outside minimum.
    margins_px = (gutter_px, outside_px, outside_px, outside_px)  # left, top, right, bottom

    pages = []
    for design in design_images:
        pages.append(_build_design_page(design, page_w, page_h, margins_px))
        pages.append(Image.new("RGB", (page_w, page_h), "white"))

    if not pages:
        raise ValueError("No design images to assemble")

    first, rest = pages[0], pages[1:]
    first.save(output_path, format="PDF", save_all=True, append_images=rest, resolution=DPI)


def _build_design_page(design, page_w, page_h, margins_px):
    left_px, top_px, right_px, bottom_px = margins_px
    page = Image.new("RGB", (page_w, page_h), "white")

    if design.mode == "RGBA":
        background = Image.new("RGB", design.size, "white")
        background.paste(design, mask=design.split()[3])
        design = background
    else:
        design = design.convert("RGB")

    inner_w = page_w - left_px - right_px
    inner_h = page_h - top_px - bottom_px
    scale = min(inner_w / design.width, inner_h / design.height)
    new_w = max(1, round(design.width * scale))
    new_h = max(1, round(design.height * scale))
    resized = design.resize((new_w, new_h), Image.LANCZOS)

    offset_x = left_px + (inner_w - new_w) // 2
    offset_y = top_px + (inner_h - new_h) // 2
    page.paste(resized, (offset_x, offset_y))

    draw = ImageDraw.Draw(page)
    draw.rectangle(
        [left_px, top_px, page_w - right_px, page_h - bottom_px],
        outline="black",
        width=BORDER_WIDTH_PX,
    )
    return page

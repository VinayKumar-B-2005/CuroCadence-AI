from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM

def convert_svg_to_png(svg_path, png_path):
    print(f"Converting {svg_path} to {png_path}...")
    drawing = svg2rlg(svg_path)
    if drawing is None:
        print(f"Failed to load {svg_path}")
        return
    renderPM.drawToFile(drawing, png_path, fmt="PNG")
    print(f"Successfully converted {svg_path} to {png_path}")

convert_svg_to_png('assets/architecture_diagram.svg', 'assets/architecture_diagram.png')
convert_svg_to_png('assets/cover_page_banner.svg', 'assets/cover_page_banner.png')
